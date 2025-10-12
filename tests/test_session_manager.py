import asyncio

import pytest

from server.session_manager import SessionManager
from shared.protocol import ChatMessage


class DummyWriter:
    def __init__(self) -> None:
        self.closed = False

    def write(self, data: bytes) -> None:  # pragma: no cover - not needed for test assertions
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:  # pragma: no cover - compatibility shim
        await asyncio.sleep(0)

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.mark.anyio
async def test_session_manager_snapshot_tracks_events() -> None:
    manager = SessionManager()
    writer = DummyWriter()

    await manager.register("alice", writer)  # registers user_joined event
    chat = ChatMessage(sender="alice", message="hello", timestamp_ms=123_000)
    await manager.add_chat_message(chat)
    await manager.grant_presenter("alice")

    snapshot = await manager.snapshot()

    assert snapshot["presenter"] == "alice"
    assert any(event["type"] == "user_joined" for event in snapshot["events"])
    assert any(event["type"] == "chat_message" for event in snapshot["events"])
    assert snapshot["clients"][0]["username"] == "alice"
    assert snapshot["clients"][0]["is_presenter"] is True

    await manager.unregister("alice")
    snapshot_after = await manager.snapshot()
    assert snapshot_after["clients"] == []
    assert any(event["type"] == "user_left" for event in snapshot_after["events"])
