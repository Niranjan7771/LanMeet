import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from client.app import ClientApp, TIME_LIMIT_LEAVE_REASON


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_client_auto_leaves_when_time_limit_expires(monkeypatch):
    app = ClientApp(username=None, server_host="localhost")
    app._connected = True
    leave_mock = AsyncMock()
    monkeypatch.setattr(app, "_leave_session", leave_mock)

    now = time.time()
    status = {
        "is_active": True,
        "is_expired": False,
        "duration_seconds": 60,
        "remaining_seconds": 1,
        "end_timestamp": now + 0.05,
        "started_at": now - 59,
        "updated_at": now,
        "progress": 0.98,
    }

    app._schedule_time_limit_watch(status)

    await asyncio.sleep(0.15)

    assert leave_mock.await_count == 1
    assert leave_mock.await_args.kwargs["reason"] == TIME_LIMIT_LEAVE_REASON

    app._cancel_time_limit_watch()


@pytest.mark.anyio("asyncio")
async def test_client_auto_leaves_when_status_already_expired(monkeypatch):
    app = ClientApp(username=None, server_host="localhost")
    app._connected = True
    leave_mock = AsyncMock()
    monkeypatch.setattr(app, "_leave_session", leave_mock)

    status = {
        "is_active": True,
        "is_expired": True,
        "duration_seconds": 15,
        "remaining_seconds": 0,
        "end_timestamp": time.time() - 1,
        "started_at": time.time() - 16,
        "updated_at": time.time(),
        "progress": 1.0,
    }

    app._schedule_time_limit_watch(status)

    await asyncio.sleep(0.05)

    assert leave_mock.await_count == 1
    assert leave_mock.await_args.kwargs["reason"] == TIME_LIMIT_LEAVE_REASON

    app._cancel_time_limit_watch()
