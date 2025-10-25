from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .session_manager import SessionManager
from shared.protocol import ControlAction
from shared.resource_paths import resolve_path

logger = logging.getLogger(__name__)


_LOG_BUFFER_LIMIT = 200
_log_buffer = deque(maxlen=_LOG_BUFFER_LIMIT)


class _InMemoryLogHandler(logging.Handler):
    """Collect recent log records for admin diagnostics."""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - logging side effect
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        _log_buffer.append(
            {
                "message": message,
                "level": record.levelname.lower(),
                "logger": record.name,
                "timestamp": record.created,
            }
        )


def _ensure_log_handler() -> None:
    root_logger = logging.getLogger()
    if any(isinstance(handler, _InMemoryLogHandler) for handler in root_logger.handlers):
        return
    handler = _InMemoryLogHandler(level=logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root_logger.addHandler(handler)


def _get_log_tail(limit: int = 50) -> list[dict[str, object]]:
    if limit <= 0:
        return []
    slice_len = min(limit, len(_log_buffer))
    if slice_len == 0:
        return []
    return list(_log_buffer)[-slice_len:]


_ensure_log_handler()


class AdminDashboard:
    """FastAPI application exposing admin insights for the collaboration server."""

    def __init__(
        self,
        session_manager: SessionManager,
        *,
        static_root: Optional[Path] = None,
        shutdown_handler: Optional[Callable[[], Awaitable[bool]]] = None,
        kick_handler: Optional[Callable[[str], Awaitable[bool]]] = None,
    ) -> None:
        self._session_manager = session_manager
        self._app = FastAPI()
        self._static_root = static_root or resolve_path("adminui")
        assets_dir = self._static_root / "assets"
        self._shutdown_handler = shutdown_handler
        self._kick_handler = kick_handler
        self._storage_cache: Optional[tuple[float, dict[str, object]]] = None
        if assets_dir.exists():
            self._app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
        else:
            logger.warning("Admin dashboard assets not found at %s", assets_dir)

        @self._app.get("/")
        async def index() -> HTMLResponse:
            html_path = self._static_root / "index.html"
            if not html_path.exists():
                raise HTTPException(status_code=404, detail="Admin dashboard assets missing")
            return HTMLResponse(html_path.read_text(encoding="utf-8"))

        @self._app.get("/api/state")
        async def state() -> dict:
            snapshot = await self._session_manager.snapshot()
            snapshot["timestamp"] = time.time()
            snapshot["storage_usage"] = self._storage_usage()
            snapshot["log_tail"] = _get_log_tail(40)
            snapshot["health"] = {
                "status": "ok",
                "participant_count": snapshot.get("participant_count", 0),
                "timestamp": snapshot["timestamp"],
            }
            return snapshot

        @self._app.get("/api/health")
        async def health() -> dict:
            snapshot = await self._session_manager.snapshot()
            return {
                "status": "ok",
                "participant_count": snapshot.get("participant_count", 0),
                "timestamp": time.time(),
            }

        @self._app.post("/api/actions/time-limit")
        async def configure_time_limit(payload: dict = Body(...)) -> dict:
            raw_duration = payload.get("duration_minutes")
            actor = str(payload.get("actor") or "admin") or "admin"
            duration: Optional[float]
            if raw_duration is None:
                duration = None
            else:
                try:
                    duration = float(raw_duration)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(status_code=400, detail="duration_minutes must be numeric") from exc
                if duration <= 0:
                    duration = None
            start_timestamp: Optional[float] = None
            if duration is not None:
                if payload.get("start_now"):
                    start_timestamp = time.time()
                else:
                    raw_start = payload.get("start_timestamp")
                    if raw_start is not None:
                        try:
                            start_timestamp = float(raw_start)
                        except (TypeError, ValueError) as exc:
                            raise HTTPException(status_code=400, detail="start_timestamp must be numeric") from exc
            status_payload = await self._session_manager.set_time_limit(
                duration_minutes=duration,
                start_timestamp=start_timestamp,
                actor=actor,
            )
            await self._session_manager.broadcast(ControlAction.TIME_LIMIT_UPDATE, status_payload)
            logger.info(
                "Admin updated time limit (duration_minutes=%s, actor=%s, start_timestamp=%s)",
                duration,
                actor,
                start_timestamp,
            )
            return {"status": "ok", "time_limit": status_payload}

        @self._app.post("/api/actions/notice")
        async def broadcast_notice(payload: dict = Body(...)) -> dict:
            message = str(payload.get("message", "")).strip()
            if not message:
                raise HTTPException(status_code=400, detail="message is required")
            level = str(payload.get("level", "info")).lower()
            if level not in {"info", "warning", "error", "success"}:
                level = "info"
            actor = str(payload.get("actor") or "admin") or "admin"
            notice = await self._session_manager.record_admin_notice(message, level=level, actor=actor)
            await self._session_manager.broadcast(ControlAction.ADMIN_NOTICE, notice)
            logger.info("Admin broadcast notice level=%s actor=%s", level, actor)
            return {"status": "ok", "notice": notice}

        @self._app.post("/api/actions/kick")
        async def kick(payload: dict = Body(...)) -> dict:
            username = str(payload.get("username", "")).strip()
            if not username:
                raise HTTPException(status_code=400, detail="username required")

            if self._kick_handler is not None:
                removed = await self._kick_handler(username)
            else:
                removed = await self._session_manager.unregister(
                    username,
                    event_type="user_kicked",
                    details={"actor": "admin"},
                )
            if not removed:
                raise HTTPException(status_code=404, detail=f"{username} is not connected")

            logger.info("Admin removed client %s", username)
            return {"status": "ok"}

        @self._app.post("/api/actions/shutdown")
        async def shutdown() -> dict:
            if self._shutdown_handler is None:
                raise HTTPException(status_code=503, detail="shutdown handler not configured")
            initiated = await self._shutdown_handler()
            logger.info("Admin requested server shutdown (initiated=%s)", initiated)
            return {
                "status": "ok" if initiated else "in_progress",
                "initiated": initiated,
            }

        @self._app.get("/api/export/events")
        async def export_events() -> JSONResponse:
            events = await self._session_manager.get_recent_events(limit=600)
            response = JSONResponse(events)
            response.headers["Content-Disposition"] = "attachment; filename=\"session-events.json\""
            return response

    @property
    def app(self) -> FastAPI:
        return self._app

    def _storage_usage(self) -> dict[str, object]:
        now = time.time()
        cache_entry = self._storage_cache
        if cache_entry and (now - cache_entry[0]) < 15.0:
            return dict(cache_entry[1])
        stats = self._calculate_storage_usage()
        self._storage_cache = (now, stats)
        return dict(stats)

    def _calculate_storage_usage(self) -> dict[str, object]:
        storage_root = resolve_path("server_storage")
        total_bytes = 0
        file_count = 0
        if storage_root.exists():
            for entry in storage_root.rglob("*"):
                try:
                    if entry.is_file():
                        file_count += 1
                        total_bytes += entry.stat().st_size
                except (OSError, PermissionError):  # pragma: no cover - filesystem guard
                    continue
        return {
            "bytes": total_bytes,
            "files": file_count,
            "path": str(storage_root),
        }


class AdminServer:
    """Background task helper for running the admin FastAPI server."""

    def __init__(
        self,
        session_manager: SessionManager,
        *,
        host: str,
        port: int,
    static_root: Optional[Path] = None,
    shutdown_handler: Optional[Callable[[], Awaitable[bool]]] = None,
        kick_handler: Optional[Callable[[str], Awaitable[bool]]] = None,
    ) -> None:
        self._dashboard = AdminDashboard(
            session_manager,
            static_root=static_root,
            shutdown_handler=shutdown_handler,
            kick_handler=kick_handler,
        )
        self._host = host
        self._port = port
        self._server: Optional[object] = None
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        import uvicorn

        if self._server is not None:
            return
        config = uvicorn.Config(self._dashboard.app, host=self._host, port=self._port, log_level="info")
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        logger.info("Admin dashboard available at http://%s:%s", self._host, self._port)

    async def stop(self) -> None:
        if self._server is None:
            return
        assert self._task is not None
        self._server.should_exit = True
        await self._task
        self._server = None
        self._task = None