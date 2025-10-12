from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

import numpy as np

from shared.protocol import MEDIA_HEADER_STRUCT, MediaFrameHeader, PayloadType

from .session_manager import SessionManager

logger = logging.getLogger(__name__)

FRAME_INTERVAL = 0.02  # 20 ms


class AudioServer(asyncio.DatagramProtocol):
    """Receives audio chunks from clients, mixes them, and broadcasts the composite stream."""

    def __init__(self, session_manager: SessionManager) -> None:
        self._session_manager = session_manager
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._clients: Dict[Tuple[str, int], str] = {}
        self._buffers: Dict[str, Deque[np.ndarray]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._mix_task: Optional[asyncio.Task[None]] = None
        self._sample_rate = 16000
        self._channels = 1
        self._frame_samples = 320  # default 20ms for 16kHz mono
        self._sequence = 0

    async def start(self, host: str, port: int) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=(host, port))
        logger.info("Audio server listening on %s:%s", host, port)
        self._mix_task = asyncio.create_task(self._mix_loop())

    async def stop(self) -> None:
        if self._mix_task:
            self._mix_task.cancel()
            try:
                await self._mix_task
            except asyncio.CancelledError:
                pass
            self._mix_task = None
        if self._transport:
            self._transport.close()
            self._transport = None

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:  # pragma: no cover - UDP callback
        if addr not in self._clients:
            try:
                message = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            if message.get("action") != "register":
                return
            username = message.get("username")
            if not username:
                return
            self._clients[addr] = username
            self._sample_rate = int(message.get("sample_rate", self._sample_rate))
            self._channels = int(message.get("channels", self._channels))
            self._frame_samples = int(message.get("frame_samples", self._frame_samples))
            logger.info("Registered audio client %s at %s", username, addr)
            return

        if len(data) < MEDIA_HEADER_STRUCT.size:
            return
        header = MediaFrameHeader.unpack(data[: MEDIA_HEADER_STRUCT.size])
        if header.payload_type != PayloadType.AUDIO.value:
            return
        payload = data[MEDIA_HEADER_STRUCT.size :]
        samples = np.frombuffer(payload, dtype=np.float32)
        username = self._clients[addr]
        asyncio.create_task(self._enqueue(username, samples))

    async def remove_user(self, username: str) -> None:
        async with self._lock:
            for addr, user in list(self._clients.items()):
                if user == username:
                    self._clients.pop(addr, None)
            self._buffers.pop(username, None)

    async def _enqueue(self, username: str, samples: np.ndarray) -> None:
        async with self._lock:
            self._buffers[username].append(samples)

    async def _mix_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(FRAME_INTERVAL)
                targets = list(self._clients.keys())
                if not targets:
                    continue
                async with self._lock:
                    # gather one chunk per user if available
                    contributor_arrays = []
                    for user in list(self._buffers.keys()):
                        buffer = self._buffers[user]
                        if buffer:
                            contributor_arrays.append(buffer.popleft())
                    if not contributor_arrays:
                        continue
                max_len = max(arr.size for arr in contributor_arrays)
                mix = np.zeros(max_len, dtype=np.float32)
                for arr in contributor_arrays:
                    if arr.size < max_len:
                        padded = np.zeros(max_len, dtype=np.float32)
                        padded[: arr.size] = arr
                        mix += padded
                    else:
                        mix[: arr.size] += arr
                mix /= max(1, len(contributor_arrays))
                payload = mix.astype(np.float32).tobytes()
                self._sequence = (self._sequence + 1) % (2**31)
                header = MediaFrameHeader(
                    stream_id=1,
                    sequence_number=self._sequence,
                    timestamp_ms=0.0,
                    payload_type=PayloadType.AUDIO.value,
                ).pack()
                datagram = header + payload
                for target in targets:
                    try:
                        self._transport and self._transport.sendto(datagram, target)
                    except Exception:
                        logger.exception("Failed to send mixed audio to %s", target)
        except asyncio.CancelledError:  # pragma: no cover - loop cancellation
            pass

    def connection_lost(self, exc: Optional[Exception]) -> None:  # pragma: no cover - UDP callback
        if exc:
            logger.error("Audio server connection lost: %s", exc)
        else:
            logger.info("Audio server closed")
