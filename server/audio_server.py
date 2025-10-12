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
MAX_BUFFER_FRAMES = 10  # keep at most 200ms of backlog per user


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
            buffer = self._buffers[username]
            buffer.append(samples)
            while len(buffer) > MAX_BUFFER_FRAMES:
                buffer.popleft()

    async def _mix_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(FRAME_INTERVAL)
                targets = list(self._clients.keys())
                if not targets:
                    continue
                async with self._lock:
                    # gather one chunk per user if available
                    contributions: Dict[str, np.ndarray] = {}
                    max_len = 0
                    for user, buffer in list(self._buffers.items()):
                        if buffer:
                            chunk = buffer.popleft()
                            contributions[user] = chunk
                            if chunk.size > max_len:
                                max_len = chunk.size
                    if not contributions or max_len == 0:
                        continue

                # Normalize chunks to a common length for mixing
                padded: Dict[str, np.ndarray] = {}
                for user, chunk in contributions.items():
                    if chunk.size == max_len:
                        padded[user] = chunk
                    else:
                        pad = np.zeros(max_len, dtype=np.float32)
                        pad[: chunk.size] = chunk
                        padded[user] = pad

                for target in targets:
                    username = self._clients.get(target)
                    if not username:
                        continue
                    others = [audio for user, audio in padded.items() if user != username]
                    if not others:
                        mixed = np.zeros(max_len, dtype=np.float32)
                    else:
                        mixed = np.zeros(max_len, dtype=np.float32)
                        for audio in others:
                            mixed += audio
                        mixed /= len(others)

                    payload = mixed.astype(np.float32).tobytes()
                    self._sequence = (self._sequence + 1) % (2**31)
                    header = MediaFrameHeader(
                        stream_id=1,
                        sequence_number=self._sequence,
                        timestamp_ms=0.0,
                        payload_type=PayloadType.AUDIO.value,
                    ).pack()
                    datagram = header + payload
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
