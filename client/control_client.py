from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Awaitable, Callable, Deque, Dict, Optional

from shared.protocol import ClientIdentity, ControlAction, decode_control_stream, encode_control_message

logger = logging.getLogger(__name__)

MessageCallback = Callable[[ControlAction, dict], Awaitable[None] | None]
DisconnectCallback = Callable[[Optional[str]], Awaitable[None] | None]


class ControlClient:
    """Handles TCP control connection to the collaboration server."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        on_message: MessageCallback,
        *,
        pre_shared_key: Optional[str] = None,
        on_disconnect: Optional[DisconnectCallback] = None,
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
        self._pre_shared_key = pre_shared_key
        self._on_disconnect = on_disconnect

    async def connect(self) -> None:
        logger.info("Connecting to server %s:%s", self._host, self._port)
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        hello = encode_control_message(
            ControlAction.HELLO,
            ClientIdentity(username=self._username, pre_shared_key=self._pre_shared_key).to_dict(),
        )
        await self._send_raw(hello)
        asyncio.create_task(self._send_loop())
        asyncio.create_task(self._recv_loop())
        await self._connected.wait()
        if self._stop:
            raise ConnectionError("Connection closed before handshake completed")
        await self._send_heartbeat()
        if not self._stop:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

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
        self._reader = None
        self._writer = None
        self._connected.clear()

    async def _notify_disconnect(self, reason: Optional[str]) -> None:
        if self._on_disconnect is None:
            return
        try:
            result = self._on_disconnect(reason)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("Disconnect callback failed")

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

    async def send_typing(self, is_typing: bool) -> None:
        await self.send(
            ControlAction.TYPING_STATUS,
            {
                "is_typing": is_typing,
                "timestamp_ms": int(time.time() * 1000),
            },
        )

    async def send_hand_status(self, hand_raised: bool) -> None:
        await self.send(
            ControlAction.HAND_STATUS,
            {
                "hand_raised": hand_raised,
                "timestamp_ms": int(time.time() * 1000),
            },
        )

    async def send_reaction(self, reaction: str) -> None:
        await self.send(
            ControlAction.REACTION,
            {
                "reaction": reaction,
                "timestamp_ms": int(time.time() * 1000),
            },
        )

    async def send_latency_update(self, latency_ms: float, jitter_ms: Optional[float] = None) -> None:
        payload: Dict[str, object] = {
            "latency_ms": float(latency_ms),
            "timestamp_ms": int(time.time() * 1000),
        }
        if jitter_ms is not None:
            payload["jitter_ms"] = float(jitter_ms)
        await self.send(ControlAction.LATENCY_UPDATE, payload)

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
        disconnect_reason: Optional[str] = None
        try:
            while not self._stop:
                chunk = await reader.read(4096)
                if not chunk:
                    logger.info("Server closed control connection")
                    disconnect_reason = "server_closed"
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
        except Exception:
            logger.exception("Error while receiving from control server")
            disconnect_reason = "recv_error"
        finally:
            if not self._connected.is_set():
                self._connected.set()
            await self.close()
            await self._notify_disconnect(disconnect_reason or "connection_closed")

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
                await self._send_heartbeat()
        except asyncio.CancelledError:
            pass

    async def _send_heartbeat(self) -> None:
        timestamp_ms = int(time.time() * 1000)
        logger.debug("Sending heartbeat from %s at %s", self._username, timestamp_ms)
        await self.send(ControlAction.HEARTBEAT, {"timestamp_ms": timestamp_ms})
