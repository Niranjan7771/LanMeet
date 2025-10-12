from __future__ import annotations

import asyncio
import json
import struct
import time
from typing import Optional, Tuple

import cv2
import numpy as np
from mss import mss

from shared.protocol import DEFAULT_SCREEN_PORT

_LENGTH_STRUCT = struct.Struct("!I")


class ScreenPublisher:
    """Captures the local screen and streams JPEG frames to the screen server."""

    def __init__(
        self,
        username: str,
        server_host: str,
        port: int = DEFAULT_SCREEN_PORT,
        *,
        fps: int = 8,
        quality: int = 75,
        monitor: Optional[int] = None,
    ) -> None:
        self._username = username
        self._server_host = server_host
        self._port = port
        self._fps = max(1, fps)
        self._quality = int(np.clip(quality, 10, 95))
        self._monitor = monitor
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task

    async def _run(self) -> None:
        reader, writer = await asyncio.open_connection(self._server_host, self._port)
        try:
            display, resolution = await asyncio.to_thread(self._prepare_monitor)
            width, height = resolution
            handshake = {
                "username": self._username,
                "width": width,
                "height": height,
                "fps": self._fps,
            }
            await self._send_json(writer, handshake)

            frame_interval = 1 / self._fps
            last_sent = time.perf_counter()
            while not self._stop_event.is_set():
                frame_bytes = await asyncio.to_thread(self._capture_frame, display, width, height)
                if frame_bytes is None:
                    continue
                await self._write_frame(writer, frame_bytes)
                now = time.perf_counter()
                elapsed = now - last_sent
                if elapsed < frame_interval:
                    await asyncio.sleep(frame_interval - elapsed)
                last_sent = now
            await self._write_frame(writer, b"")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _prepare_monitor(self) -> Tuple[dict, Tuple[int, int]]:
        with mss() as sct:
            if self._monitor is not None and 0 < self._monitor < len(sct.monitors):
                monitor = sct.monitors[self._monitor]
            else:
                monitor = sct.monitors[1]
            width = monitor["width"]
            height = monitor["height"]
        return monitor, (width, height)

    def _capture_frame(self, monitor: dict, width: int, height: int) -> Optional[bytes]:
        with mss() as sct:
            raw = sct.grab(monitor)
        frame = np.array(raw)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        success, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._quality])
        if not success:
            return None
        return bytes(encoded)

    async def _send_json(self, writer: asyncio.StreamWriter, data: dict) -> None:
        payload = json.dumps(data).encode("utf-8")
        writer.write(_LENGTH_STRUCT.pack(len(payload)))
        writer.write(payload)
        await writer.drain()

    async def _write_frame(self, writer: asyncio.StreamWriter, frame: bytes) -> None:
        writer.write(_LENGTH_STRUCT.pack(len(frame)))
        if frame:
            writer.write(frame)
        await writer.drain()
