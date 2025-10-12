from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Dict, Optional, Set, Tuple

from shared.protocol import ChatMessage, ControlAction, encode_control_message

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT = 10.0  # seconds


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

    async def register(self, username: str, writer: asyncio.StreamWriter, peername: Optional[Tuple[str, ...]] = None) -> ConnectedClient:
        async with self._lock:
            if username in self._clients:
                raise ValueError(f"Username '{username}' already connected")
            client = ConnectedClient(username=username, writer=writer)
            if peername:
                client.peer_ip = peername[0]
                if len(peername) > 1:
                    try:
                        client.peer_port = int(peername[1])
                    except (TypeError, ValueError):
                        client.peer_port = None
            self._clients[username] = client
            logger.info("Registered client %s", username)
            self._record_event(
                "user_joined",
                {
                    "username": username,
                },
            )
            return client

    async def unregister(self, username: str) -> bool:
        async with self._lock:
            client = self._clients.pop(username, None)
            if client is None:
                return False
            if self._presenter == username:
                self._presenter = None
            try:
                client.writer.close()
            except Exception:  # pragma: no cover - cleanup best effort
                logger.exception("Error while closing writer for %s", username)
            logger.info("Unregistered client %s", username)
            self._record_event(
                "user_left",
                {
                    "username": username,
                },
            )
            return True

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
                client.touch()

    def _record_event(self, event_type: str, details: Dict[str, object]) -> None:
        event = {
            "type": event_type,
            "timestamp": time.time(),
            "details": details,
        }
        self._event_log.append(event)
        if len(self._event_log) > 1000:
            self._event_log.pop(0)


def _calculate_rate(total_bytes: int, connected_at: float) -> float:
    elapsed = max(0.001, time.time() - connected_at)
    return (total_bytes * 8) / elapsed
