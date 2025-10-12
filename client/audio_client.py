from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from typing import Optional, Tuple

import numpy as np
import sounddevice as sd

from shared.protocol import MEDIA_HEADER_STRUCT, MediaFrameHeader, PayloadType

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_SAMPLES = int(SAMPLE_RATE * 0.02)  # 20ms


class _AudioProtocol(asyncio.DatagramProtocol):
    def __init__(self, client: "AudioClient") -> None:
        self._client = client

    def connection_made(self, transport: asyncio.BaseTransport) -> None:  # pragma: no cover - UDP callback
        self._client._on_transport_ready(transport)

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:  # pragma: no cover - UDP callback
        self._client._on_datagram(data)


class AudioClient:
    """Handles microphone capture and speaker playback over UDP."""

    def __init__(self, username: str, server_host: str, server_port: int) -> None:
        self._username = username
        self._server_host = server_host
        self._server_port = server_port
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._capture_stream: Optional[sd.InputStream] = None
        self._play_stream: Optional[sd.OutputStream] = None
        self._play_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=32)
        self._sequence = 0
        self._running = threading.Event()
        self._capture_enabled = False

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._loop.create_datagram_endpoint(lambda: _AudioProtocol(self), local_addr=("0.0.0.0", 0))
        self._running.set()
        self._start_streams()
        self._register()

    async def stop(self) -> None:
        self._running.clear()
        self._capture_enabled = False
        if self._capture_stream:
            self._capture_stream.stop()
            self._capture_stream.close()
            self._capture_stream = None
        if self._play_stream:
            self._play_stream.stop()
            self._play_stream.close()
            self._play_stream = None
        if self._transport:
            self._transport.close()
            self._transport = None

    def _start_streams(self) -> None:
        self._capture_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=FRAME_SAMPLES,
            callback=self._capture_callback,
        )
        self._play_stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=FRAME_SAMPLES,
            callback=self._playback_callback,
        )
        self._capture_stream.start()
        self._play_stream.start()

    def _capture_callback(self, indata, frames, time_info, status) -> None:  # pragma: no cover - audio callback
        if (
            not self._running.is_set()
            or self._transport is None
            or self._loop is None
            or not self._capture_enabled
        ):
            return
        if status:
            logger.warning("Audio input status: %s", status)
        samples = np.array(indata, dtype=np.float32).flatten()
        payload = samples.tobytes()
        header = MediaFrameHeader(
            stream_id=1,
            sequence_number=self._next_sequence(),
            timestamp_ms=0.0,
            payload_type=PayloadType.AUDIO.value,
        ).pack()
        datagram = header + payload
        self._loop.call_soon_threadsafe(self._transport.sendto, datagram, (self._server_host, self._server_port))

    def _playback_callback(self, outdata, frames, time_info, status) -> None:  # pragma: no cover - audio callback
        if status:
            logger.warning("Audio output status: %s", status)
        try:
            chunk = self._play_queue.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            return
        samples = np.frombuffer(chunk, dtype=np.float32)
        required = frames * CHANNELS
        if samples.size < required:
            padded = np.zeros(required, dtype=np.float32)
            padded[: samples.size] = samples
        else:
            padded = samples[:required]
        outdata[:] = padded.reshape(frames, CHANNELS)

    def _on_transport_ready(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]
        self._register()

    def _register(self) -> None:
        if not self._transport:
            return
        payload = json.dumps(
            {
                "action": "register",
                "username": self._username,
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
                "frame_samples": FRAME_SAMPLES,
            }
        ).encode("utf-8")
        self._loop and self._loop.call_soon_threadsafe(
            self._transport.sendto, payload, (self._server_host, self._server_port)
        )

    def _on_datagram(self, data: bytes) -> None:
        if len(data) < MEDIA_HEADER_STRUCT.size:
            return
        header = MediaFrameHeader.unpack(data[: MEDIA_HEADER_STRUCT.size])
        if header.payload_type != PayloadType.AUDIO.value:
            return
        payload = data[MEDIA_HEADER_STRUCT.size :]
        try:
            self._play_queue.put_nowait(payload)
        except queue.Full:
            # Drop audio if queue is full
            pass

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) % (2**31)
        return self._sequence

    def set_capture_enabled(self, enabled: bool) -> None:
        """Enable or disable microphone capture without stopping playback."""
        self._capture_enabled = enabled
