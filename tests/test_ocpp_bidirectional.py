"""Library-to-library discovery with a station-owned report worker."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from ocpp.routing import after, on
from ocpp.v21 import ChargePoint as ChargePoint21
from ocpp.v201 import ChargePoint as ChargePoint201
from test_ocpp_transport import inventory, state_when
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from custom_components.wallbox_manager.core.capabilities import EvidenceState
from custom_components.wallbox_manager.core.events import StationIdentity
from custom_components.wallbox_manager.core.models import StationId
from custom_components.wallbox_manager.protocols.ocpp.common.transport import (
    CentralSystem,
)
from custom_components.wallbox_manager.runtime import Runtime


class Station:
    def __init__(self, connection):
        super().__init__("station-a", connection, response_timeout=1)
        self.reports = asyncio.Queue()
        self.acked = asyncio.Event()
        self.requests = []
        self.acknowledged = []
        self.emit_report = True
        self.first_request = asyncio.Event()
        self.release_response = asyncio.Event()
        self.release_response.set()

    @on("GetBaseReport")
    async def get_base_report(self, request_id, report_base):
        self.requests.append(request_id)
        self.first_request.set()
        await self.release_response.wait()
        return self._call_result.GetBaseReport(status="Accepted")

    @after("GetBaseReport")
    def queue_report(self, request_id, report_base):
        if self.emit_report:
            self.reports.put_nowait(request_id)

    async def report_worker(self):
        while True:
            request_id = await self.reports.get()
            await self.call(
                self._call.NotifyReport(
                    request_id=request_id,
                    generated_at="2026-09-21T00:00:00Z",
                    seq_no=0,
                    tbc=False,
                    report_data=inventory(),
                ),
                suppress=False,
            )
            self.acknowledged.append(request_id)
            self.acked.set()

    async def boot(self):
        return await self.call(
            self._call.BootNotification(
                charging_station={"vendor_name": "Test", "model": "Station"},
                reason="PowerUp",
            ),
            suppress=False,
        )


class Station21(Station, ChargePoint21):
    pass


class Station201(Station, ChargePoint201):
    pass


@asynccontextmanager
async def paired(protocol="ocpp2.1", known=False):
    runtime = Runtime()
    if known:
        token = runtime.connect(StationId("station-a"))
        runtime.boot(token, StationIdentity())
    server = await CentralSystem(runtime, "127.0.0.1", 0, response_timeout=0.3).start()
    try:
        async with connect(
            f"ws://127.0.0.1:{server.port}/station-a",
            subprotocols=[protocol],
            proxy=None,
        ) as ws:
            station = (Station21 if protocol == "ocpp2.1" else Station201)(ws)
            tasks = [
                asyncio.create_task(station.start()),
                asyncio.create_task(station.report_worker()),
            ]
            try:
                yield server, station
            finally:
                for task in tasks:
                    task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    assert isinstance(
                        result, (asyncio.CancelledError, ConnectionClosed)
                    )
    finally:
        await server.stop()
        assert all(
            not s.tasks and not s.retirement_tasks for s in server.sessions.values()
        )


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_library_discovery(caplog, protocol):
    async with paired(protocol) as (server, station):
        assert (await station.boot()).status == "Accepted"
        await asyncio.wait_for(station.acked.wait(), 2)
        state = await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        assert len(state.connectors) == 3
        await asyncio.sleep(0.4)
        assert server.runtime.get(StationId("station-a")) == state
        assert state.connected
        assert state.token.connection_generation == state.token.boot_generation == 1
        adapter = server.sessions[state.token.station].adapter
        assert adapter._response_queue.empty()
        assert not adapter._call_lock.locked()
        assert station.acknowledged == [1]
        assert station.requests == [1]
        assert "unknown unique id" not in caplog.text


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_reconnect_boot_while_discovery_call_pending(caplog, protocol):
    async with paired(protocol, known=True) as (server, station):
        station.release_response.clear()
        await asyncio.wait_for(station.first_request.wait(), 1)
        boot = asyncio.create_task(station.boot())
        try:
            await state_when(server.runtime, lambda s: s.token.boot_generation == 2)
            station.release_response.set()
            assert (await boot).status == "Accepted"
            state = await state_when(
                server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
            )
            await asyncio.wait_for(station.acked.wait(), 1)
            await asyncio.sleep(0.4)
            assert station.acknowledged == station.requests == [1, 2]
            assert server.runtime.get(state.token.station) == state
            assert state.connected
            assert state.token.connection_generation == state.token.boot_generation == 2
            assert "unknown unique id" not in caplog.text
        finally:
            station.release_response.set()
            boot.cancel()
            await asyncio.gather(boot, return_exceptions=True)


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_reverse_call_before_get_base_report_response(protocol):
    async with paired(protocol) as (server, station):
        station.emit_report = False
        station.release_response.clear()
        await station.boot()
        await asyncio.wait_for(station.first_request.wait(), 1)
        adapter = server.sessions[StationId("station-a")].adapter
        report = adapter._report
        reverse = asyncio.create_task(
            station.call(
                station._call.NotifyReport(
                    request_id=report.request_id,
                    generated_at="2026-09-21T00:00:00Z",
                    seq_no=0,
                    tbc=False,
                    report_data=inventory(),
                ),
                suppress=False,
            )
        )
        try:
            # The CSMS dispatches NotifyReport even while GetBaseReport.call()
            # is still waiting for Accepted. The station's delayed handler is
            # released afterwards so its reader can consume the reverse ACK.
            await asyncio.wait_for(report.complete.wait(), 1)
            assert adapter._call_lock.locked()
            station.release_response.set()
            await asyncio.wait_for(reverse, 1)
            state = await state_when(
                server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
            )
            assert state.connected and len(state.connectors) == 3
        finally:
            station.release_response.set()
            reverse.cancel()
            await asyncio.gather(reverse, return_exceptions=True)


async def test_session_stop_joins_shielded_pending_call():
    async with paired() as (server, station):
        station.release_response.clear()
        await station.boot()
        await asyncio.wait_for(station.first_request.wait(), 1)
        session = server.sessions[StationId("station-a")]
        assert session.adapter._call_lock.locked()
        await server.stop()
        assert not session.tasks and not session.retirement_tasks
        assert not session.adapter._call_lock.locked()
        assert not server.runtime.get(StationId("station-a")).connected
