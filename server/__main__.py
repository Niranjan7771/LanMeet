from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import webbrowser

from pathlib import Path

from shared.resource_paths import resolve_path

from shared.protocol import (
    DEFAULT_AUDIO_PORT,
    DEFAULT_FILE_PORT,
    DEFAULT_SCREEN_PORT,
    DEFAULT_TCP_PORT,
    DEFAULT_VIDEO_PORT,
)

from server.control_server import ControlServer
from server.audio_server import AudioServer
from server.file_server import FileServer
from server.screen_server import ScreenServer
from server.video_server import VideoServer
from server.session_manager import SessionManager
from server.admin_dashboard import AdminServer
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
    parser.add_argument("--admin-host", default="127.0.0.1", help="Host for the admin dashboard server")
    parser.add_argument("--admin-port", type=int, default=8700, help="Port for the admin dashboard server")
    parser.add_argument(
        "--admin-static",
        type=Path,
        default=resolve_path("adminui"),
        help="Path to admin dashboard static assets",
    )
    parser.add_argument(
        "--open-dashboard",
        action="store_true",
        help="Open the admin dashboard in a browser after startup",
    )
    parser.add_argument(
        "--no-open-dashboard",
        action="store_true",
        help="Suppress automatic dashboard launch",
    )
    parser.add_argument(
        "--log-level",
        type=str.upper,
        default="INFO",
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"],
        help="Logging verbosity",
    )
    args = parser.parse_args()

    open_dashboard = args.open_dashboard
    if not args.open_dashboard and not args.no_open_dashboard and len(sys.argv) == 1:
        open_dashboard = True

    logging.basicConfig(
        level=getattr(logging, args.log_level, logging.INFO),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )

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

    async def request_shutdown() -> None:
        if stop_event.is_set():
            return
        logger.info("Admin initiated shutdown")
        stop_event.set()
        await file_server.cleanup_storage()

    admin_server = AdminServer(
        session_manager,
        host=args.admin_host,
        port=args.admin_port,
        static_root=args.admin_static,
        kick_handler=control_server.force_disconnect,
        shutdown_handler=request_shutdown,
    )

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
    await admin_server.start()

    if open_dashboard:
        dashboard_url = f"http://{args.admin_host}:{args.admin_port}"
        # Give the server a moment to accept connections before opening the browser.
        await asyncio.sleep(0.5)
        webbrowser.open_new_tab(dashboard_url)

    heartbeat_task = asyncio.create_task(session_manager.heartbeat_watcher())

    await stop_event.wait()

    logger.info("Shutdown signal processed; stopping services")

    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        pass

    await control_server.stop()
    await screen_server.stop()
    await file_server.stop()
    await video_server.stop()
    await audio_server.stop()
    await admin_server.stop()

    logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
