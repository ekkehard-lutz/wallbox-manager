"""Session retirement tests adapted from pinned lbbrhzn/ocpp regressions.

Source: tests/test_reconnect_lifecycle.py and test_initial_start_lifecycle.py,
848407c11ff659ce59779a99ce69984bbb0e3ce1. Copyright (c) 2021 lbbrhzn, MIT.
See custom_components/wallbox_manager/THIRD_PARTY_NOTICES.md.
"""

import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest
from websockets.exceptions import ConnectionClosedOK

from custom_components.wallbox_manager.core.models import StationId
from custom_components.wallbox_manager.protocols.ocpp.common.sessions import Session
from custom_components.wallbox_manager.protocols.ocpp.common.transport import (
    CentralSystem,
)
from custom_components.wallbox_manager.runtime import Runtime


class Socket:
    subprotocol = "ocpp1.6"
    request = SimpleNamespace(path="/a")

    def __init__(self):
        self.ended = asyncio.Event()
        self.started = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_release.set()
        self.closes = 0
        self.close_error = None

    async def recv(self):
        self.started.set()
        await self.ended.wait()
        raise ConnectionClosedOK(None, None)

    async def close(self):
        self.closes += 1
        self.close_entered.set()
        await self.close_release.wait()
        self.ended.set()
        if self.close_error:
            raise self.close_error


async def ticks():
    for _ in range(30):
        await asyncio.sleep(0)


@pytest.mark.parametrize("explicit_stop", [False, True])
async def test_overlapping_admission_latest_wins(explicit_stop):
    server = CentralSystem(Runtime(), "127.0.0.1", 0)
    old, first, latest = Socket(), Socket(), Socket()
    tasks = [asyncio.create_task(server.on_connect(old))]
    try:
        await asyncio.wait_for(old.started.wait(), 1)
        old.close_release.clear()
        tasks.append(asyncio.create_task(server.on_connect(first)))
        await old.close_entered.wait()
        tasks.append(
            asyncio.create_task(
                server.stop() if explicit_stop else server.on_connect(latest)
            )
        )
        await ticks()
        old.close_release.set()
        await ticks()
        assert first.ended.is_set()
        assert old.closes == 1
        if explicit_stop:
            assert not server.runtime.get(StationId("a")).connected
        else:
            await asyncio.wait_for(latest.started.wait(), 1)
            assert server.sessions[StationId("a")].connection is latest
            assert not latest.ended.is_set()
    finally:
        old.close_release.set()
        await server.stop()
        await asyncio.gather(*tasks)


async def test_stale_finalizer_cannot_close_new_session(monkeypatch):
    server = CentralSystem(Runtime(), "127.0.0.1", 0)
    old, new = Socket(), Socket()
    entered, release = asyncio.Event(), asyncio.Event()
    original = Session.stop

    async def paused(self):
        if asyncio.current_task().get_name() == "old-run":
            entered.set()
            await release.wait()
        await original(self)

    monkeypatch.setattr(Session, "stop", paused)
    old_runner = asyncio.create_task(server.on_connect(old), name="old-run")
    new_runner = None
    try:
        await asyncio.wait_for(old.started.wait(), 1)
        new_runner = asyncio.create_task(server.on_connect(new))
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(new.started.wait(), 1)
        release.set()
        await old_runner
        assert not new.ended.is_set()
        assert server.runtime.get(StationId("a")).connected
    finally:
        release.set()
        await server.stop()
        await asyncio.gather(*(t for t in (old_runner, new_runner) if t))


async def test_shared_stop_survives_repeated_waiter_cancellation():
    socket = Socket()
    session = Session(socket)
    session.spawn(socket.recv())
    await socket.started.wait()
    socket.close_release.clear()
    first = asyncio.create_task(session.stop())
    await socket.close_entered.wait()
    second = asyncio.create_task(session.stop())
    for _ in range(2):
        first.cancel()
        await ticks()
    socket.close_release.set()
    await asyncio.gather(first, second, return_exceptions=True)
    assert first.cancelled()
    assert second.exception() is None
    assert socket.closes == 1
    assert not session.tasks


async def test_retirement_timeout_tracks_surviving_child():
    socket = Socket()
    session = Session(socket, timeout=0.02)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hostile():
        entered.set()
        while not release.is_set():
            with suppress(asyncio.CancelledError):
                await release.wait()
        raise RuntimeError("late survivor failure")

    child = session.spawn(hostile())
    await entered.wait()
    try:
        with pytest.raises(TimeoutError):
            await session.stop()
        assert child in session.retirement_tasks
        with pytest.raises(TimeoutError):
            await session.stop()
    finally:
        release.set()
        await asyncio.gather(child, return_exceptions=True)
        await ticks()
    assert not session.retirement_tasks


async def test_close_failure_can_be_retried():
    socket = Socket()
    session = Session(socket)
    socket.close_error = OSError("close failed")
    with pytest.raises(OSError):
        await session.stop()
    socket.close_error = None
    await session.stop()
    assert socket.closes == 2


async def test_adapter_initialization_failure_leaves_station_offline(monkeypatch):
    from custom_components.wallbox_manager.protocols.ocpp.common.transport import (
        ADAPTERS,
    )

    def fail(*args):
        raise ValueError("adapter initialization failed")

    monkeypatch.setitem(ADAPTERS, "ocpp1.6", fail)
    server = CentralSystem(Runtime(), "127.0.0.1", 0)
    socket = Socket()
    await server.on_connect(socket)
    assert socket.ended.is_set()
    assert not server.runtime.get(StationId("a")).connected
    assert not server.sessions
