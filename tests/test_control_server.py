import asyncio

import pytest

from server.control_server import ControlServer
from server.session_manager import SessionManager


class DummyWriter:
    def __init__(self) -> None:
        self.closed = False
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        await asyncio.sleep(0)


class DummyVideoServer:
    def __init__(self) -> None:
        self.removed: list[str] = []

    async def remove_user(self, username: str) -> None:
        self.removed.append(username)


class DummyAudioServer:
    def __init__(self) -> None:
        self.removed: list[str] = []

    async def remove_user(self, username: str) -> None:
        self.removed.append(username)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_force_disconnect_cleans_up_media_servers() -> None:
    manager = SessionManager()
    writer = DummyWriter()
    await manager.register("alice", writer)

    video_server = DummyVideoServer()
    audio_server = DummyAudioServer()
    control_server = ControlServer(
        "127.0.0.1",
        0,
        manager,
        video_server=video_server,
        audio_server=audio_server,
    )

    disconnected = await control_server.force_disconnect("alice")

    assert disconnected is True
    assert writer.closed is True
    assert video_server.removed == ["alice"]
    assert audio_server.removed == ["alice"]
    snapshot = await manager.snapshot()
    assert snapshot["clients"] == []
    assert any(event["type"] == "user_kicked" for event in snapshot["events"])
    assert await manager.is_banned("alice") is True

    with pytest.raises(PermissionError):
        await manager.register("alice", DummyWriter())


@pytest.mark.anyio
async def test_force_disconnect_unknown_user_is_noop() -> None:
    manager = SessionManager()
    control_server = ControlServer("127.0.0.1", 0, manager)

    disconnected = await control_server.force_disconnect("ghost")

    assert disconnected is False
    snapshot = await manager.snapshot()
    assert snapshot["clients"] == []
    assert await manager.is_banned("ghost") is False
