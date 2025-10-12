from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .session_manager import SessionManager

logger = logging.getLogger(__name__)


class AdminDashboard:
    """FastAPI application exposing admin insights for the collaboration server."""

    def __init__(self, session_manager: SessionManager, *, static_root: Optional[Path] = None) -> None:
        self._session_manager = session_manager
        self._app = FastAPI()
        self._static_root = static_root or Path(__file__).resolve().parent.parent / "adminui"
        assets_dir = self._static_root / "assets"
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
            return snapshot

    @property
    def app(self) -> FastAPI:
        return self._app


class AdminServer:
    """Background task helper for running the admin FastAPI server."""

    def __init__(self, session_manager: SessionManager, *, host: str, port: int, static_root: Optional[Path] = None) -> None:
        self._dashboard = AdminDashboard(session_manager, static_root=static_root)
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