from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Optional, Tuple

from shared.protocol import MEDIA_HEADER_STRUCT, MediaFrameHeader, PayloadType

from .session_manager import SessionManager

logger = logging.getLogger(__name__)


class VideoServer(asyncio.DatagramProtocol):
    """UDP relay for video frames."""

    def __init__(self, session_manager: SessionManager) -> None:
        self._session_manager = session_manager
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._clients: Dict[Tuple[str, int], str] = {}

    async def start(self, host: str, port: int) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=(host, port))
        logger.info("Video server listening on %s:%s", host, port)

    async def stop(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:  # pragma: no cover - UDP callback
        logger.debug("Video server transport ready")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:  # pragma: no cover - UDP callback
        if addr not in self._clients:
            try:
                message = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.debug("Discarding non-JSON handshake from %s", addr)
                return
            if message.get("action") != "register":
                logger.debug("Unexpected handshake payload from %s: %s", addr, message)
                return
            username = message.get("username")
            if not username:
                return
            self._clients[addr] = username
            logger.info("Registered video client %s at %s", username, addr)
            return

        if len(data) < MEDIA_HEADER_STRUCT.size:
            return
        header = MediaFrameHeader.unpack(data[: MEDIA_HEADER_STRUCT.size])
        if header.payload_type != PayloadType.VIDEO.value:
            return
        # Relay frame to every other participant
        for target_addr, target_user in list(self._clients.items()):
            if target_addr == addr:
                continue
            try:
                self._transport and self._transport.sendto(data, target_addr)
            except Exception:
                logger.exception("Failed to forward video frame to %s", target_user)

    async def remove_user(self, username: str) -> None:
        for addr, user in list(self._clients.items()):
            if user == username:
                self._clients.pop(addr, None)

    def connection_lost(self, exc: Optional[Exception]) -> None:  # pragma: no cover - UDP callback
        if exc:
            logger.error("Video server connection lost: %s", exc)
        else:
            logger.info("Video server closed")
