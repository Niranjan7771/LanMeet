from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from shared.protocol import ChatMessage, ControlAction, encode_control_message

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT = 10.0  # seconds


@dataclass(slots=True)
class ConnectedClient:
    username: str
    writer: asyncio.StreamWriter
    last_seen: float = field(default_factory=lambda: time.monotonic())
    is_presenter: bool = False

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    def send(self, action: ControlAction, data: Dict[str, object]) -> None:
        payload = encode_control_message(action, data)
        self.writer.write(payload)


class SessionManager:
    """Coordinates connected clients and manages broadcasts."""

    def __init__(self) -> None:
        self._clients: Dict[str, ConnectedClient] = {}
        self._lock = asyncio.Lock()
        self._presenter: Optional[str] = None
        self._chat_history: list[ChatMessage] = []

    async def register(self, username: str, writer: asyncio.StreamWriter) -> ConnectedClient:
        async with self._lock:
            if username in self._clients:
                raise ValueError(f"Username '{username}' already connected")
            client = ConnectedClient(username=username, writer=writer)
            self._clients[username] = client
            logger.info("Registered client %s", username)
            return client

    async def unregister(self, username: str) -> None:
        async with self._lock:
            client = self._clients.pop(username, None)
            if client:
                if self._presenter == username:
                    self._presenter = None
                try:
                    client.writer.close()
                except Exception:  # pragma: no cover - cleanup best effort
                    logger.exception("Error while closing writer for %s", username)
                logger.info("Unregistered client %s", username)

    async def grant_presenter(self, username: str) -> bool:
        async with self._lock:
            if username not in self._clients:
                return False
            if self._presenter == username:
                return True
            if self._presenter is not None and self._presenter != username:
                return False
            self._presenter = username
            self._clients[username].is_presenter = True
            return True

    async def revoke_presenter(self, username: str) -> None:
        async with self._lock:
            if self._presenter == username:
                self._presenter = None
            client = self._clients.get(username)
            if client:
                client.is_presenter = False

    async def get_presenter(self) -> Optional[str]:
        async with self._lock:
            return self._presenter

    async def is_presenter(self, username: str) -> bool:
        async with self._lock:
            return self._presenter == username

    async def get_client(self, username: str) -> Optional[ConnectedClient]:
        async with self._lock:
            return self._clients.get(username)

    async def broadcast(self, action: ControlAction, data: Dict[str, object], *, exclude: Optional[Set[str]] = None) -> None:
        if exclude is None:
            exclude = set()
        async with self._lock:
            for username, client in self._clients.items():
                if username in exclude:
                    continue
                try:
                    client.send(action, data)
                except Exception:
                    logger.exception("Failed to queue message to %s", username)

    async def send_to(self, username: str, action: ControlAction, data: Dict[str, object]) -> None:
        async with self._lock:
            client = self._clients.get(username)
            if client is None:
                return
            try:
                client.send(action, data)
            except Exception:
                logger.exception("Failed to send direct message to %s", username)

    async def add_chat_message(self, chat: ChatMessage) -> None:
        async with self._lock:
            self._chat_history.append(chat)
            if len(self._chat_history) > 200:
                self._chat_history.pop(0)

    async def get_chat_history(self) -> list[ChatMessage]:
        async with self._lock:
            return list(self._chat_history)

    async def list_clients(self) -> list[str]:
        async with self._lock:
            return list(self._clients.keys())

    async def heartbeat_watcher(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_TIMEOUT)
            stale: list[str] = []
            async with self._lock:
                now = time.monotonic()
                for username, client in list(self._clients.items()):
                    if now - client.last_seen > HEARTBEAT_TIMEOUT * 2:
                        stale.append(username)
                for username in stale:
                    logger.warning("Client %s timed out", username)
                    self._clients.pop(username, None)
            for username in stale:
                await self.broadcast(ControlAction.USER_LEFT, {"username": username})

    async def mark_heartbeat(self, username: str) -> None:
        async with self._lock:
            client = self._clients.get(username)
            if client:
                client.touch()
