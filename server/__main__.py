from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from pathlib import Path

from shared.protocol import (
    DEFAULT_AUDIO_PORT,
    DEFAULT_FILE_PORT,
    DEFAULT_SCREEN_PORT,
    DEFAULT_TCP_PORT,
    DEFAULT_VIDEO_PORT,
)

from .control_server import ControlServer
from .audio_server import AudioServer
from .file_server import FileServer
from .screen_server import ScreenServer
from .video_server import VideoServer
from .session_manager import SessionManager

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(description="LAN collaboration suite server")
    parser.add_argument("--host", default="0.0.0.0", help="Host/IP to bind the control server")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help="TCP control port")
    parser.add_argument("--screen-port", type=int, default=DEFAULT_SCREEN_PORT, help="TCP screen sharing port")
    parser.add_argument("--video-port", type=int, default=DEFAULT_VIDEO_PORT, help="UDP video port")
    parser.add_argument("--audio-port", type=int, default=DEFAULT_AUDIO_PORT, help="UDP audio port")
    parser.add_argument("--file-port", type=int, default=DEFAULT_FILE_PORT, help="TCP file transfer port")
    parser.add_argument(
        "--storage-dir",
        type=Path,
        default=Path("server_storage"),
        help="Directory for temporary file storage",
    )
    args = parser.parse_args()

    session_manager = SessionManager()
    file_server = FileServer(args.host, args.file_port, args.storage_dir, session_manager)
    video_server = VideoServer(session_manager)
    audio_server = AudioServer(session_manager)
    control_server = ControlServer(
        args.host,
        args.tcp_port,
        session_manager,
        file_server=file_server,
        video_server=video_server,
        audio_server=audio_server,
        media_config={
            "video_port": args.video_port,
            "audio_port": args.audio_port,
            "screen_port": args.screen_port,
            "file_port": args.file_port,
        },
    )
    screen_server = ScreenServer(args.host, args.screen_port, session_manager)

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Signals aren't implemented on Windows for ProactorEventLoop; fallback to keyboard interrupt.
            pass

    await control_server.start()
    await screen_server.start()
    await file_server.start()
    await video_server.start(args.host, args.video_port)
    await audio_server.start(args.host, args.audio_port)

    heartbeat_task = asyncio.create_task(session_manager.heartbeat_watcher())

    await stop_event.wait()

    heartbeat_task.cancel()
    await control_server.stop()
    await screen_server.stop()
    await file_server.stop()
    await video_server.stop()
    await audio_server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
