from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import webbrowser

from pathlib import Path
from typing import Optional

from shared.resource_paths import resolve_path

from shared.protocol import (
    DEFAULT_AUDIO_PORT,
    DEFAULT_FILE_PORT,
    DEFAULT_LATENCY_PORT,
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
from server.latency_server import LatencyServer
logger = logging.getLogger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(description="LAN collaboration suite server")
    parser.add_argument("--host", default="0.0.0.0", help="Host/IP to bind the control server")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help="TCP control port")
    parser.add_argument("--screen-port", type=int, default=DEFAULT_SCREEN_PORT, help="TCP screen sharing port")
    parser.add_argument("--video-port", type=int, default=DEFAULT_VIDEO_PORT, help="UDP video port")
    parser.add_argument("--audio-port", type=int, default=DEFAULT_AUDIO_PORT, help="UDP audio port")
    parser.add_argument("--latency-port", type=int, default=DEFAULT_LATENCY_PORT, help="UDP latency probe port")
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
    parser.add_argument("--pre-shared-key", type=str, default=None, help="Optional pre-shared key required for clients")
    parser.add_argument("--log-file", type=Path, default=None, help="Optional path to a rotating log file")
    parser.add_argument("--log-max-bytes", type=int, default=5 * 1024 * 1024, help="Max size of the log file before rotation")
    parser.add_argument("--log-backup-count", type=int, default=5, help="Number of rotated log files to retain")
    args = parser.parse_args()

    open_dashboard = args.open_dashboard
    if not args.open_dashboard and not args.no_open_dashboard and len(sys.argv) == 1:
        open_dashboard = True

    log_handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        from logging.handlers import RotatingFileHandler

        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            args.log_file,
            maxBytes=max(1024, args.log_max_bytes),
            backupCount=max(1, args.log_backup_count),
        )
        file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
        log_handlers.append(file_handler)

    logging.basicConfig(
        level=getattr(logging, args.log_level, logging.INFO),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        handlers=log_handlers,
        force=True,
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
            "latency_port": args.latency_port,
        },
        pre_shared_key=args.pre_shared_key,
        latency_port=args.latency_port,
    )
    screen_server = ScreenServer(args.host, args.screen_port, session_manager)
    latency_server = LatencyServer(pre_shared_key=args.pre_shared_key)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    shutdown_requested = False
    shutdown_reason = "Server shutting down"

    def trigger_shutdown(source: str, reason: Optional[str] = None) -> bool:
        nonlocal shutdown_requested, shutdown_reason
        if shutdown_requested:
            logger.debug("Shutdown already in progress (source=%s)", source)
            return False
        shutdown_requested = True
        if reason:
            shutdown_reason = reason
        logger.info("%s initiated shutdown", source)
        def _emit_shutdown_mark() -> None:
            asyncio.create_task(session_manager.mark_shutdown_requested(reason=shutdown_reason))

        try:
            loop.call_soon_threadsafe(stop_event.set)
            loop.call_soon_threadsafe(_emit_shutdown_mark)
        except RuntimeError:
            stop_event.set()
            try:
                asyncio.get_running_loop().create_task(
                    session_manager.mark_shutdown_requested(reason=shutdown_reason)
                )
            except RuntimeError:
                pass
        return True

    async def request_shutdown() -> bool:
        reason = "Server shutting down by administrator"
        return trigger_shutdown("Admin dashboard", reason)

    admin_server = AdminServer(
        session_manager,
        host=args.admin_host,
        port=args.admin_port,
        static_root=args.admin_static,
        kick_handler=control_server.force_disconnect,
        shutdown_handler=request_shutdown,
    )

    def _signal_handler() -> None:
        trigger_shutdown("Shutdown signal")

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
    await latency_server.start(args.host, args.latency_port)
    await admin_server.start()

    if open_dashboard:
        dashboard_url = f"http://{args.admin_host}:{args.admin_port}"
        # Give the server a moment to accept connections before opening the browser.
        await asyncio.sleep(0.5)
        webbrowser.open_new_tab(dashboard_url)

    heartbeat_task = asyncio.create_task(session_manager.heartbeat_watcher())


    await stop_event.wait()

    logger.info("Shutdown signal processed; stopping services")

    try:
        await session_manager.disconnect_all(reason=shutdown_reason)
    except Exception:
        logger.exception("Failed to disconnect participants during shutdown")

    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        pass

    try:
        await control_server.stop()
    except Exception:
        logger.exception("Error stopping control server")

    try:
        await screen_server.stop()
    except Exception:
        logger.exception("Error stopping screen server")

    try:
        await file_server.stop()
    except Exception:
        logger.exception("Error stopping file server")

    try:
        await video_server.stop()
    except Exception:
        logger.exception("Error stopping video server")

    try:
        await audio_server.stop()
    except Exception:
        logger.exception("Error stopping audio server")

    try:
        await latency_server.stop()
    except Exception:
        logger.exception("Error stopping latency server")

    try:
        await admin_server.stop()
    except Exception:
        logger.exception("Error stopping admin server")

    try:
        await file_server.cleanup_storage()
    except Exception:
        logger.exception("Failed to clear temporary storage during shutdown")

    logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
