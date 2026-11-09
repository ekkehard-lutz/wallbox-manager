"""F5 ordering and lifecycle replays over a simulated localhost OCPP peer."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from conftest import eventually
from ocpp.exceptions import OCPPError
from ocpp.routing import on
from ocpp.v21 import call
from test_active_control import event, ready
from test_live_netz_timing import running
from test_protocol import peer
from test_pv_phase_lockout import PhaseStation, establish, pending, periods, setup
from test_pv_runtime import install, power, tick

from custom_components.wallbox_manager.grid_point import Point


async def status(station, value="Faulted", *, at=None, connector=1):
    return await station.call(
        call.StatusNotification(
            timestamp=(at or datetime.now(UTC)).isoformat(),
            connector_status=value,
            evse_id=1,
            connector_id=connector,
        ),
        suppress=False,
    )


async def transaction(station, kind, seq, tx="net-test"):
    await station.call(
        call.TransactionEvent(
            event_type=kind,
            timestamp=datetime.now(UTC).isoformat(),
            trigger_reason={
                "Started": "CablePluggedIn",
                "Updated": "ChargingStateChanged",
                "Ended": "EVDeparted",
            }[kind],
            seq_no=seq,
            transaction_info={"transaction_id": tx, "charging_state": "EVConnected"},
            evse={"id": 1, "connector_id": 1},
        )
    )


def connector(grid):
    return ("observation", grid.state.binding, "connector_state")


@asynccontextmanager
async def pause_request(monkeypatch, grid, pause_at):
    adapter = grid.listener.adapter
    request = adapter.request
    reached, resume = asyncio.Event(), asyncio.Event()
    sent_profile = False

    async def pause():
        if not reached.is_set():
            reached.set()
            await resume.wait()

    async def delayed(payload):
        nonlocal sent_profile
        if isinstance(payload, call.SetChargingProfile) and pause_at == "pre-wire":
            await pause()
        response = await request(payload)
        if isinstance(payload, call.SetChargingProfile):
            sent_profile = True
            if pause_at == "post-ack":
                await pause()
        if (
            isinstance(payload, call.GetCompositeSchedule)
            and sent_profile
            and pause_at == "post-readback"
        ):
            await pause()
        return response

    with monkeypatch.context() as patch:
        patch.setattr(adapter, "request", delayed)
        try:
            yield reached, resume
        finally:
            resume.set()


async def reached(event):
    async with asyncio.timeout(5):
        await event.wait()


def queue(grid, amps=18):
    grid.allow_issue = True
    grid.launch(pv_point=Point(amps, 1, amps * 230))
    return grid.task


@pytest.mark.parametrize("pause_at", ["pre-wire", "post-ack", "post-readback"])
@pytest.mark.parametrize(
    "followup", ["none", "updated", "permission", "local", "ended"]
)
async def test_fault_fences_queued_intent_and_delayed_results(
    ha, monkeypatch, pause_at, followup
):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await setup(entry, station)
        await establish(grid, pv, station, 1, 20)
        held = grid.confirmed
        async with pause_request(monkeypatch, grid, pause_at) as (paused, resume):
            work = queue(grid)
            await reached(paused)
            epoch, generation, tx = (
                grid.epoch,
                grid.state.generation,
                grid.transaction(),
            )
            writes, reads = len(station.writes), station.composite_reads
            count = len(station.profiles)
            await status(station)
            assert grid.state.value(connector(grid)) == "faulted"
            assert grid.epoch > epoch
            assert grid.state.generation == generation and grid.transaction() == tx
            assert grid.settings.get("charge_release") and grid.hardware_enabled()
            assert grid.eligible() and grid.pv_ack is None
            assert grid.requested is None and grid.acknowledged is None
            assert (
                len(station.profiles),
                len(station.writes),
                station.composite_reads,
            ) == (
                count,
                writes,
                reads,
            )
            if followup == "updated":
                await transaction(station, "Updated", 1)
                assert grid.transaction() == tx
            elif followup == "permission":
                event(entry, "ChargingEnabled", "false")
            elif followup == "local":
                event(entry, "ControlAuthority", "Local")
            elif followup == "ended":
                await transaction(station, "Ended", 1)

            resume.set()
            await work
            assert grid.confirmed == held
            assert grid.acknowledged is None and grid.pv_ack is None
            assert len(station.profiles) == count
            assert len(station.writes) == writes
            if pause_at != "pre-wire":
                assert grid.point_uncertain
                assert grid.attributes()["applied_current_a"] is None


@pytest.mark.parametrize("clear", ["Unavailable", "Available", "Occupied", None])
async def test_only_new_current_connector_fault_invalidates(ha, monkeypatch, clear):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await setup(entry, station)
        await establish(grid, pv, station, 1, 20)
        stamp = datetime.now(UTC)
        await status(station, "Occupied", at=stamp)
        epoch = grid.epoch
        await status(station, at=stamp - timedelta(microseconds=1))
        assert grid.epoch == epoch
        await status(station, "Unavailable")
        assert grid.epoch == epoch
        await status(station)
        epoch = grid.epoch
        async with pause_request(monkeypatch, grid, "pre-wire") as (paused, resume):
            work = queue(grid)
            await reached(paused)
            count = len(station.profiles)
            await status(station)  # Later timestamp, same held value.
            if clear:
                await status(station, clear)
            assert grid.epoch == epoch and len(station.profiles) == count
            resume.set()
            await work
        assert periods(station, count) == [(1, 18)]
        assert grid.status == "confirmed" and grid.confirmed.amps == 18
        if clear:
            await status(station)
            assert grid.epoch > epoch


async def test_wrong_scope_and_old_generation_do_not_invalidate(ha):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, _ = await setup(entry, station)
        generation = grid.state.generation
    async with peer(entry, station_type=PhaseStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        await transaction(station, "Started", 0, "replacement")
        epoch = grid.epoch
        adapter = grid.listener.adapter
        assert grid.transaction() == "replacement"
        assert not adapter.observe(
            generation, connector(grid), "faulted", "ocpp2.1:StatusNotification"
        )
        with pytest.raises(OCPPError):
            await status(station, connector=2)
        assert grid.epoch == epoch
        assert grid.state.value(connector(grid)) is None
        await status(station)
        assert grid.epoch > epoch
        assert grid.transaction() == "replacement"


@pytest.mark.parametrize("result", ["confirmed", "accepted_unconfirmed", "rejected"])
async def test_fresh_netz_apply_under_held_fault(ha, result):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, settings, clock = await running(entry, station)
        previous = grid.confirmed
        start = len(station.profiles)
        await status(station)
        assert len(station.profiles) == start
        if result == "accepted_unconfirmed":
            station.readback = False
        if result == "rejected":
            station.profile_result = "Rejected"
        await settings.set("soll_power", 4.14)
        if result == "rejected":
            with pytest.raises(ValueError, match="Rejected"):
                await grid.action()
        else:
            await grid.action()
        assert grid.state.value(connector(grid)) == "faulted"
        assert len(station.profiles) == start + 1
        assert periods(station)[-1][1] > 0
        assert grid.status == result
        if result != "confirmed":
            assert grid.confirmed == previous
            assert grid.attributes()["applied_current_a"] is None
        if result == "accepted_unconfirmed":
            assert grid.acknowledged == grid.requested
        await clock.advance(90)
        assert len(station.profiles) == start + 1 and grid.retry_deadline is None


async def test_persisted_old_pv_proposal_drops_and_next_tick_can_submit(
    ha, monkeypatch
):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await setup(entry, station)
        pv.values["grid_export_power"] = 2000
        persist = pv.persist
        paused, resume = asyncio.Event(), asyncio.Event()

        async def delayed():
            await persist()
            if pv.proposed and pv.proposed.amps > 0 and not paused.is_set():
                paused.set()
                await resume.wait()

        monkeypatch.setattr(pv, "persist", delayed)
        start = len(station.profiles)
        work = asyncio.create_task(pv.tick())
        try:
            await reached(paused)
            await status(station)
            assert pv.proposed is None
        finally:
            resume.set()
        await work
        assert len(station.profiles) == start
        await tick(pv)
        assert periods(station, start) == [(1, 6)]
        assert (
            grid.status == "confirmed"
            and grid.state.value(connector(grid)) == "faulted"
        )


@pytest.mark.parametrize("stop", ["operator", "soc", "economic"])
@pytest.mark.parametrize("fault_timing", ["before-stop", "pre-wire"])
async def test_zero_policy_keeps_priority_during_fault(
    ha, monkeypatch, stop, fault_timing
):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await setup(entry, station)
        await establish(grid, pv, station, 1, 20)
        start = len(station.profiles)

        async def stop_work():
            if stop == "operator":
                await grid.stop_release()
            else:
                if stop == "soc":
                    pv.values["soc_speicher_aktuell"] = 0
                else:
                    assert pv.budget.bind(
                        [*grid.state.binding, "remote", grid.transaction()]
                    )
                    pv.budget.wh = 50
                await tick(pv)

        if fault_timing == "before-stop":
            await status(station)
            assert len(station.profiles) == start
            await stop_work()
        else:
            async with pause_request(monkeypatch, grid, "pre-wire") as (paused, resume):
                work = asyncio.create_task(stop_work())
                try:
                    await reached(paused)
                    await status(station)
                    assert len(station.profiles) == start
                finally:
                    resume.set()
                await work
        await grid.task
        assert [amps for _, amps in periods(station, start)] == [0]
        assert grid.settings.get("charge_release") is (stop != "operator")
        assert grid.hardware_enabled() is True and grid.confirmed.amps == 0
        assert pv.budget.stopped is (stop == "economic")


@pytest.mark.parametrize("approval_pending", [False, True])
async def test_fault_cancels_netz_retry_and_preserves_business_expiry(
    ha, monkeypatch, approval_pending
):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, settings, clock = await running(entry, station)
        station.profile_result = "Rejected"
        station.profile_info = {
            "reason_code": "ReenableLockout",
            "additional_info": "remainingSeconds=20",
        }
        await settings.set("soll_power", 4.14)
        with pytest.raises(ValueError, match="ReenableLockout"):
            await grid.action()
        retry, expiry = grid.timer, grid.schedule_timer
        assert grid.retry_deadline is not None and expiry is not None
        start = len(station.profiles)
        request = settings.get("grid_request")
        if approval_pending:
            await settings.set("grid_duration", "00:21")
            persist = settings.persist
            paused, resume = asyncio.Event(), asyncio.Event()

            async def delayed(profiles):
                if not paused.is_set():
                    paused.set()
                    await resume.wait()
                await persist(profiles)

            monkeypatch.setattr(settings, "persist", delayed)
            work = asyncio.create_task(grid.action())
            try:
                await reached(paused)
                expiry = grid.schedule_timer
                await status(station)
            finally:
                resume.set()
            with pytest.raises(ValueError, match="Superseded"):
                await work
        else:
            await status(station)
        assert retry.cancelled and grid.retry_deadline is None
        assert grid.schedule_timer is expiry and settings.get("grid_request") == request
        assert len(station.profiles) == start
        retry.callback()  # Also fence a callback already queued for execution.
        await clock.advance(30)
        assert len(station.profiles) == start
        station.profile_result, station.profile_info = "Accepted", None
        await clock.advance(request["end_at"] - clock.now)
        await eventually(lambda: station.amps == 0)
        assert (
            not settings.get("charge_release") and settings.get("grid_request") is None
        )
        assert [amps for _, amps in periods(station, start)] == [0]


async def test_fault_retires_pv_phase_retry_context_without_dispatch(ha):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await pending(entry, station)
        count = len(station.profiles)
        await status(station)
        assert grid.pv_phase_deadline is None
        await station.clock.advance(120)
        assert len(station.profiles) == count and pv.proposed is None


class DelayedAckStation(PhaseStation):
    @on("SetChargingProfile")
    async def profile(self, evse_id, charging_profile):
        result = super().profile(evse_id, charging_profile)
        if self.release:
            self.started.set()
            await self.release.wait()
        return result


async def test_fault_during_outstanding_economic_restart_cannot_commit_ack(ha):
    entry = await install(ha)
    async with peer(entry, station_type=DelayedAckStation) as station:
        grid, pv = await setup(entry, station)
        pv.values.update(grid_export_power=2000, storage_charge_power=100)
        assert pv.budget.bind([*grid.state.binding, "remote", grid.transaction()])
        pv.budget.stopped, pv.budget.reset, pv.budget.cause = (
            True,
            True,
            "import_budget",
        )
        await power(station, 0)
        station.release = asyncio.Event()
        station.started.clear()
        await pv.tick()
        await reached(station.started)
        work = grid.task
        assert pv.pending_restart == "battery_charging"
        count = len(station.profiles)
        # The peer is still handling SetChargingProfile. Deliver its outbound
        # notification while its reader is blocked on returning the old ACK.
        notice = asyncio.create_task(status(station))
        try:
            await eventually(lambda: grid.state.value(connector(grid)) == "faulted")
            assert pv.pending_restart is None and pv.budget.stopped
            assert len(station.profiles) == count
        finally:
            station.release.set()
            await notice
        await work
        assert pv.budget.stopped and pv.budget.cause == "import_budget"
        assert grid.acknowledged is None and grid.pv_ack is None
        assert grid.attributes()["applied_current_a"] is None
        stored = await pv.store.async_load()
        assert stored["economics"]["stopped"]
