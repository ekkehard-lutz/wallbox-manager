"""NETZ resume uses current applied evidence or independent authorization."""

import pytest
from conftest import eventually
from ocpp.routing import on
from test_active_control import ready
from test_critical_fault_intent import pause_request, reached, transaction
from test_grid_profile import GridStation, start_grid
from test_live_netz_timing import clock_grid, configure
from test_protocol import peer
from test_pv_phase_lockout import periods
from test_release_recovery import session


class ResumeStation(GridStation):
    malformed = False

    @on("GetCompositeSchedule")
    def composite(self, evse_id, duration, charging_rate_unit=None):
        response = super().composite(evse_id, duration, charging_rate_unit)
        if self.malformed:
            response.schedule["evse_id"] = 2  # Wire-valid but wrong scope.
        return response


async def resume(station):
    await session(station, resumed=True)


@pytest.mark.parametrize("readback", ["matching", "mismatch", "rejected", "malformed"])
@pytest.mark.parametrize("transition", ["reconnect", "boot"])
async def test_netz_same_transaction_reconciles_current_offer(ha, readback, transition):
    entry = await ha.install()

    async def check(grid, station):
        station.values.update(ControlAuthority="OCPP", PhaseRotation="RST")
        station.phases = 3
        station.amps = 10 if readback == "mismatch" else 16
        station.readback = readback != "rejected"
        station.malformed = readback == "malformed"
        count = len(station.profiles)
        await ready(entry, station)
        await resume(station)
        await eventually(lambda: grid.task and grid.task.done())
        assert grid.transaction() == "net-test"
        assert grid.settings.get("charge_release")
        assert periods(station, count) == ([(3, 16)] if readback == "mismatch" else [])
        if readback in ("rejected", "malformed"):
            assert grid.status == "unconfirmed"
            assert grid.attributes()["applied_current_a"] is None
        else:
            assert grid.attributes()["applied_current_a"] == 16

    async with peer(entry, station_type=ResumeStation) as station:
        grid = await start_grid(entry, station)
        assert grid.current_point().amps == 16
        if transition == "boot":
            await check(grid, station)
            return
    await eventually(lambda: not grid.state.connected)
    async with peer(entry, station_type=ResumeStation) as station:
        await check(grid, station)


@pytest.mark.parametrize("authorization", ["new_transaction", "explicit_apply"])
async def test_independent_netz_decision_can_issue_without_readback(ha, authorization):
    entry = await ha.install()
    async with peer(entry, station_type=ResumeStation) as station:
        grid = await start_grid(entry, station)
    await eventually(lambda: not grid.state.connected)
    async with peer(entry, station_type=ResumeStation) as station:
        station.values.update(ControlAuthority="OCPP", PhaseRotation="RST")
        station.readback = False
        await ready(entry, station)
        if authorization == "new_transaction":
            await transaction(station, "Started", 0, tx="new")
            await eventually(lambda: grid.task and grid.task.done())
            assert grid.transaction() == "new"
            assert periods(station) == [(3, 16)]
        else:
            await resume(station)
            await eventually(lambda: grid.task and grid.task.done())
            assert not station.profiles
            await grid.settings.set("soll_power", 9)
            await grid.action()
            assert periods(station) == [(3, 13)]
        assert grid.status == "accepted_unconfirmed"


@pytest.mark.parametrize("stop", ["none", "expiry", "reconnect"])
async def test_offline_netz_start_and_stop_with_same_transaction(ha, stop):
    entry = await ha.install()
    async with peer(entry, station_type=ResumeStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        grid, settings, clock = clock_grid(entry)
        await settings.initialize_power()
        await configure(settings, "00:01", "00:01")
        await session(station)
        await grid.action()
        assert not station.profiles
        request = settings.get("grid_request")
    await eventually(lambda: not grid.state.connected)
    await clock.advance(120 if stop == "expiry" else 90)
    await ha.async_block_till_done()
    async with peer(entry, station_type=ResumeStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        station.readback = False
        await ready(entry, station)
        if stop == "reconnect":
            await grid.stop_release()
        await resume(station)
        await eventually(lambda: grid.task and grid.task.done())
        if stop != "none":
            assert not station.profiles and not settings.get("charge_release")
            assert settings.get("grid_request") is None
        else:
            assert periods(station) == [(1, 6)]
            assert settings.get("grid_request") == request
            assert grid.phase() == ("active", 1120)

    if stop == "none":
        # Once the independently matured start has sent a proposal, it cannot
        # grant a second write when the same transaction is resumed again.
        await eventually(lambda: not grid.state.connected)
        async with peer(entry, station_type=ResumeStation) as station:
            station.values["ControlAuthority"] = "OCPP"
            station.readback = False
            await ready(entry, station)
            await transaction(station, "Updated", 2)
            await eventually(lambda: grid.task and grid.task.done())
            assert not station.profiles and grid.status == "unconfirmed"


@pytest.mark.parametrize("boundary", ["post-ack", "post-readback"])
async def test_live_replacement_discards_netz_outcome(ha, monkeypatch, boundary):
    entry = await ha.install()
    async with peer(entry, station_type=ResumeStation) as old:
        grid = await start_grid(entry, old)
        async with pause_request(monkeypatch, grid, boundary) as (paused, _):
            await grid.settings.set("soll_power", 9)
            await grid.settings._approve_release()
            await reached(paused)
            task = grid.task
            adapter = grid.listener.adapter
            async with peer(entry, station_type=ResumeStation) as new:
                new.values.update(ControlAuthority="OCPP", PhaseRotation="RST")
                new.readback = False
                await ready(entry, new)
                await resume(new)
                await eventually(lambda: task.done() and not adapter.tasks)
                await eventually(lambda: grid.task is not task and grid.task.done())
                assert task.cancelled()
                assert not new.profiles and grid.status == "unconfirmed"
                assert grid.acknowledged is None
                assert grid.attributes()["applied_current_a"] is None
