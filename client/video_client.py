from __future__ import annotations

import asyncio
import base64
import json
import time
import zlib
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from shared.protocol import MEDIA_HEADER_STRUCT, MediaFrameHeader, PayloadType

FrameCallback = Callable[[str, str], Awaitable[None]]


def stream_id_for(username: str) -> int:
    return zlib.crc32(username.encode("utf-8")) & 0xFFFFFFFF


class _VideoProtocol(asyncio.DatagramProtocol):
    def __init__(self, client: "VideoClient") -> None:
        self._client = client

    def connection_made(self, transport: asyncio.BaseTransport) -> None:  # pragma: no cover - UDP callback
        self._client._on_transport_ready(transport)

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:  # pragma: no cover - UDP callback
        self._client._on_datagram(data)


class VideoClient:
    """Captures webcam frames and handles incoming composite video streams."""

    def __init__(
        self,
        username: str,
        server_host: str,
        server_port: int,
        on_frame: FrameCallback,
        *,
        device_index: int = 0,
        width: int = 640,
        height: int = 360,
        fps: int = 12,
        quality: int = 60,
    ) -> None:
        self._username = username
        self._server_host = server_host
        self._server_port = server_port
        self._on_frame = on_frame
        self._device_index = device_index
        self._width = width
        self._height = height
        self._fps = max(1, fps)
        self._quality = max(20, min(quality, 90))
        self._stream_id = stream_id_for(username)
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._capture_task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._sequence = 0
        self._peers: Dict[int, str] = {}
        self._capture_enabled = False

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: _VideoProtocol(self), local_addr=("0.0.0.0", 0))
        self._register()
        self._stop_event.clear()
        self._capture_task = asyncio.create_task(self._capture_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._capture_task:
            await self._capture_task
            self._capture_task = None
        self._capture_enabled = False
        if self._transport:
            self._transport.close()
            self._transport = None

    def update_peers(self, peers: List[str]) -> None:
        mapping = {stream_id_for(peer): peer for peer in peers if peer != self._username}
        self._peers = mapping

    def _on_transport_ready(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]
        self._register()

    def _register(self) -> None:
        if not self._transport:
            return
        payload = json.dumps({"action": "register", "username": self._username}).encode("utf-8")
        self._transport.sendto(payload, (self._server_host, self._server_port))

    def _on_datagram(self, data: bytes) -> None:
        if len(data) < MEDIA_HEADER_STRUCT.size:
            return
        header = MediaFrameHeader.unpack(data[: MEDIA_HEADER_STRUCT.size])
        if header.payload_type != PayloadType.VIDEO.value:
            return
        if header.stream_id == self._stream_id:
            return
        payload = data[MEDIA_HEADER_STRUCT.size :]
        username = self._peers.get(header.stream_id, f"stream-{header.stream_id}")
        encoded = base64.b64encode(payload).decode("ascii")
        asyncio.create_task(self._on_frame(username, encoded))

    async def _capture_loop(self) -> None:
        cap = cv2.VideoCapture(self._device_index)
        if not cap.isOpened():
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        cap.set(cv2.CAP_PROP_FPS, self._fps)
        try:
            frame_interval = 1 / self._fps
            while not self._stop_event.is_set():
                if not self._capture_enabled:
                    await asyncio.sleep(0.2)
                    continue
                frame = await asyncio.to_thread(self._read_frame, cap)
                if frame is None:
                    continue
                success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._quality])
                if not success:
                    continue
                payload = buffer.tobytes()
                header = MediaFrameHeader(
                    stream_id=self._stream_id,
                    sequence_number=self._next_sequence(),
                    timestamp_ms=time.time() * 1000,
                    payload_type=PayloadType.VIDEO.value,
                ).pack()
                if self._transport:
                    self._transport.sendto(header + payload, (self._server_host, self._server_port))
                await asyncio.sleep(frame_interval)
        finally:
            cap.release()

    def _read_frame(self, cap: cv2.VideoCapture) -> Optional[np.ndarray]:
        ret, frame = cap.read()
        if not ret:
            return None
        frame = cv2.resize(frame, (self._width, self._height))
        return frame

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) % (2**31)
        return self._sequence

    def set_capture_enabled(self, enabled: bool) -> None:
        self._capture_enabled = enabled
