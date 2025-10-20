import asyncio

import pytest

from server.session_manager import SessionManager
from shared.protocol import ChatMessage


class DummyWriter:
    def __init__(self) -> None:
        self.closed = False

    def write(self, data: bytes) -> None:  # pragma: no cover - not needed for test assertions
        pass

    async def drain(self) -> None:  # pragma: no cover - compatibility shim
        await asyncio.sleep(0)

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
    await manager.record_received("alice", 2048)
    chat = ChatMessage(sender="alice", message="hello", timestamp_ms=123_000)
    await manager.add_chat_message(chat)
    await manager.grant_presenter("alice")

    snapshot = await manager.snapshot()

    assert snapshot["presenter"] == "alice"
    assert any(event["type"] == "user_joined" for event in snapshot["events"])
    assert any(event["type"] == "chat_message" for event in snapshot["events"])
    assert snapshot["clients"][0]["username"] == "alice"
    assert snapshot["clients"][0]["is_presenter"] is True
    assert snapshot["clients"][0]["connection_type"] == "tcp"
    assert snapshot["clients"][0]["bytes_received"] >= 2048
    assert snapshot["clients"][0]["bandwidth_bps"] >= 0
    assert snapshot["participant_count"] == 1
    assert "alice" in snapshot["participant_usernames"]
    assert snapshot["banned_usernames"] == []

    await manager.unregister("alice")
    snapshot_after = await manager.snapshot()
    assert snapshot_after["clients"] == []
    assert any(event["type"] == "user_left" for event in snapshot_after["events"])
    assert snapshot_after["participant_count"] == 0
    assert snapshot_after["participant_usernames"] == []
    assert snapshot_after["banned_usernames"] == []


@pytest.mark.anyio
async def test_unregister_records_custom_event() -> None:
    manager = SessionManager()
    writer = DummyWriter()

    await manager.register("bob", writer)
    await manager.unregister("bob", event_type="user_kicked", details={"actor": "admin"})

    snapshot = await manager.snapshot()
    assert any(event["type"] == "user_kicked" for event in snapshot["events"])


@pytest.mark.anyio
async def test_ban_user_prevents_registration() -> None:
    manager = SessionManager()
    writer = DummyWriter()

    await manager.register("carol", writer)
    await manager.unregister("carol")
    await manager.ban_user("carol")

    assert await manager.is_banned("carol") is True

    with pytest.raises(PermissionError):
        await manager.register("carol", DummyWriter())
