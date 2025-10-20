from __future__ import annotations

import asyncio
import logging
import os
import random
import webbrowser
from typing import Dict, List, Optional

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

    def __init__(self, username: Optional[str], server_host: str, tcp_port: int = DEFAULT_TCP_PORT) -> None:
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
        await self._stop_media_clients()
        if self._client:
            await self._client.close()
        self._username = username
        self._prefill_username = username
        self._connected = False
        self._client = ControlClient(
            host=self._server_host,
            port=self._tcp_port,
            username=username,
            on_message=self._handle_control_message,
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
        self._peer_media = {
            username: {
                "audio_enabled": self._audio_enabled,
                "video_enabled": self._video_enabled,
            }
        }
        try:
            await self._client.connect()
            self._connected = True
            await self._broadcast_session_status("connected", username=username)
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
            raise

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
        }

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
        elif action == ControlAction.CHAT_MESSAGE:
            message = {
                "sender": payload.get("sender"),
                "message": payload.get("message"),
                "timestamp_ms": payload.get("timestamp_ms"),
            }
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
            await self._broadcast_session_status("kicked", message=reason)
            await self._stop_ui_server()

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
            await self._client.send_chat(message)
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
            await self._leave_session(reason="auto" if payload.get("auto") else None)
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
        await self._broadcast_session_status("disconnecting", username=username, message=reason)

        await self._stop_media_clients()

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
        for key in ("video_port", "audio_port", "screen_port", "file_port"):
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
