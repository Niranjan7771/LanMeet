from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import time
from typing import Optional

from shared.protocol import ControlAction

from .session_manager import SessionManager

logger = logging.getLogger(__name__)

_LENGTH_STRUCT = struct.Struct("!I")


class ScreenServer:
    """TCP server receiving presenter screen frames and broadcasting to participants."""

    def __init__(self, host: str, port: int, session_manager: SessionManager) -> None:
        self._host = host
        self._port = port
        self._session_manager = session_manager
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_connection, self._host, self._port)
        sockets = ", ".join(str(sock.getsockname()) for sock in self._server.sockets or [])
        logger.info("Screen server listening on %s", sockets)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        username = None
        try:
            handshake = await self._read_json(reader)
            username = handshake.get("username")
            if not username:
                raise ValueError("username missing in screen share handshake")
            if not await self._session_manager.is_presenter(username):
                raise PermissionError(f"{username} is not the active presenter")
            width = handshake.get("width")
            height = handshake.get("height")
            fps = handshake.get("fps")
            logger.info("Screen stream from %s (%sx%s @ %s fps)", username, width, height, fps)
            await self._session_manager.broadcast(
                ControlAction.SCREEN_CONTROL,
                {"state": "start", "username": username, "width": width, "height": height},
                exclude={username},
            )
            while True:
                frame = await self._read_frame(reader)
                if frame is None:
                    break
                encoded = base64.b64encode(frame).decode("ascii")
                await self._session_manager.broadcast(
                    ControlAction.SCREEN_FRAME,
                    {
                        "username": username,
                        "timestamp_ms": int(time.time() * 1000),
                        "frame": encoded,
                        "width": width,
                        "height": height,
                    },
                    exclude={username},
                )
        except asyncio.IncompleteReadError:
            logger.warning("Screen stream ended abruptly from %s", peer)
        except PermissionError as exc:
            logger.warning("Screen stream rejected: %s", exc)
        except Exception:
            logger.exception("Error while handling screen stream from %s", peer)
        finally:
            if username:
                await self._session_manager.broadcast(
                    ControlAction.SCREEN_CONTROL,
                    {"state": "stop", "username": username},
                    exclude={username},
                )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_json(self, reader: asyncio.StreamReader) -> dict:
        length_bytes = await reader.readexactly(_LENGTH_STRUCT.size)
        (length,) = _LENGTH_STRUCT.unpack(length_bytes)
        payload = await reader.readexactly(length)
        return json.loads(payload.decode("utf-8"))

    async def _read_frame(self, reader: asyncio.StreamReader) -> Optional[bytes]:
        length_bytes = await reader.readexactly(_LENGTH_STRUCT.size)
        (length,) = _LENGTH_STRUCT.unpack(length_bytes)
        if length == 0:
            return None
        return await reader.readexactly(length)
