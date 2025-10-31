from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import webbrowser
from typing import Awaitable, Callable, Dict, List, Optional, Set

from fastapi import File, FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from shared.protocol import (
    ControlAction,
    DEFAULT_AUDIO_PORT,
    DEFAULT_FILE_PORT,
    DEFAULT_SCREEN_PORT,
    DEFAULT_TCP_PORT,
    DEFAULT_VIDEO_PORT,
)

from .control_client import ControlClient
from .file_client import FileClient
from .screen_client import ScreenPublisher
from .video_client import VideoClient
from .audio_client import AudioClient
from shared.resource_paths import project_root

logger = logging.getLogger(__name__)

MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB limit
RECONNECT_BASE_DELAY_SECONDS = 2.0
RECONNECT_MAX_DELAY_SECONDS = 30.0
LATENCY_SAMPLE_INTERVAL_SECONDS = 5.0
TIME_LIMIT_LEAVE_REASON = "Meeting time limit reached"


class _LatencyProbeProtocol(asyncio.DatagramProtocol):
    def __init__(self, handler: Callable[[bytes], None]) -> None:
        self._handler = handler

    def datagram_received(self, data: bytes, addr) -> None:  # pragma: no cover - network callback
        try:
            self._handler(data)
        except Exception:
            logger.exception("Latency probe response handler failed")


class LatencyProbe:
    """Background helper to measure UDP round-trip latency to the server."""

    def __init__(
        self,
        username: str,
        server_host: str,
        server_port: int,
        *,
        pre_shared_key: Optional[str] = None,
    interval: float = LATENCY_SAMPLE_INTERVAL_SECONDS,
        on_metrics: Optional[Callable[[float, Optional[float]], Awaitable[None] | None]] = None,
    ) -> None:
        self._username = username
        self._server_host = server_host
        self._server_port = server_port
        self._pre_shared_key = pre_shared_key
        self._interval = max(1.0, interval)
        self._on_metrics = on_metrics
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._sequence = 0
        self._pending: Dict[int, float] = {}
        self._previous_latency: Optional[float] = None

    async def start(self) -> None:
        if self._running:
            return
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _LatencyProbeProtocol(self._handle_packet),
            local_addr=("0.0.0.0", 0),
        )
        self._transport = transport
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.debug("Latency probe started for %s", self._username)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:  # pragma: no cover - task cancellation
                pass
        self._task = None
        if self._transport:
            self._transport.close()
            self._transport = None
        self._pending.clear()
        logger.debug("Latency probe stopped for %s", self._username)

    async def _run(self) -> None:
        try:
            while self._running:
                await self._send_probe()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:  # pragma: no cover - task cancellation
            return

    async def _send_probe(self) -> None:
        if not self._transport:
            return
        self._sequence = (self._sequence + 1) % (2**31)
        timestamp_ms = int(time.time() * 1000)
        payload = {
            "username": self._username,
            "timestamp_ms": timestamp_ms,
            "sequence": self._sequence,
        }
        if self._pre_shared_key:
            payload["pre_shared_key"] = self._pre_shared_key
        message = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._pending[self._sequence] = timestamp_ms
        try:
            self._transport.sendto(message, (self._server_host, self._server_port))
        except Exception:
            logger.exception("Failed to send latency probe")

    def _handle_packet(self, data: bytes) -> None:
        if not self._running:
            return
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.debug("Ignoring malformed latency response")
            return

        sequence = payload.get("sequence")
        if not isinstance(sequence, int):
            return
        sent_timestamp = self._pending.pop(sequence, None)
        if sent_timestamp is None:
            return
        now_ms = int(time.time() * 1000)
        latency_ms = max(0.0, float(now_ms - sent_timestamp))
        jitter_ms: Optional[float] = None
        if self._previous_latency is not None:
            jitter_ms = abs(latency_ms - self._previous_latency)
        self._previous_latency = latency_ms
        if self._on_metrics is None:
            return
        try:
            result = self._on_metrics(latency_ms, jitter_ms)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)
        except Exception:
            logger.exception("Latency metrics callback failed")

class WebSocketHub:
    """Tracks active UI WebSocket connections."""

    def __init__(self) -> None:
        self._connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self._connections:
                self._connections.remove(ws)

    async def broadcast(self, message: Dict[str, object]) -> None:
        async with self._lock:
            for ws in list(self._connections):
                try:
                    if ws.application_state == WebSocketState.CONNECTED:
                        await ws.send_json(message)
                except Exception:
                    logger.exception("Failed to send WebSocket message")


class ClientApp:
    """Client runtime orchestrating control plane and local web UI."""

    def __init__(self, username: Optional[str], server_host: str, tcp_port: int = DEFAULT_TCP_PORT, *, pre_shared_key: Optional[str] = None) -> None:
        self._prefill_username = username
        self._username: Optional[str] = None
        self._server_host = server_host
        self._tcp_port = tcp_port
        self._client: Optional[ControlClient] = None
        self._ws_hub = WebSocketHub()
        self._file_client: Optional[FileClient] = None
        self._screen_publisher: Optional[ScreenPublisher] = None
        self._video_client: Optional[VideoClient] = None
        self._audio_client: Optional[AudioClient] = None
        self._media_config: Dict[str, int] = {
            "video_port": DEFAULT_VIDEO_PORT,
            "audio_port": DEFAULT_AUDIO_PORT,
            "screen_port": DEFAULT_SCREEN_PORT,
            "file_port": DEFAULT_FILE_PORT,
        }
        self._peers: List[str] = []
        self._chat_history: List[Dict[str, object]] = []
        self._file_catalog: Dict[str, Dict[str, object]] = {}
        self._peer_media: Dict[str, Dict[str, bool]] = {}
        self._presenter: Optional[str] = None
        self._audio_enabled = False
        self._video_enabled = False
        self._screen_requested = False
        self._kicked = False
        self._kick_reason = None
        self._connected = False
        self._uvicorn_server = None
        self._app = FastAPI()
        self._pre_shared_key = pre_shared_key
        self._presence: Dict[str, Dict[str, object]] = {}
        self._latency_probe: Optional[LatencyProbe] = None
        self._own_latency: Optional[Dict[str, float]] = None
        self._should_reconnect = False
        self._reconnect_task: Optional[asyncio.Task[None]] = None
        self._reconnect_attempt = 0
        self._reaction_log: List[Dict[str, object]] = []
        self._local_hand_raised = False
        self._latency_probe_port: Optional[int] = None
        self._time_limit: Optional[Dict[str, object]] = None
        self._admin_notices: List[Dict[str, object]] = []
        self._time_limit_exit_triggered = False
        self._time_limit_expiry_task: Optional[asyncio.Task[None]] = None
        self._configure_routes()

    def _configure_routes(self) -> None:
        root = project_root()
        static_dir = root / "webui"

        asset_candidates = [static_dir / "assets", root / "assets"]
        assets_dir = next((candidate for candidate in asset_candidates if candidate.exists()), None)
        if not assets_dir:
            raise RuntimeError("Unable to locate static assets directory; expected one of: " + ", ".join(str(p) for p in asset_candidates))

        self._app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @self._app.get("/")
        async def index() -> HTMLResponse:
            html_path = static_dir / "index.html"
            return HTMLResponse(html_path.read_text(encoding="utf-8"))

        @self._app.get("/api/config")
        async def config() -> Dict[str, object]:
            return {
                "prefill_username": self._prefill_username,
                "server_host": self._server_host,
                "tcp_port": self._tcp_port,
            }

        @self._app.get("/api/random-name")
        async def random_name() -> Dict[str, str]:
            return {"username": self._generate_username()}

        @self._app.websocket("/ws/control")
        async def ws_control(websocket: WebSocket) -> None:
            await self._ws_hub.connect(websocket)
            try:
                await websocket.send_json(
                    {
                        "type": "session_status",
                        "payload": {
                            "state": "connected"
                            if self._connected
                            else ("kicked" if self._kicked else "idle"),
                            "username": self._username,
                            "message": self._kick_reason,
                        },
                    }
                )
                if self._connected:
                    await websocket.send_json(
                        {
                            "type": "state_snapshot",
                            "payload": self._build_snapshot(),
                        }
                    )
                while True:
                    data = await websocket.receive_json()
                    await self._handle_ui_message(data)
            except WebSocketDisconnect:
                pass
            finally:
                await self._ws_hub.disconnect(websocket)

        @self._app.post("/api/files/upload")
        async def upload_file(file: UploadFile = File(...)) -> Dict[str, object]:
            if not file:
                raise HTTPException(status_code=400, detail="File missing")
            if self._file_client is None:
                raise HTTPException(status_code=412, detail="Not connected to collaboration session")

            size_bytes = await self._determine_upload_size(file)
            if size_bytes > MAX_UPLOAD_SIZE_BYTES:
                max_mb = MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)
                raise HTTPException(status_code=413, detail=f"File exceeds {max_mb} MB limit")
            setattr(file, "size", size_bytes)

            async def report_progress(sent: int, total: int) -> None:
                await self._ws_hub.broadcast(
                    {
                        "type": "file_progress",
                        "payload": {
                            "file_id": None,
                            "filename": file.filename,
                            "received": sent,
                            "total_size": total,
                        },
                    }
                )

            file_id = await self._file_client.upload(file, progress=report_progress)
            await self._ws_hub.broadcast(
                {
                    "type": "file_upload_complete",
                    "payload": {
                        "file_id": file_id,
                        "filename": file.filename,
                    },
                }
            )
            return {"status": "ok", "file_id": file_id}

        @self._app.get("/api/files/download/{file_id}")
        async def download_file(file_id: str) -> StreamingResponse:
            if self._file_client is None:
                raise HTTPException(status_code=412, detail="Not connected to collaboration session")
            try:
                metadata, stream = await self._file_client.download(file_id)
            except FileNotFoundError as exc:  # pragma: no cover - network path
                raise HTTPException(status_code=404, detail=f"File {file_id} not found") from exc

            async def iterator():
                async for chunk in stream:
                    yield chunk

            original_name = metadata.get("filename") if isinstance(metadata, dict) else None
            safe_name = self._sanitize_filename(str(original_name or file_id))
            headers = {
                "Content-Disposition": f"attachment; filename=\"{safe_name}\""
            }
            return StreamingResponse(iterator(), media_type="application/octet-stream", headers=headers)

    def _normalize_presence_entry(self, raw: Dict[str, object]) -> Optional[Dict[str, object]]:
        username = raw.get("username")
        if not isinstance(username, str) or not username:
            return None
        entry: Dict[str, object] = {
            "username": username,
            "audio_enabled": bool(raw.get("audio_enabled", False)),
            "video_enabled": bool(raw.get("video_enabled", False)),
            "hand_raised": bool(raw.get("hand_raised", False)),
            "is_typing": bool(raw.get("is_typing", False)),
            "is_presenter": bool(raw.get("is_presenter", False)),
            "last_seen_seconds": float(raw.get("last_seen_seconds", 0.0) or 0.0),
        }
        latency_val = raw.get("latency_ms")
        jitter_val = raw.get("jitter_ms")
        try:
            entry["latency_ms"] = float(latency_val) if latency_val is not None else None
        except (TypeError, ValueError):
            entry["latency_ms"] = None
        try:
            entry["jitter_ms"] = float(jitter_val) if jitter_val is not None else None
        except (TypeError, ValueError):
            entry["jitter_ms"] = None
        entry["is_self"] = username == self._username
        return entry

    def _normalize_time_limit(self, raw: Optional[Dict[str, object]]) -> Optional[Dict[str, object]]:
        if not isinstance(raw, dict):
            return None
        status: Dict[str, object] = {
            "is_active": bool(raw.get("is_active", False)),
            "is_expired": bool(raw.get("is_expired", False)),
        }
        for field in ("duration_seconds", "remaining_seconds"):
            value = raw.get(field)
            if value is None:
                status[field] = None
            else:
                try:
                    status[field] = int(value)
                except (TypeError, ValueError):
                    status[field] = None
        for field in ("end_timestamp", "started_at", "updated_at"):
            value = raw.get(field)
            if value is None:
                status[field] = None
            else:
                try:
                    status[field] = float(value)
                except (TypeError, ValueError):
                    status[field] = None
        progress = raw.get("progress")
        if progress is None:
            status["progress"] = None
        else:
            try:
                status["progress"] = max(0.0, min(1.0, float(progress)))
            except (TypeError, ValueError):
                status["progress"] = None
        return status

    def _normalize_admin_notice(self, raw: Dict[str, object]) -> Optional[Dict[str, object]]:
        if not isinstance(raw, dict):
            return None
        message = str(raw.get("message", "")).strip()
        if not message:
            return None
        notice: Dict[str, object] = {
            "message": message,
            "level": str(raw.get("level", "info")).lower(),
            "actor": str(raw.get("actor", "admin")) or "admin",
        }
        timestamp = raw.get("timestamp")
        try:
            notice["timestamp"] = float(timestamp) if timestamp is not None else time.time()
        except (TypeError, ValueError):
            notice["timestamp"] = time.time()
        return notice

    def _presence_values(self) -> List[Dict[str, object]]:
        return [dict(value) for value in self._presence.values()]

    async def _broadcast_presence_sync(self) -> None:
        await self._ws_hub.broadcast(
            {
                "type": "presence_sync",
                "payload": {
                    "participants": self._presence_values(),
                },
            }
        )

    async def _broadcast_presence_update(self, entry: Dict[str, object]) -> None:
        await self._ws_hub.broadcast(
            {
                "type": "presence_update",
                "payload": dict(entry),
            }
        )

    async def _determine_upload_size(self, upload: UploadFile) -> int:
        """Return the size in bytes of an incoming UploadFile without consuming it."""
        try:
            upload.file.seek(0, os.SEEK_END)
            size = int(upload.file.tell())
            upload.file.seek(0)
            return size
        except (AttributeError, OSError, ValueError):
            size = 0
            chunk_size = 1024 * 1024
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE_BYTES:
                    break
            await upload.seek(0)
            return size

    def _sanitize_filename(self, filename: str) -> str:
        """Ensure filenames used in headers contain only ASCII-safe characters."""
        if not filename:
            return "download.bin"
        safe_chars = []
        for ch in filename:
            code = ord(ch)
            if 32 <= code < 127 and ch not in {'\\', '"'}:
                safe_chars.append(ch)
            else:
                safe_chars.append("_")
        sanitized = "".join(safe_chars).strip()
        return sanitized or "download.bin"

    async def _start_latency_probe(self) -> None:
        if self._username is None:
            await self._stop_latency_probe()
            return
        port_value = self._media_config.get("latency_port")
        if not port_value:
            await self._stop_latency_probe()
            return
        try:
            port = int(port_value)
        except (TypeError, ValueError):
            logger.warning("Invalid latency port %s", port_value)
            await self._stop_latency_probe()
            return
        if self._latency_probe and self._latency_probe_port == port:
            return
        await self._stop_latency_probe()
        probe = LatencyProbe(
            username=self._username,
            server_host=self._server_host,
            server_port=port,
            pre_shared_key=self._pre_shared_key,
            on_metrics=self._on_latency_metrics,
        )
        try:
            await probe.start()
        except Exception:
            logger.exception("Unable to start latency probe")
            return
        self._latency_probe = probe
        self._latency_probe_port = port

    async def _stop_latency_probe(self) -> None:
        if self._latency_probe is None:
            return
        try:
            await self._latency_probe.stop()
        finally:
            self._latency_probe = None
            self._latency_probe_port = None
            self._own_latency = None

    async def _on_latency_metrics(self, latency_ms: float, jitter_ms: Optional[float]) -> None:
        self._own_latency = {
            "latency_ms": latency_ms,
            "jitter_ms": jitter_ms if jitter_ms is not None else None,
        }
        if self._username:
            entry = self._presence.get(self._username)
            if entry is not None:
                entry["latency_ms"] = latency_ms
                entry["jitter_ms"] = jitter_ms
                await self._broadcast_presence_update(entry)
        await self._ws_hub.broadcast(
            {
                "type": "latency_metrics",
                "payload": {
                    "latency_ms": latency_ms,
                    "jitter_ms": jitter_ms,
                },
            }
        )
        if self._client and self._connected:
            try:
                await self._client.send_latency_update(latency_ms, jitter_ms)
            except Exception:
                logger.debug("Latency update send failed", exc_info=True)

    def _cancel_reconnect(self) -> None:
        if self._reconnect_task is None:
            return
        task = self._reconnect_task
        self._reconnect_task = None
        task.cancel()

    def _schedule_reconnect(self, *, immediate: bool = False) -> None:
        if not self._should_reconnect:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        delay = 0.0 if immediate else min(
            RECONNECT_BASE_DELAY_SECONDS * (2 ** self._reconnect_attempt),
            RECONNECT_MAX_DELAY_SECONDS,
        )
        self._reconnect_attempt += 1

        async def _worker(delay_seconds: float) -> None:
            try:
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                if not self._should_reconnect:
                    return
                username = self._username or self._prefill_username
                if not username:
                    logger.info("Reconnect aborted; no username set")
                    return
                await self._start_session(username)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Reconnect attempt failed")
                if self._should_reconnect:
                    self._schedule_reconnect()
            finally:
                self._reconnect_task = None

        self._reconnect_task = asyncio.create_task(_worker(delay))

    async def _on_control_disconnect(self, reason: Optional[str]) -> None:
        if not self._should_reconnect:
            return
        if reason:
            logger.warning("Control connection lost: %s", reason)
        else:
            logger.warning("Control connection lost")
        self._connected = False
        await self._stop_latency_probe()
        self._cancel_time_limit_watch()
        self._time_limit_exit_triggered = False
        await self._broadcast_session_status(
            "reconnecting",
            username=self._username,
            message=reason,
        )
        self._schedule_reconnect()

    def _generate_username(self) -> str:
        adjectives = [
            "swift",
            "bright",
            "lively",
            "bold",
            "stellar",
            "brisk",
            "clever",
        ]
        nouns = [
            "lynx",
            "sparrow",
            "otter",
            "falcon",
            "fox",
            "orca",
            "aurora",
        ]
        return f"{random.choice(adjectives)}-{random.choice(nouns)}-{random.randint(100, 999)}"

    async def _start_session(self, username: str) -> None:
        if self._kicked:
            await self._broadcast_session_status(
                "kicked",
                username=username,
                message=self._kick_reason or "An administrator removed you from this meeting.",
            )
            return
        await self._broadcast_session_status("connecting", username=username)
        self._should_reconnect = True
        self._cancel_reconnect()
        await self._stop_latency_probe()
        await self._stop_media_clients()
        if self._client:
            await self._client.close()
        self._cancel_time_limit_watch()
        self._time_limit_exit_triggered = False
        self._time_limit = None
        self._username = username
        self._prefill_username = username
        self._connected = False
        self._client = ControlClient(
            host=self._server_host,
            port=self._tcp_port,
            username=username,
            on_message=self._handle_control_message,
            pre_shared_key=self._pre_shared_key,
            on_disconnect=self._on_control_disconnect,
        )
        self._file_client = FileClient(host=self._server_host, port=self._media_config["file_port"], username=username)
        self._screen_publisher = ScreenPublisher(
            username=username,
            server_host=self._server_host,
            port=self._media_config["screen_port"],
        )
        self._audio_enabled = False
        self._video_enabled = False
        self._screen_requested = False
        self._local_hand_raised = False
        self._peer_media = {
            username: {
                "audio_enabled": self._audio_enabled,
                "video_enabled": self._video_enabled,
            }
        }
        self._presence[username] = {
            "username": username,
            "audio_enabled": self._audio_enabled,
            "video_enabled": self._video_enabled,
            "hand_raised": self._local_hand_raised,
            "is_typing": False,
            "is_presenter": False,
            "latency_ms": None,
            "jitter_ms": None,
            "last_seen_seconds": 0.0,
            "is_self": True,
        }
        await self._broadcast_presence_update(self._presence[username])
        try:
            await self._client.connect()
            self._connected = True
            self._reconnect_attempt = 0
            await self._broadcast_session_status("connected", username=username)
            await self._start_latency_probe()
        except Exception as exc:
            if self._kicked:
                await self._stop_media_clients()
                if self._client:
                    await self._client.close()
                self._client = None
                self._file_client = None
                self._screen_publisher = None
                self._connected = False
                return
            await self._broadcast_session_status("error", message=str(exc))
            await self._stop_media_clients()
            if self._client:
                await self._client.close()
            self._client = None
            self._file_client = None
            self._screen_publisher = None
            self._username = None
            self._connected = False
            self._schedule_reconnect()
            return

    async def _stop_media_clients(self) -> None:
        tasks: List[asyncio.Future[None]] = []
        if self._video_client:
            tasks.append(asyncio.create_task(self._video_client.stop()))
        if self._audio_client:
            tasks.append(asyncio.create_task(self._audio_client.stop()))
        if self._screen_publisher:
            tasks.append(asyncio.create_task(self._screen_publisher.stop()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._video_client = None
        self._audio_client = None
        self._screen_publisher = None
        self._screen_requested = False
        self._peer_media = {}
        await self._stop_latency_probe()

    async def _stop_ui_server(self) -> None:
        server = self._uvicorn_server
        if not server:
            return
        if getattr(server, "should_exit", False):
            return
        server.should_exit = True
        await asyncio.sleep(0)

    async def _broadcast_session_status(self, state: str, **payload: object) -> None:
        payload_data = dict(payload)
        payload_data.setdefault("username", self._username or self._prefill_username)
        await self._ws_hub.broadcast(
            {
                "type": "session_status",
                "payload": {
                    "state": state,
                    **payload_data,
                },
            }
        )

    def _build_snapshot(self) -> Dict[str, object]:
        files = [dict(file) for file in self._file_catalog.values()]
        chat_history = [dict(message) for message in self._chat_history]
        return {
            "connected": self._connected,
            "username": self._username,
            "peers": list(self._peers),
            "chat_history": chat_history,
            "files": files,
            "presenter": self._presenter,
            "media": {
                "audio_enabled": self._audio_enabled,
                "video_enabled": self._video_enabled,
                "screen_requested": self._screen_requested,
            },
            "peer_media": {
                peer: dict(state) for peer, state in self._peer_media.items()
            },
            "presence": self._presence_values(),
            "latency": dict(self._own_latency) if self._own_latency else None,
            "hand_raised": self._local_hand_raised,
            "reactions": [dict(item) for item in self._reaction_log[-50:]],
            "time_limit": dict(self._time_limit) if self._time_limit else None,
            "admin_notices": [dict(item) for item in self._admin_notices[-20:]],
        }

    def _cancel_time_limit_watch(self) -> None:
        task = self._time_limit_expiry_task
        if task is None:
            return
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if task is current:
            self._time_limit_expiry_task = None
            return
        if not task.done():
            task.cancel()
        self._time_limit_expiry_task = None

    def _compute_time_limit_delay(self, status: Optional[Dict[str, object]]) -> Optional[float]:
        if not status or not status.get("is_active"):
            return None
        now = time.time()
        end_ts = status.get("end_timestamp")
        if isinstance(end_ts, (int, float)):
            return max(0.0, float(end_ts) - now)
        remaining = status.get("remaining_seconds")
        if isinstance(remaining, (int, float)):
            return max(0.0, float(remaining))
        duration = status.get("duration_seconds")
        started_at = status.get("started_at")
        if isinstance(duration, (int, float)) and isinstance(started_at, (int, float)):
            return max(0.0, float(started_at + duration - now))
        return None

    def _schedule_time_limit_watch(self, status: Optional[Dict[str, object]]) -> None:
        self._cancel_time_limit_watch()
        if not status or not status.get("is_active"):
            self._time_limit_exit_triggered = False
            return
        delay = self._compute_time_limit_delay(status)
        is_expired = bool(status.get("is_expired"))
        if is_expired or (delay is not None and delay <= 0.0):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            task = loop.create_task(self._handle_time_limit_expired())
            task.add_done_callback(lambda _: setattr(self, "_time_limit_expiry_task", None))
            self._time_limit_expiry_task = task
            return
        if delay is None:
            return
        self._time_limit_exit_triggered = False

        async def _wait_and_leave() -> None:
            try:
                await asyncio.sleep(delay)
                await self._handle_time_limit_expired()
            except asyncio.CancelledError:
                return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(_wait_and_leave())
        task.add_done_callback(lambda _: setattr(self, "_time_limit_expiry_task", None))
        self._time_limit_expiry_task = task

    async def _handle_time_limit_expired(self) -> None:
        if self._time_limit_exit_triggered:
            return
        self._time_limit_exit_triggered = True
        if not self._connected and not self._client:
            return
        await self._leave_session(reason=TIME_LIMIT_LEAVE_REASON)

    def _set_audio_enabled(self, enabled: bool) -> None:
        self._audio_enabled = enabled
        if self._audio_client:
            self._audio_client.set_capture_enabled(enabled)
        self._update_local_media_state(audio_enabled=enabled)

    def _set_video_enabled(self, enabled: bool) -> None:
        self._video_enabled = enabled
        if self._video_client:
            self._video_client.set_capture_enabled(enabled)
        self._update_local_media_state(video_enabled=enabled)

    def _update_local_media_state(
        self,
        *,
        audio_enabled: Optional[bool] = None,
        video_enabled: Optional[bool] = None,
    ) -> None:
        if self._username is None:
            return
        entry = self._peer_media.setdefault(
            self._username,
            {
                "audio_enabled": False,
                "video_enabled": False,
            },
        )
        if audio_enabled is not None:
            entry["audio_enabled"] = audio_enabled
        if video_enabled is not None:
            entry["video_enabled"] = video_enabled
        presence_entry = self._presence.get(self._username)
        if presence_entry is not None:
            if audio_enabled is not None:
                presence_entry["audio_enabled"] = audio_enabled
            if video_enabled is not None:
                presence_entry["video_enabled"] = video_enabled
            asyncio.create_task(self._broadcast_presence_update(presence_entry))

    async def run(self, host: str = "127.0.0.1", port: int = 8100) -> None:
        import uvicorn

        config = uvicorn.Config(self._app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        self._uvicorn_server = server
        url = f"http://{host}:{port}" if host != "0.0.0.0" else f"http://127.0.0.1:{port}"
        webbrowser.open_new_tab(url)

        self._connected = False
        await self._broadcast_session_status("idle")
        try:
            await server.serve()
        finally:
            self._uvicorn_server = None
            self._kicked = False
            self._kick_reason = None

    async def _handle_control_message(self, action: ControlAction, payload: Dict[str, object]) -> None:
        logger.debug("Control action %s payload %s", action, payload)
        if action == ControlAction.PRESENTER_GRANTED:
            username = payload.get("username")
            if username == self._username:
                self._screen_requested = True
                if self._screen_publisher:
                    await self._screen_publisher.start()
            else:
                self._screen_requested = False
            self._presenter = username
        elif action == ControlAction.PRESENTER_REVOKED:
            username = payload.get("username")
            if username == self._username:
                self._screen_requested = False
                if self._screen_publisher:
                    await self._screen_publisher.stop()
            if self._presenter == username:
                self._presenter = None

        if action == ControlAction.WELCOME:
            media = payload.get("media") or {}
            await self._ensure_media_clients(media)
            peers = payload.get("peers", [])
            self._peers = [peer for peer in peers if isinstance(peer, str)]
            if self._video_client:
                self._video_client.update_peers(self._peers)
            chat_history = payload.get("chat_history") or []
            self._chat_history = [dict(message) for message in chat_history if isinstance(message, dict)]
            files = payload.get("files") or []
            self._file_catalog = {}
            for file in files:
                if isinstance(file, dict) and file.get("file_id"):
                    file_copy = dict(file)
                    self._file_catalog[file_copy["file_id"]] = file_copy
            self._presenter = payload.get("presenter")
            raw_media_state = payload.get("media_state")
            media_state = raw_media_state if isinstance(raw_media_state, dict) else {}
            refreshed_peer_media: dict[str, dict[str, bool]] = {}
            for peer, state in media_state.items():
                if isinstance(peer, str) and isinstance(state, dict):
                    refreshed_peer_media[peer] = {
                        "audio_enabled": bool(state.get("audio_enabled")),
                        "video_enabled": bool(state.get("video_enabled")),
                    }
            if self._username:
                refreshed_peer_media.setdefault(
                    self._username,
                    {
                        "audio_enabled": self._audio_enabled,
                        "video_enabled": self._video_enabled,
                    },
                )
            self._peer_media = refreshed_peer_media
            for peer in self._peers:
                if isinstance(peer, str):
                    self._peer_media.setdefault(
                        peer,
                        {
                            "audio_enabled": False,
                            "video_enabled": False,
                        },
                    )
            presence_items = payload.get("presence") or []
            if isinstance(presence_items, list):
                self._presence.clear()
                for item in presence_items:
                    if isinstance(item, dict):
                        normalized = self._normalize_presence_entry(item)
                        if normalized:
                            self._presence[normalized["username"]] = normalized
            time_limit_payload = self._normalize_time_limit(payload.get("time_limit"))
            self._time_limit = time_limit_payload
            payload["time_limit"] = time_limit_payload
            self._schedule_time_limit_watch(time_limit_payload)
            if self._admin_notices:
                payload["admin_notices"] = [dict(item) for item in self._admin_notices[-10:]]
            if self._username:
                entry = self._presence.get(self._username)
                if entry is None:
                    entry = self._normalize_presence_entry({"username": self._username}) or {
                        "username": self._username,
                        "audio_enabled": self._audio_enabled,
                        "video_enabled": self._video_enabled,
                        "hand_raised": self._local_hand_raised,
                        "is_typing": False,
                        "is_presenter": False,
                        "latency_ms": None,
                        "jitter_ms": None,
                        "last_seen_seconds": 0.0,
                        "is_self": True,
                    }
                    self._presence[self._username] = entry
                entry["audio_enabled"] = self._audio_enabled
                entry["video_enabled"] = self._video_enabled
                entry["hand_raised"] = self._local_hand_raised
                entry["is_self"] = True
                if entry.get("latency_ms") is not None:
                    self._own_latency = {
                        "latency_ms": float(entry["latency_ms"]),
                        "jitter_ms": entry.get("jitter_ms"),
                    }
            await self._broadcast_presence_sync()
            await self._start_latency_probe()
        elif action == ControlAction.USER_JOINED:
            username = payload.get("username")
            participants = payload.get("participants")
            if isinstance(participants, list):
                self._peers = [peer for peer in participants if isinstance(peer, str)]
            elif username and username not in self._peers:
                self._peers.append(username)
            if self._video_client:
                self._video_client.update_peers(self._peers)
            if self._username:
                self._peer_media.setdefault(
                    self._username,
                    {
                        "audio_enabled": self._audio_enabled,
                        "video_enabled": self._video_enabled,
                    },
                )
            if isinstance(username, str):
                self._peer_media.setdefault(
                    username,
                    {
                        "audio_enabled": False,
                        "video_enabled": False,
                    },
                )
        elif action == ControlAction.USER_LEFT:
            username = payload.get("username")
            participants = payload.get("participants")
            if isinstance(participants, list):
                self._peers = [peer for peer in participants if isinstance(peer, str)]
            elif username and username in self._peers:
                self._peers = [peer for peer in self._peers if peer != username]
            if self._video_client:
                self._video_client.update_peers(self._peers)
            if self._presenter == username:
                self._presenter = None
            if self._username:
                self._peer_media.setdefault(
                    self._username,
                    {
                        "audio_enabled": self._audio_enabled,
                        "video_enabled": self._video_enabled,
                    },
                )
            if isinstance(username, str):
                self._peer_media.pop(username, None)
                self._presence.pop(username, None)
                await self._broadcast_presence_sync()
        elif action == ControlAction.CHAT_MESSAGE:
            message = {
                "sender": payload.get("sender"),
                "message": payload.get("message"),
                "timestamp_ms": payload.get("timestamp_ms"),
            }
            # Preserve recipients if present (for UI rendering and local snapshot)
            if isinstance(payload.get("recipients"), list):
                message["recipients"] = [
                    str(x).strip() for x in payload.get("recipients") if isinstance(x, str) and str(x).strip()
                ]
            self._chat_history.append(message)
            if len(self._chat_history) > 200:
                self._chat_history.pop(0)
        elif action == ControlAction.FILE_OFFER:
            if payload.get("files"):
                for file in payload["files"]:
                    if isinstance(file, dict) and file.get("file_id"):
                        file_copy = dict(file)
                        self._file_catalog[file_copy["file_id"]] = file_copy
            elif payload.get("file_id"):
                file_copy = dict(payload)
                self._file_catalog[file_copy["file_id"]] = file_copy
        elif action == ControlAction.VIDEO_STATUS:
            username = payload.get("username")
            if isinstance(username, str):
                entry = self._peer_media.setdefault(
                    username,
                    {
                        "audio_enabled": False,
                        "video_enabled": False,
                    },
                )
                if "audio_enabled" in payload:
                    entry["audio_enabled"] = bool(payload.get("audio_enabled"))
                if "video_enabled" in payload:
                    entry["video_enabled"] = bool(payload.get("video_enabled"))
                if username == self._username and "video_enabled" in payload:
                    self._video_enabled = bool(payload.get("video_enabled"))
        elif action == ControlAction.AUDIO_STATUS:
            username = payload.get("username")
            if isinstance(username, str):
                entry = self._peer_media.setdefault(
                    username,
                    {
                        "audio_enabled": False,
                        "video_enabled": False,
                    },
                )
                if "audio_enabled" in payload:
                    entry["audio_enabled"] = bool(payload.get("audio_enabled"))
                if username == self._username and "audio_enabled" in payload:
                    self._audio_enabled = bool(payload.get("audio_enabled"))
                presence_entry = self._presence.get(username)
                if presence_entry is not None and "audio_enabled" in payload:
                    presence_entry["audio_enabled"] = bool(payload.get("audio_enabled"))
                    await self._broadcast_presence_update(presence_entry)
        elif action == ControlAction.KICKED:
            reason = str(payload.get("reason") or "An administrator removed you from this meeting.")
            self._kicked = True
            self._kick_reason = reason
            self._connected = False
            await self._stop_media_clients()
            if self._client:
                await self._client.close()
            self._client = None
            self._file_client = None
            self._screen_publisher = None
            self._peers = []
            self._chat_history.clear()
            self._file_catalog.clear()
            self._peer_media.clear()
            self._presenter = None
            self._cancel_time_limit_watch()
            self._time_limit_exit_triggered = False
            self._time_limit = None
            await self._broadcast_session_status("kicked", message=reason)
            await self._stop_ui_server()
        elif action == ControlAction.PRESENCE_SYNC:
            participants = payload.get("participants")
            if isinstance(participants, list):
                self._presence.clear()
                for item in participants:
                    if isinstance(item, dict):
                        normalized = self._normalize_presence_entry(item)
                        if normalized:
                            self._presence[normalized["username"]] = normalized
                await self._broadcast_presence_sync()
        elif action == ControlAction.PRESENCE_UPDATE:
            normalized = self._normalize_presence_entry(payload)
            if normalized:
                self._presence[normalized["username"]] = normalized
                if normalized["username"] == self._username:
                    self._local_hand_raised = bool(normalized.get("hand_raised", False))
                    latency_ms = normalized.get("latency_ms")
                    if latency_ms is not None:
                        self._own_latency = {
                            "latency_ms": float(latency_ms),
                            "jitter_ms": normalized.get("jitter_ms"),
                        }
                await self._broadcast_presence_update(normalized)
        elif action == ControlAction.TYPING_STATUS:
            username = payload.get("username")
            is_typing = bool(payload.get("is_typing", False))
            entry = self._presence.get(username) if isinstance(username, str) else None
            if entry is not None:
                entry["is_typing"] = is_typing
                await self._broadcast_presence_update(entry)
        elif action == ControlAction.HAND_STATUS:
            username = payload.get("username")
            hand_raised = bool(payload.get("hand_raised", False))
            entry = self._presence.get(username) if isinstance(username, str) else None
            if entry is not None:
                entry["hand_raised"] = hand_raised
                if username == self._username:
                    self._local_hand_raised = hand_raised
                await self._broadcast_presence_update(entry)
        elif action == ControlAction.REACTION:
            reaction = {
                "username": payload.get("username"),
                "reaction": payload.get("reaction"),
                "timestamp_ms": payload.get("timestamp_ms"),
            }
            self._reaction_log.append(reaction)
            if len(self._reaction_log) > 200:
                self._reaction_log.pop(0)
        elif action == ControlAction.LATENCY_UPDATE:
            username = payload.get("username")
            entry = self._presence.get(username) if isinstance(username, str) else None
            if entry is not None:
                entry["latency_ms"] = payload.get("latency_ms")
                entry["jitter_ms"] = payload.get("jitter_ms")
                if username == self._username:
                    self._own_latency = {
                        "latency_ms": payload.get("latency_ms"),
                        "jitter_ms": payload.get("jitter_ms"),
                    }
                await self._broadcast_presence_update(entry)
        elif action == ControlAction.TIME_LIMIT_UPDATE:
            normalized = self._normalize_time_limit(payload)
            self._time_limit = normalized
            payload = normalized or {}
            self._schedule_time_limit_watch(normalized)
        elif action == ControlAction.ADMIN_NOTICE:
            notice = self._normalize_admin_notice(payload)
            if notice:
                self._admin_notices.append(notice)
                if len(self._admin_notices) > 100:
                    self._admin_notices = self._admin_notices[-100:]
                payload = notice
            else:
                payload = {}

        await self._ws_hub.broadcast(
            {
                "type": action.value,
                "payload": payload,
            }
        )

    async def _handle_ui_message(self, data: Dict[str, object]) -> None:
        """Handle messages coming from the web UI via WebSocket."""

        kind = data.get("type")
        payload = data.get("payload", {})
        if kind == "join":
            if self._kicked:
                await self._broadcast_session_status(
                    "kicked",
                    message=self._kick_reason or "An administrator removed you from this meeting.",
                )
                return
            username = str(payload.get("username") or self._generate_username())
            try:
                await self._start_session(username)
            except Exception:
                logger.exception("Failed to establish collaboration session")
        elif kind == "chat_send":
            if not self._client:
                return
            message = payload.get("message", "")
            raw_recipients = payload.get("recipients")
            recipients: Optional[List[str]] = None
            if isinstance(raw_recipients, list):
                recipients = [str(x).strip() for x in raw_recipients if isinstance(x, str) and str(x).strip()]
                if not recipients:
                    recipients = None
            await self._client.send_chat(message, recipients=recipients)
        elif kind == "request_presenter":
            if not self._client:
                return
            await self._client.send(ControlAction.PRESENTER_GRANTED, {})
        elif kind == "release_presenter":
            if not self._client:
                return
            await self._client.send(ControlAction.PRESENTER_REVOKED, {})
        elif kind == "file_request_list":
            if not self._client:
                return
            await self._client.send(ControlAction.FILE_REQUEST, {"request": "list"})
        elif kind == "file_download":
            if not self._client:
                return
            file_id = payload.get("file_id")
            if file_id:
                await self._ws_hub.broadcast(
                    {
                        "type": "file_download_ready",
                        "payload": {
                            "file_id": file_id,
                            "url": f"/api/files/download/{file_id}",
                        },
                    }
                )
        elif kind == "toggle_audio":
            if not self._client:
                return
            enabled = bool(payload.get("enabled", False))
            self._set_audio_enabled(enabled)
            await self._client.send(ControlAction.AUDIO_STATUS, {"audio_enabled": enabled})
        elif kind == "toggle_video":
            if not self._client:
                return
            enabled = bool(payload.get("enabled", False))
            self._set_video_enabled(enabled)
            await self._client.send(ControlAction.VIDEO_STATUS, {"video_enabled": enabled})
        elif kind == "typing":
            if not self._client or self._username is None:
                return
            is_typing = bool(payload.get("is_typing", False))
            await self._client.send_typing(is_typing)
            entry = self._presence.get(self._username)
            if entry is not None:
                entry["is_typing"] = is_typing
                asyncio.create_task(self._broadcast_presence_update(entry))
        elif kind == "toggle_hand":
            if not self._client or self._username is None:
                return
            desired = bool(payload.get("hand_raised", False))
            self._local_hand_raised = desired
            await self._client.send_hand_status(desired)
            entry = self._presence.get(self._username)
            if entry is not None:
                entry["hand_raised"] = desired
                asyncio.create_task(self._broadcast_presence_update(entry))
        elif kind == "send_reaction":
            if not self._client:
                return
            reaction = str(payload.get("reaction", "")).strip()
            if not reaction:
                return
            await self._client.send_reaction(reaction)
        elif kind == "copy_file_link":
            file_id = payload.get("file_id")
            if isinstance(file_id, str) and file_id:
                await self._ws_hub.broadcast(
                    {
                        "type": "file_share_link",
                        "payload": {
                            "file_id": file_id,
                            "url": f"/api/files/download/{file_id}",
                        },
                    }
                )
        elif kind == "toggle_presentation":
            desired = bool(payload.get("enabled", False))
            if not self._client:
                return
            if desired:
                await self._client.send(ControlAction.PRESENTER_GRANTED, {})
                self._screen_requested = True
            else:
                await self._client.send(ControlAction.PRESENTER_REVOKED, {})
                self._screen_requested = False
        elif kind == "leave_session":
            reason = payload.get("reason")
            if isinstance(reason, str) and reason.strip():
                await self._leave_session(reason=reason.strip())
            else:
                await self._leave_session(reason=TIME_LIMIT_LEAVE_REASON if payload.get("auto") else None)
        elif kind == "heartbeat":
            # UI-level heartbeat - ignore for now.
            return
        else:
            logger.warning("Unhandled UI message: %s", data)

    async def _leave_session(self, *, reason: Optional[str] = None) -> None:
        if not self._client and not self._connected:
            await self._broadcast_session_status("idle")
            await self._stop_ui_server()
            return

        username = self._username
        self._should_reconnect = False
        self._cancel_reconnect()
        self._cancel_time_limit_watch()
        self._time_limit_exit_triggered = False
        await self._broadcast_session_status("disconnecting", username=username, message=reason)

        await self._stop_media_clients()
        await self._stop_latency_probe()

        if self._client:
            try:
                await self._client.close()
            except Exception:
                logger.exception("Error while closing control client")
            finally:
                self._client = None

        self._file_client = None
        self._screen_publisher = None
        self._video_client = None
        self._audio_client = None
        self._connected = False
        self._audio_enabled = False
        self._video_enabled = False
        self._screen_requested = False
        self._peers = []
        self._chat_history = []
        self._file_catalog = {}
        self._presenter = None
        self._presence.clear()
        self._reaction_log.clear()
        self._own_latency = None
        self._local_hand_raised = False
        self._time_limit = None

        if username:
            self._prefill_username = username
            await self._ws_hub.broadcast(
                {
                    "type": ControlAction.USER_LEFT.value,
                    "payload": {"username": username},
                }
            )

        self._username = None
        await self._broadcast_session_status("idle")
        await self._stop_ui_server()

    async def _ensure_media_clients(self, media: Dict[str, int]) -> None:
        if self._username is None:
            return
        changed = False
        for key in ("video_port", "audio_port", "screen_port", "file_port", "latency_port"):
            if key in media and media[key] != self._media_config.get(key):
                self._media_config[key] = media[key]
                changed = True

        if changed:
            # Recreate helpers tied to port numbers.
            if self._file_client is not None:
                self._file_client = FileClient(
                    host=self._server_host,
                    port=self._media_config["file_port"],
                    username=self._username,
                )
            if self._screen_publisher is not None:
                await self._screen_publisher.stop()
                self._screen_publisher = ScreenPublisher(
                    username=self._username,
                    server_host=self._server_host,
                    port=self._media_config["screen_port"],
                )

        if self._video_client is None or changed:
            if self._video_client is not None:
                await self._video_client.stop()
            try:
                video_port = self._media_config.get("video_port", DEFAULT_VIDEO_PORT)
                self._video_client = VideoClient(
                    username=self._username,
                    server_host=self._server_host,
                    server_port=video_port,
                    on_frame=self._handle_video_frame,
                )
                await self._video_client.start()
                self._video_client.update_peers(self._peers)
                self._video_client.set_capture_enabled(self._video_enabled)
            except Exception:  # pragma: no cover - hardware dependent
                logger.exception("Unable to start video client")
                self._video_client = None

        if self._audio_client is None or changed:
            if self._audio_client is not None:
                await self._audio_client.stop()
            try:
                audio_port = self._media_config.get("audio_port", DEFAULT_AUDIO_PORT)
                self._audio_client = AudioClient(
                    username=self._username,
                    server_host=self._server_host,
                    server_port=audio_port,
                )
                await self._audio_client.start()
                self._audio_client.set_capture_enabled(self._audio_enabled)
            except Exception:  # pragma: no cover - hardware dependent
                logger.exception("Unable to start audio client")
                self._audio_client = None

        await self._start_latency_probe()

    async def _handle_video_frame(self, username: str, frame_b64: str) -> None:
        await self._ws_hub.broadcast(
            {
                "type": "video_frame",
                "payload": {
                    "username": username,
                    "frame": frame_b64,
                },
            }
        )
