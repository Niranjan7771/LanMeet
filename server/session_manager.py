from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Dict, Optional, Set, Tuple

from shared.protocol import ChatMessage, ControlAction, encode_control_message

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT = 30.0  # seconds


@dataclass(slots=True)
class ConnectedClient:
    username: str
    writer: asyncio.StreamWriter
    last_seen: float = field(default_factory=lambda: time.monotonic())
    connected_at: float = field(default_factory=lambda: time.time())
    is_presenter: bool = False
    connection_type: str = "tcp"
    peer_ip: Optional[str] = None
    peer_port: Optional[int] = None
    bytes_sent: int = 0
    bytes_received: int = 0
    audio_enabled: bool = False
    video_enabled: bool = False
    is_typing: bool = False
    last_typing_at: float = field(default_factory=lambda: 0.0)
    hand_raised: bool = False
    latency_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    last_latency_update: float = field(default_factory=lambda: 0.0)

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    def send(self, action: ControlAction, data: Dict[str, object]) -> None:
        payload = encode_control_message(action, data)
        self.bytes_sent += len(payload)
        self.writer.write(payload)


class SessionManager:
    """Coordinates connected clients and manages broadcasts."""

    def __init__(self) -> None:
        self._clients: Dict[str, ConnectedClient] = {}
        self._lock = asyncio.Lock()
        self._presenter: Optional[str] = None
        self._chat_history: list[ChatMessage] = []
        self._event_log: list[dict] = []
        self._banned_usernames: Set[str] = set()
        self._presence_cache: Dict[str, dict[str, object]] = {}
        self._session_started_at: float = time.time()
        self._time_limit_started_at: Optional[float] = None
        self._time_limit_duration_seconds: Optional[float] = None
        self._time_limit_end_timestamp: Optional[float] = None
        self._shutdown_requested: bool = False
        self._shutdown_reason: Optional[str] = None
        self._shutdown_requested_at: Optional[float] = None

    async def register(self, username: str, writer: asyncio.StreamWriter, peername: Optional[Tuple[str, ...]] = None) -> ConnectedClient:
        async with self._lock:
            if username in self._clients:
                raise ValueError(f"Username '{username}' already connected")
            if username in self._banned_usernames:
                raise PermissionError(f"Username '{username}' is not allowed to join")
            client = ConnectedClient(username=username, writer=writer)
            if peername:
                client.peer_ip = peername[0]
                if len(peername) > 1:
                    try:
                        client.peer_port = int(peername[1])
                    except (TypeError, ValueError):
                        client.peer_port = None
            if not self._clients:
                self._session_started_at = time.time()
            self._clients[username] = client
            logger.info("Registered client %s", username)
            self._record_event(
                "user_joined",
                {
                    "username": username,
                },
            )
            self._presence_cache[username] = self._client_presence_payload(client)
            return client

    async def unregister(
        self,
        username: str,
        *,
        event_type: str = "user_left",
        details: Optional[Dict[str, object]] = None,
    ) -> bool:
        async with self._lock:
            client = self._clients.pop(username, None)
            if client is None:
                return False
            if self._presenter == username:
                self._presenter = None
            self._presence_cache.pop(username, None)
            try:
                client.writer.close()
            except Exception:  # pragma: no cover - cleanup best effort
                logger.exception("Error while closing writer for %s", username)
            logger.info("Unregistered client %s", username)
            event_details: Dict[str, object] = {"username": username}
            if details:
                event_details.update(details)
            self._record_event(event_type, event_details)
            return True
    async def update_media_state(
        self,
        username: str,
        *,
        audio_enabled: Optional[bool] = None,
        video_enabled: Optional[bool] = None,
    ) -> Optional[dict[str, object]]:
        async with self._lock:
            client = self._clients.get(username)
            if client is None:
                return None
            if audio_enabled is not None:
                client.audio_enabled = audio_enabled
            if video_enabled is not None:
                client.video_enabled = video_enabled
            self._presence_cache[username] = self._client_presence_payload(client)
            return {
                "username": username,
                "audio_enabled": client.audio_enabled,
                "video_enabled": client.video_enabled,
            }

    async def get_media_state_snapshot(self) -> dict[str, dict[str, bool]]:
        async with self._lock:
            snapshot: dict[str, dict[str, bool]] = {}
            for username, client in self._clients.items():
                snapshot[username] = {
                    "audio_enabled": client.audio_enabled,
                    "video_enabled": client.video_enabled,
                }
                self._presence_cache[username] = self._client_presence_payload(client)
            return snapshot

    async def grant_presenter(self, username: str) -> bool:
        async with self._lock:
            if username not in self._clients:
                return False
            if self._presenter == username:
                return True
            if self._presenter is not None and self._presenter != username:
                return False
            self._presenter = username
            self._clients[username].is_presenter = True
            self._record_event(
                "presenter_granted",
                {
                    "username": username,
                },
            )
            return True

    async def revoke_presenter(self, username: str) -> None:
        async with self._lock:
            if self._presenter == username:
                self._presenter = None
            client = self._clients.get(username)
            if client:
                client.is_presenter = False
            self._record_event(
                "presenter_revoked",
                {
                    "username": username,
                },
            )

    async def get_presenter(self) -> Optional[str]:
        async with self._lock:
            return self._presenter

    async def is_presenter(self, username: str) -> bool:
        async with self._lock:
            return self._presenter == username

    async def get_client(self, username: str) -> Optional[ConnectedClient]:
        async with self._lock:
            return self._clients.get(username)

    async def record_received(self, username: str, num_bytes: int) -> None:
        if num_bytes <= 0:
            return
        async with self._lock:
            client = self._clients.get(username)
            if client:
                client.bytes_received += num_bytes

    async def broadcast(self, action: ControlAction, data: Dict[str, object], *, exclude: Optional[Set[str]] = None) -> None:
        if exclude is None:
            exclude = set()
        drains: list[Awaitable[None]] = []
        async with self._lock:
            for username, client in self._clients.items():
                if username in exclude:
                    continue
                try:
                    client.send(action, data)
                    drains.append(client.writer.drain())
                except Exception:
                    logger.exception("Failed to queue message to %s", username)
        if drains:
            await asyncio.gather(*drains, return_exceptions=True)

    async def send_to(self, username: str, action: ControlAction, data: Dict[str, object]) -> None:
        drain: Optional[Awaitable[None]] = None
        async with self._lock:
            client = self._clients.get(username)
            if client is None:
                return
            try:
                client.send(action, data)
                drain = client.writer.drain()
            except Exception:
                logger.exception("Failed to send direct message to %s", username)
        if drain is not None:
            await asyncio.gather(drain, return_exceptions=True)

    async def add_chat_message(self, chat: ChatMessage) -> None:
        async with self._lock:
            self._chat_history.append(chat)
            if len(self._chat_history) > 200:
                self._chat_history.pop(0)
            self._record_event(
                "chat_message",
                {
                    "sender": chat.sender,
                    "message": chat.message,
                },
            )

    async def get_chat_history(self) -> list[ChatMessage]:
        async with self._lock:
            return list(self._chat_history)

    async def get_presence_entry(self, username: str) -> Optional[dict[str, object]]:
        async with self._lock:
            client = self._clients.get(username)
            if client is None:
                return None
            payload = self._client_presence_payload(client)
            self._presence_cache[username] = payload
            return payload

    async def list_clients(self) -> list[str]:
        async with self._lock:
            return list(self._clients.keys())

    async def snapshot(self) -> dict:
        async with self._lock:
            now_monotonic = time.monotonic()
            clients: list[dict[str, object]] = []
            usernames: list[str] = []
            for client in self._clients.values():
                clients.append(
                    {
                        "username": client.username,
                        "last_seen_seconds": max(0.0, now_monotonic - client.last_seen),
                        "connected_at": client.connected_at,
                        "is_presenter": client.is_presenter,
                        "connection_type": client.connection_type,
                        "peer_ip": client.peer_ip,
                        "peer_port": client.peer_port,
                        "bytes_sent": client.bytes_sent,
                        "bytes_received": client.bytes_received,
                        "throughput_bps": _calculate_rate(client.bytes_received, client.connected_at),
                        "bandwidth_bps": _calculate_rate(client.bytes_sent, client.connected_at),
                        "audio_enabled": client.audio_enabled,
                        "video_enabled": client.video_enabled,
                        "hand_raised": client.hand_raised,
                        "is_typing": client.is_typing,
                        "latency_ms": client.latency_ms,
                        "jitter_ms": client.jitter_ms,
                    }
                )
                usernames.append(client.username)
            chat_history = [msg.to_dict() for msg in self._chat_history]
            events = list(self._event_log[-300:])
            return {
                "clients": clients,
                "presenter": self._presenter,
                "chat_history": chat_history,
                "events": events,
                "participant_usernames": usernames,
                "participant_count": len(usernames),
                "banned_usernames": sorted(self._banned_usernames),
                "latency_summary": self._latency_summary_locked(),
                "time_limit": self._build_time_limit_status_locked(now=time.time()),
                "session_started_at": self._session_started_at,
                "shutdown_requested": self._shutdown_requested,
                "shutdown_reason": self._shutdown_reason,
                "shutdown_requested_at": self._shutdown_requested_at,
            }

    async def mark_shutdown_requested(self, *, reason: str) -> None:
        timestamp = time.time()
        async with self._lock:
            if self._shutdown_requested:
                return
            self._shutdown_requested = True
            self._shutdown_reason = reason
            self._shutdown_requested_at = timestamp
            self._record_event(
                "shutdown_requested",
                {
                    "reason": reason,
                    "timestamp": timestamp,
                },
            )

    async def get_presence_snapshot(self) -> list[dict[str, object]]:
        async with self._lock:
            snapshot: list[dict[str, object]] = []
            for client in self._clients.values():
                payload = self._client_presence_payload(client)
                self._presence_cache[client.username] = payload
                snapshot.append(payload)
            return snapshot

    async def set_typing(self, username: str, is_typing: bool) -> Optional[dict[str, object]]:
        async with self._lock:
            client = self._clients.get(username)
            if client is None:
                return None
            client.is_typing = is_typing
            client.last_typing_at = time.time()
            self._presence_cache[username] = self._client_presence_payload(client)
            return {
                "username": username,
                "is_typing": is_typing,
                "timestamp_ms": int(client.last_typing_at * 1000),
            }

    async def set_hand_status(self, username: str, *, raised: bool) -> Optional[dict[str, object]]:
        async with self._lock:
            client = self._clients.get(username)
            if client is None:
                return None
            if client.hand_raised == raised:
                self._presence_cache[username] = self._client_presence_payload(client)
                return {
                    "username": username,
                    "hand_raised": client.hand_raised,
                }
            client.hand_raised = raised
            event_type = "hand_raised" if raised else "hand_lowered"
            self._record_event(
                event_type,
                {
                    "username": username,
                },
            )
            self._presence_cache[username] = self._client_presence_payload(client)
            return {
                "username": username,
                "hand_raised": raised,
            }

    async def update_latency(self, username: str, *, latency_ms: float, jitter_ms: Optional[float] = None) -> Optional[dict[str, object]]:
        async with self._lock:
            client = self._clients.get(username)
            if client is None:
                return None
            client.latency_ms = latency_ms
            client.jitter_ms = jitter_ms
            client.last_latency_update = time.time()
            self._presence_cache[username] = self._client_presence_payload(client)
            return {
                "username": username,
                "latency_ms": latency_ms,
                "jitter_ms": jitter_ms,
                "timestamp_ms": int(client.last_latency_update * 1000),
            }

    async def heartbeat_watcher(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_TIMEOUT)
            stale: list[str] = []
            async with self._lock:
                now = time.monotonic()
                for username, client in list(self._clients.items()):
                    if now - client.last_seen > HEARTBEAT_TIMEOUT * 2:
                        stale.append(username)
                for username in stale:
                    logger.warning("Client %s timed out", username)
                    self._clients.pop(username, None)
            for username in stale:
                participants = await self.list_clients()
                await self.broadcast(
                    ControlAction.USER_LEFT,
                    {"username": username, "participants": participants},
                )

    async def mark_heartbeat(self, username: str) -> None:
        async with self._lock:
            client = self._clients.get(username)
            if client:
                elapsed = time.monotonic() - client.last_seen
                client.touch()
                self._presence_cache[username] = self._client_presence_payload(client)
                logger.debug("Heartbeat received from %s (%.2fs since last)", username, elapsed)

    async def ban_user(self, username: str) -> None:
        async with self._lock:
            self._banned_usernames.add(username)

    async def unban_user(self, username: str) -> None:
        async with self._lock:
            self._banned_usernames.discard(username)

    async def disconnect_all(self, *, reason: str = "Server shutting down") -> None:
        """Forcefully disconnect every connected client with a shutdown reason."""

        drains: list[Awaitable[None]] = []
        waiters: list[Awaitable[None]] = []
        async with self._lock:
            if not self._clients:
                return
            clients = list(self._clients.values())
            for client in clients:
                try:
                    client.send(
                        ControlAction.KICKED,
                        {
                            "reason": reason,
                            "actor": "system",
                        },
                    )
                    drains.append(client.writer.drain())
                except Exception:
                    logger.exception("Failed to notify %s about shutdown", client.username)
                try:
                    client.writer.close()
                    waiters.append(client.writer.wait_closed())
                except Exception:
                    logger.exception("Error while closing writer for %s during shutdown", client.username)
            disconnected = len(clients)
            self._clients.clear()
            self._presence_cache.clear()
            self._presenter = None
            self._record_event(
                "server_shutdown",
                {
                    "reason": reason,
                    "disconnected": disconnected,
                },
            )
        pending = drains + waiters
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def is_banned(self, username: str) -> bool:
        async with self._lock:
            return username in self._banned_usernames

    async def list_banned(self) -> list[str]:
        async with self._lock:
            return list(self._banned_usernames)

    async def record_blocked_attempt(self, username: str) -> None:
        async with self._lock:
            self._record_event(
                "user_blocked",
                {
                    "username": username,
                },
            )

    async def get_recent_events(self, limit: int = 300) -> list[dict[str, object]]:
        async with self._lock:
            if limit <= 0:
                return []
            return list(self._event_log[-limit:])

    async def set_time_limit(
        self,
        *,
        duration_minutes: Optional[float],
        start_timestamp: Optional[float] = None,
        actor: str = "admin",
    ) -> dict[str, object]:
        now = time.time()
        async with self._lock:
            if duration_minutes is None or duration_minutes <= 0:
                was_active = self._time_limit_duration_seconds is not None
                self._time_limit_duration_seconds = None
                self._time_limit_end_timestamp = None
                self._time_limit_started_at = None
                if was_active:
                    self._record_event(
                        "time_limit_cleared",
                        {
                            "actor": actor,
                        },
                    )
            else:
                duration_seconds = max(60.0, float(duration_minutes) * 60.0)
                start_time = start_timestamp if start_timestamp is not None else self._time_limit_started_at or now
                self._time_limit_started_at = start_time
                self._time_limit_duration_seconds = duration_seconds
                self._time_limit_end_timestamp = start_time + duration_seconds
                self._record_event(
                    "time_limit_set",
                    {
                        "actor": actor,
                        "duration_minutes": round(duration_seconds / 60.0, 2),
                    },
                )
            status = self._build_time_limit_status_locked(now=now)
        return status

    async def get_time_limit_status(self) -> dict[str, object]:
        async with self._lock:
            return self._build_time_limit_status_locked()

    async def record_admin_notice(self, message: str, *, level: str = "info", actor: str = "admin") -> dict[str, object]:
        level_normalized = level.lower()
        timestamp = time.time()
        async with self._lock:
            self._record_event(
                "admin_notice",
                {
                    "message": message,
                    "level": level_normalized,
                    "actor": actor,
                },
            )
        return {
            "message": message,
            "level": level_normalized,
            "actor": actor,
            "timestamp": timestamp,
        }

    def _record_event(self, event_type: str, details: Dict[str, object]) -> None:
        event = {
            "type": event_type,
            "timestamp": time.time(),
            "details": details,
        }
        self._event_log.append(event)
        if len(self._event_log) > 1000:
            self._event_log.pop(0)

    def _client_presence_payload(self, client: ConnectedClient) -> dict[str, object]:
        return {
            "username": client.username,
            "is_presenter": client.is_presenter,
            "audio_enabled": client.audio_enabled,
            "video_enabled": client.video_enabled,
            "hand_raised": client.hand_raised,
            "is_typing": client.is_typing,
            "latency_ms": client.latency_ms,
            "jitter_ms": client.jitter_ms,
            "last_seen_seconds": max(0.0, time.monotonic() - client.last_seen),
        }

    def _latency_summary_locked(self) -> dict[str, object]:
        latencies = [client.latency_ms for client in self._clients.values() if client.latency_ms is not None]
        if not latencies:
            return {
                "sample_count": 0,
                "average_ms": None,
                "min_ms": None,
                "max_ms": None,
            }
        sample_count = len(latencies)
        average_ms = sum(latencies) / sample_count
        return {
            "sample_count": sample_count,
            "average_ms": average_ms,
            "min_ms": min(latencies),
            "max_ms": max(latencies),
        }

    def _build_time_limit_status_locked(self, *, now: Optional[float] = None) -> dict[str, object]:
        current_time = now if now is not None else time.time()
        if self._time_limit_duration_seconds is None or self._time_limit_started_at is None:
            return {
                "is_active": False,
                "duration_seconds": None,
                "remaining_seconds": None,
                "end_timestamp": None,
                "started_at": None,
                "is_expired": False,
                "progress": None,
                "updated_at": current_time,
            }
        duration_seconds = max(1.0, self._time_limit_duration_seconds)
        end_timestamp = self._time_limit_end_timestamp or (self._time_limit_started_at + duration_seconds)
        remaining_seconds = max(0.0, end_timestamp - current_time)
        elapsed_seconds = max(0.0, current_time - self._time_limit_started_at)
        progress = None
        if duration_seconds > 0:
            progress = min(1.0, max(0.0, elapsed_seconds / duration_seconds))
        return {
            "is_active": True,
            "duration_seconds": int(round(duration_seconds)),
            "remaining_seconds": int(round(remaining_seconds)),
            "end_timestamp": float(end_timestamp),
            "started_at": float(self._time_limit_started_at),
            "is_expired": remaining_seconds <= 0.0,
            "progress": progress,
            "updated_at": current_time,
        }


def _calculate_rate(total_bytes: int, connected_at: float) -> float:
    elapsed = max(0.001, time.time() - connected_at)
    return (total_bytes * 8) / elapsed
