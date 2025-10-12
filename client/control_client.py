from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Awaitable, Callable, Deque, Dict, Optional

from shared.protocol import (
    ChatMessage,
    ClientIdentity,
    ControlAction,
    decode_control_stream,
    encode_control_message,
)

logger = logging.getLogger(__name__)

HeartbeatCallback = Callable[[None], None]
MessageCallback = Callable[[ControlAction, dict], Awaitable[None] | None]


class ControlClient:
    """Handles TCP control connection to the collaboration server."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        on_message: MessageCallback,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._on_message = on_message
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._buffer = bytearray()
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._send_queue: Deque[bytes] = deque()
        self._send_event = asyncio.Event()
        self._connected = asyncio.Event()
        self._stop = False

    async def connect(self) -> None:
        logger.info("Connecting to server %s:%s", self._host, self._port)
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        hello = encode_control_message(
            ControlAction.HELLO,
            ClientIdentity(username=self._username).to_dict(),
        )
        await self._send_raw(hello)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        asyncio.create_task(self._send_loop())
        asyncio.create_task(self._recv_loop())
        await self._connected.wait()

    async def close(self) -> None:
        self._stop = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass

    async def _send_raw(self, data: bytes) -> None:
        if not self._writer:
            raise RuntimeError("Client is not connected")
        self._writer.write(data)
        await self._writer.drain()

    async def send(self, action: ControlAction, payload: Dict[str, object]) -> None:
        self._send_queue.append(encode_control_message(action, payload))
        self._send_event.set()

    async def send_chat(self, message: str) -> None:
        await self.send(
            ControlAction.CHAT_MESSAGE,
            {
                "message": message,
                "timestamp_ms": int(time.time() * 1000),
            },
        )

    async def _send_loop(self) -> None:
        while not self._stop:
            await self._send_event.wait()
            self._send_event.clear()
            while self._send_queue:
                data = self._send_queue.popleft()
                try:
                    await self._send_raw(data)
                except Exception:
                    logger.exception("Failed to send control message")
                    self._stop = True
                    break

    async def _recv_loop(self) -> None:
        assert self._reader is not None
        reader = self._reader
        try:
            while not self._stop:
                chunk = await reader.read(4096)
                if not chunk:
                    logger.info("Server closed control connection")
                    break
                self._buffer.extend(chunk)
                messages, remaining = decode_control_stream(bytes(self._buffer))
                self._buffer = bytearray(remaining)
                for message in messages:
                    action = ControlAction(message["action"])
                    payload = message["data"]
                    if action == ControlAction.WELCOME:
                        self._connected.set()
                    asyncio.create_task(self._dispatch(action, payload))
        finally:
            await self.close()

    async def _dispatch(self, action: ControlAction, payload: dict) -> None:
        try:
            result = self._on_message(action, payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("Error while handling control message %s", action)

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._stop:
                await asyncio.sleep(3)
                timestamp_ms = int(time.time() * 1000)
                logger.debug("Sending heartbeat from %s at %s", self._username, timestamp_ms)
                await self.send(ControlAction.HEARTBEAT, {"timestamp_ms": timestamp_ms})
        except asyncio.CancelledError:
            pass
