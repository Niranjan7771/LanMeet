from __future__ import annotations

import asyncio
import logging
import webbrowser
from pathlib import Path
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

logger = logging.getLogger(__name__)


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

    def __init__(self, username: str, server_host: str, tcp_port: int = DEFAULT_TCP_PORT) -> None:
        self._username = username
        self._server_host = server_host
        self._tcp_port = tcp_port
        self._client = ControlClient(
            host=self._server_host,
            port=self._tcp_port,
            username=self._username,
            on_message=self._handle_control_message,
        )
        self._ws_hub = WebSocketHub()
        self._file_client = FileClient(host=self._server_host, port=DEFAULT_FILE_PORT, username=self._username)
        self._screen_publisher = ScreenPublisher(
            username=self._username,
            server_host=self._server_host,
            port=DEFAULT_SCREEN_PORT,
        )
        self._video_client: Optional[VideoClient] = None
        self._audio_client: Optional[AudioClient] = None
        self._media_config: Dict[str, int] = {
            "video_port": DEFAULT_VIDEO_PORT,
            "audio_port": DEFAULT_AUDIO_PORT,
            "screen_port": DEFAULT_SCREEN_PORT,
            "file_port": DEFAULT_FILE_PORT,
        }
        self._peers: List[str] = []
        self._app = FastAPI()
        self._configure_routes()

    def _configure_routes(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        static_dir = project_root / "webui"

        asset_candidates = [static_dir / "assets", project_root / "assets"]
        assets_dir = next((candidate for candidate in asset_candidates if candidate.exists()), None)
        if not assets_dir:
            raise RuntimeError("Unable to locate static assets directory; expected one of: " + ", ".join(str(p) for p in asset_candidates))

        self._app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @self._app.get("/")
        async def index() -> HTMLResponse:
            html_path = static_dir / "index.html"
            return HTMLResponse(html_path.read_text(encoding="utf-8"))

        @self._app.websocket("/ws/control")
        async def ws_control(websocket: WebSocket) -> None:
            await self._ws_hub.connect(websocket)
            try:
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
            try:
                metadata, stream = await self._file_client.download(file_id)
            except FileNotFoundError as exc:  # pragma: no cover - network path
                raise HTTPException(status_code=404, detail=f"File {file_id} not found") from exc

            async def iterator():
                async for chunk in stream:
                    yield chunk

            headers = {
                "Content-Disposition": f"attachment; filename=\"{metadata['filename']}\""
            }
            return StreamingResponse(iterator(), media_type="application/octet-stream", headers=headers)

    async def run(self, host: str = "127.0.0.1", port: int = 8100) -> None:
        import uvicorn

        await self._client.connect()

        config = uvicorn.Config(self._app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        url = f"http://{host}:{port}" if host != "0.0.0.0" else f"http://127.0.0.1:{port}"
        webbrowser.open_new_tab(url)

        await server.serve()

    async def _handle_control_message(self, action: ControlAction, payload: Dict[str, object]) -> None:
        logger.debug("Control action %s payload %s", action, payload)
        if action == ControlAction.PRESENTER_GRANTED and payload.get("username") == self._username:
            await self._screen_publisher.start()
        elif action == ControlAction.PRESENTER_REVOKED and payload.get("username") == self._username:
            await self._screen_publisher.stop()

        if action == ControlAction.WELCOME:
            media = payload.get("media") or {}
            await self._ensure_media_clients(media)
            peers = payload.get("peers", [])
            self._peers = peers
            if self._video_client:
                self._video_client.update_peers(peers)
        elif action == ControlAction.USER_JOINED:
            username = payload.get("username")
            if username and username not in self._peers:
                self._peers.append(username)
                if self._video_client:
                    self._video_client.update_peers(self._peers)
        elif action == ControlAction.USER_LEFT:
            username = payload.get("username")
            if username and username in self._peers:
                self._peers = [peer for peer in self._peers if peer != username]
                if self._video_client:
                    self._video_client.update_peers(self._peers)

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
        if kind == "chat_send":
            message = payload.get("message", "")
            await self._client.send_chat(message)
        elif kind == "request_presenter":
            await self._client.send(ControlAction.PRESENTER_GRANTED, {})
        elif kind == "release_presenter":
            await self._client.send(ControlAction.PRESENTER_REVOKED, {})
        elif kind == "file_request_list":
            await self._client.send(ControlAction.FILE_REQUEST, {"request": "list"})
        elif kind == "file_download":
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
        elif kind == "heartbeat":
            # UI-level heartbeat - ignore for now.
            return
        else:
            logger.warning("Unhandled UI message: %s", data)

    async def _ensure_media_clients(self, media: Dict[str, int]) -> None:
        changed = False
        for key in ("video_port", "audio_port", "screen_port", "file_port"):
            if key in media and media[key] != self._media_config.get(key):
                self._media_config[key] = media[key]
                changed = True

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
