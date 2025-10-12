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
    ) -> None:
        self._host = host
        self._port = port
        self._session_manager = session_manager
        self._server: Optional[asyncio.AbstractServer] = None
        self._file_server = file_server
        self._video_server = video_server
        self._audio_server = audio_server
        self._media_config = media_config or {}

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
                    client = await self._session_manager.register(identity.username, writer, peername=peer)
                    username = client.username
                    await self._session_manager.record_received(username, len(data))
                    participants = await self._session_manager.list_clients()
                    await self._session_manager.broadcast(
                        ControlAction.USER_JOINED,
                        {"username": username, "participants": participants},
                        exclude={username},
                    )
                    chat_history = [msg.to_dict() for msg in await self._session_manager.get_chat_history()]
                    file_offers = []
                    if self._file_server:
                        file_offers = [offer.to_dict() for offer in await self._file_server.list_files()]
                    presenter = await self._session_manager.get_presenter()
                    client.send(
                        ControlAction.WELCOME,
                        {
                            "username": username,
                            "chat_history": chat_history,
                            "peers": await self._session_manager.list_clients(),
                            "files": file_offers,
                            "media": self._media_config,
                            "presenter": presenter,
                        },
                    )
                    await writer.drain()
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
            chat = ChatMessage.from_dict({
                "sender": username,
                "message": payload.get("message", ""),
                "timestamp_ms": payload.get("timestamp_ms"),
            })
            await self._session_manager.add_chat_message(chat)
            await self._session_manager.broadcast(ControlAction.CHAT_MESSAGE, chat.to_dict())
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

        # TODO: handle screen, file control messages.
        logger.debug("Unhandled control action %s from %s", action, username)
