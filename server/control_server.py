from __future__ import annotations

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

from shared.protocol import (
    ChatMessage,
    ClientIdentity,
    ControlAction,
    decode_control_stream,
    encode_control_message,
)

from .session_manager import SessionManager

if TYPE_CHECKING:
    from .audio_server import AudioServer
    from .file_server import FileServer
    from .video_server import VideoServer

logger = logging.getLogger(__name__)


class ControlServer:
    """Handles TCP control plane for chat, messaging, and coordination."""

    def __init__(
        self,
        host: str,
        port: int,
        session_manager: SessionManager,
        file_server: Optional["FileServer"] = None,
        video_server: Optional["VideoServer"] = None,
        audio_server: Optional["AudioServer"] = None,
        media_config: Optional[dict] = None,
        *,
        pre_shared_key: Optional[str] = None,
        latency_port: Optional[int] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._session_manager = session_manager
        self._server: Optional[asyncio.AbstractServer] = None
        self._file_server = file_server
        self._video_server = video_server
        self._audio_server = audio_server
        self._media_config = media_config or {}
        if latency_port is not None:
            self._media_config.setdefault("latency_port", latency_port)
        self._pre_shared_key = pre_shared_key
        self._latency_port = latency_port

    async def _broadcast_presence_entry(self, username: str) -> None:
        entry = await self._session_manager.get_presence_entry(username)
        if entry:
            await self._session_manager.broadcast(ControlAction.PRESENCE_UPDATE, entry)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        sockets = ", ".join(str(sock.getsockname()) for sock in self._server.sockets or [])
        logger.info("Control server listening on %s", sockets)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def force_disconnect(self, username: str, *, actor: str = "admin") -> bool:
        """Forcefully remove a client from the session and clean up media state."""

        await self._session_manager.send_to(
            username,
            ControlAction.KICKED,
            {
                "reason": "An administrator removed you from this meeting.",
                "actor": actor,
            },
        )

        removed = await self._session_manager.unregister(
            username,
            event_type="user_kicked",
            details={"actor": actor},
        )

        if not removed:
            return False

        await self._session_manager.ban_user(username)

        participants = await self._session_manager.list_clients()
        await self._session_manager.broadcast(
            ControlAction.USER_LEFT,
            {"username": username, "participants": participants},
        )
        presence = await self._session_manager.get_presence_snapshot()
        await self._session_manager.broadcast(
            ControlAction.PRESENCE_SYNC,
            {"participants": presence},
        )

        tasks = []
        if self._video_server:
            tasks.append(self._video_server.remove_user(username))
        if self._audio_server:
            tasks.append(self._audio_server.remove_user(username))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("Forcefully disconnected %s (actor=%s)", username, actor)
        return True

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        logger.info("Incoming TCP connection from %s", peer)

        buffer = b""
        username: Optional[str] = None
        try:
            # Expect initial HELLO with identity
            while username is None:
                data = await reader.read(4096)
                if not data:
                    raise ConnectionError("connection closed before handshake")
                buffer += data
                messages, buffer = decode_control_stream(buffer)
                for message in messages:
                    action = ControlAction(message["action"])
                    payload = message["data"]
                    if action != ControlAction.HELLO:
                        raise ValueError("Expected HELLO as first message")
                    identity = ClientIdentity.from_dict(payload)
                    if self._pre_shared_key and identity.pre_shared_key != self._pre_shared_key:
                        logger.warning("Rejected client %s due to invalid pre-shared key", identity.username)
                        try:
                            writer.write(
                                encode_control_message(
                                    ControlAction.ERROR,
                                    {
                                        "reason": "Authentication failed",
                                        "code": "auth_failed",
                                    },
                                )
                            )
                            await writer.drain()
                        except Exception:
                            logger.debug("Failed to notify unauthenticated client %s", identity.username)
                        return
                    if await self._session_manager.is_banned(identity.username):
                        logger.warning("Rejected banned user %s", identity.username)
                        try:
                            writer.write(
                                encode_control_message(
                                    ControlAction.KICKED,
                                    {
                                        "reason": "An administrator removed you from this meeting.",
                                    },
                                )
                            )
                            await writer.drain()
                        except Exception:
                            logger.debug("Failed to notify banned user %s during handshake", identity.username)
                        await self._session_manager.record_blocked_attempt(identity.username)
                        return
                    client = await self._session_manager.register(identity.username, writer, peername=peer)
                    username = client.username
                    await self._session_manager.record_received(username, len(data))
                    participants = await self._session_manager.list_clients()
                    await self._session_manager.broadcast(
                        ControlAction.USER_JOINED,
                        {"username": username, "participants": participants},
                        exclude={username},
                    )
                    # Send chat history filtered for the joining user
                    chat_history = [msg.to_dict() for msg in await self._session_manager.get_chat_history_for(identity.username)]
                    file_offers = []
                    if self._file_server:
                        file_offers = [offer.to_dict() for offer in await self._file_server.list_files()]
                    presenter = await self._session_manager.get_presenter()
                    media_state = await self._session_manager.get_media_state_snapshot()
                    presence = await self._session_manager.get_presence_snapshot()
                    client.send(
                        ControlAction.WELCOME,
                        {
                            "username": username,
                            "chat_history": chat_history,
                            "peers": await self._session_manager.list_clients(),
                            "files": file_offers,
                            "media": self._media_config,
                            "presenter": presenter,
                            "media_state": media_state,
                            "presence": presence,
                            "time_limit": await self._session_manager.get_time_limit_status(),
                        },
                    )
                    await writer.drain()
                    await self._session_manager.broadcast(
                        ControlAction.PRESENCE_SYNC,
                        {
                            "participants": presence,
                        },
                    )
            assert username is not None

            while True:
                data = await reader.read(4096)
                if not data:
                    break
                buffer += data
                if username:
                    await self._session_manager.record_received(username, len(data))
                messages, buffer = decode_control_stream(buffer)
                for message in messages:
                    action = ControlAction(message["action"])
                    payload = message["data"]
                    await self._handle_message(username, action, payload)
        except Exception as exc:
            logger.exception("Error while handling client %s: %s", peer, exc)
        finally:
            if username:
                removed = await self._session_manager.unregister(username)
                if removed:
                    participants = await self._session_manager.list_clients()
                    await self._session_manager.broadcast(
                        ControlAction.USER_LEFT,
                        {"username": username, "participants": participants},
                    )
                    presence = await self._session_manager.get_presence_snapshot()
                    await self._session_manager.broadcast(
                        ControlAction.PRESENCE_SYNC,
                        {"participants": presence},
                    )
                tasks = []
                if self._video_server:
                    tasks.append(self._video_server.remove_user(username))
                if self._audio_server:
                    tasks.append(self._audio_server.remove_user(username))
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_message(self, username: str, action: ControlAction, payload: dict) -> None:
        if action == ControlAction.HEARTBEAT:
            await self._session_manager.mark_heartbeat(username)
            return

        if action == ControlAction.CHAT_MESSAGE:
            # Normalize recipients if any (list of strings)
            recipients_raw = payload.get("recipients")
            recipients: list[str] | None = None
            if isinstance(recipients_raw, list):
                recipients = [str(x).strip() for x in recipients_raw if isinstance(x, str) and x.strip()]
                if not recipients:
                    recipients = None

            chat = ChatMessage.from_dict({
                "sender": username,
                "message": payload.get("message", ""),
                "timestamp_ms": payload.get("timestamp_ms"),
                "recipients": recipients,
            })
            await self._session_manager.add_chat_message(chat)
            # Route: broadcast if no recipients; else direct-send to recipients plus sender
            if not recipients:
                await self._session_manager.broadcast(ControlAction.CHAT_MESSAGE, chat.to_dict())
            else:
                targets = set(recipients)
                targets.add(username)
                # Only send to currently connected clients in targets
                for target in list(targets):
                    await self._session_manager.send_to(target, ControlAction.CHAT_MESSAGE, chat.to_dict())
            return

        if action == ControlAction.PRESENTER_GRANTED:
            granted = await self._session_manager.grant_presenter(username)
            if not granted:
                return
            await self._session_manager.broadcast(
                ControlAction.PRESENTER_GRANTED,
                {"username": username},
            )
            return

        if action == ControlAction.PRESENTER_REVOKED:
            await self._session_manager.revoke_presenter(username)
            await self._session_manager.broadcast(
                ControlAction.PRESENTER_REVOKED,
                {"username": username},
            )
            return

        if action == ControlAction.FILE_REQUEST:
            req_type = payload.get("request")
            if req_type == "list" and self._file_server:
                offers = [offer.to_dict() for offer in await self._file_server.list_files()]
                await self._session_manager.send_to(
                    username,
                    ControlAction.FILE_OFFER,
                    {"files": offers},
                )
            return

        if action == ControlAction.VIDEO_STATUS:
            enabled = bool(payload.get("video_enabled", False))
            state = await self._session_manager.update_media_state(username, video_enabled=enabled)
            if state:
                await self._session_manager.broadcast(
                    ControlAction.VIDEO_STATUS,
                    state,
                )
                await self._broadcast_presence_entry(username)
            return

        if action == ControlAction.AUDIO_STATUS:
            enabled = bool(payload.get("audio_enabled", False))
            state = await self._session_manager.update_media_state(username, audio_enabled=enabled)
            if state:
                await self._session_manager.broadcast(
                    ControlAction.AUDIO_STATUS,
                    state,
                )
                await self._broadcast_presence_entry(username)
            return

        if action == ControlAction.TYPING_STATUS:
            result = await self._session_manager.set_typing(
                username,
                bool(payload.get("is_typing", False)),
            )
            if result:
                await self._session_manager.broadcast(ControlAction.TYPING_STATUS, result)
                await self._broadcast_presence_entry(username)
            return

        if action == ControlAction.HAND_STATUS:
            raised = bool(payload.get("hand_raised", False))
            result = await self._session_manager.set_hand_status(username, raised=raised)
            if result:
                await self._session_manager.broadcast(ControlAction.HAND_STATUS, result)
                presence = await self._session_manager.get_presence_snapshot()
                await self._session_manager.broadcast(
                    ControlAction.PRESENCE_SYNC,
                    {"participants": presence},
                )
                await self._broadcast_presence_entry(username)
            return

        if action == ControlAction.REACTION:
            reaction = {
                "username": username,
                "reaction": payload.get("reaction", ""),
                "timestamp_ms": payload.get("timestamp_ms"),
            }
            await self._session_manager.broadcast(ControlAction.REACTION, reaction)
            return

        if action == ControlAction.LATENCY_UPDATE:
            latency_ms = float(payload.get("latency_ms", 0.0))
            jitter_ms = payload.get("jitter_ms")
            if jitter_ms is not None:
                jitter_ms = float(jitter_ms)
            result = await self._session_manager.update_latency(
                username,
                latency_ms=latency_ms,
                jitter_ms=jitter_ms,
            )
            if result:
                await self._session_manager.broadcast(ControlAction.LATENCY_UPDATE, result)
                await self._broadcast_presence_entry(username)
            return

        # TODO: handle screen, file control messages.
        logger.debug("Unhandled control action %s from %s", action, username)
