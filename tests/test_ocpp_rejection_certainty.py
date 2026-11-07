"""Command certainty through the shared NETZ/PV OCPP path, with a fake station."""

import asyncio
import logging
from datetime import UTC, datetime

import pytest
from conftest import eventually
from ocpp.routing import on
from ocpp.v21 import call
from test_beta22_profile_integration import settled
from test_grid_profile import start_grid
from test_protocol import peer
from test_pv_phase_lockout import PhaseStation, establish, periods, records, setup
from test_pv_runtime import install, tick
from test_safe_charge_actions import context, invoke

from custom_components.wallbox_manager.grid_point import Point


class PartialStation(PhaseStation):
    partial_offer = None

    @on("SetChargingProfile")
    def profile(self, evse_id, charging_profile):
        result = super().profile(evse_id, charging_profile)
        if self.partial_offer is not None:
            self.phases, self.amps = self.partial_offer
            # Hardware changed, but its independent phase report is delayed.
            self.partial_offer = None
        return result


async def prepare(entry, station, profile):
    if profile == "NETZ":
        grid = await start_grid(entry, station)
        await settled(grid)
        return grid
    grid, pv = await setup(entry, station)
    await establish(grid, pv, station, 1, 20)
    return grid


async def propose(grid, profile, *, same_phase=False):
    if profile == "NETZ":
        await grid.settings.set("soll_power", 9 if same_phase else 2)
        await grid.settings._approve_release()
    else:
        grid.allow_issue = True
        grid.launch(pv_point=Point(18, 1, 4140) if same_phase else Point(7, 3, 4830))
    await settled(grid)


def assert_unconfirmed(grid, held):
    # Retain the last observation internally without publishing it as applied.
    assert grid.confirmed == held
    assert grid.point_uncertain
    attributes = grid.attributes()
    assert attributes["applied_current_a"] is None
    assert attributes["applied_phase_count"] is None
    assert attributes["applied_offered_power_w"] is None


@pytest.mark.parametrize("profile", ["NETZ", "PV_OPTIMUM"])
@pytest.mark.parametrize(
    "reason,same_phase,partial",
    [
        (None, False, None),
        ("Unrecognized", False, None),
        ("RestoreFailed", False, "phase"),
        ("RestoreFailed", False, "restore"),
        (None, True, "current"),
    ],
)
async def test_ambiguous_rejection_preserves_uncertainty(
    ha, caplog, profile, reason, same_phase, partial
):
    caplog.set_level(logging.INFO, logger="custom_components.wallbox_manager.pv")
    entry = await install(ha)
    async with peer(entry, station_type=PartialStation) as station:
        grid = await prepare(entry, station, profile)
        held = grid.confirmed
        old_ack = grid.acknowledged
        station.profile_result = "Rejected"
        station.profile_info = {"reason_code": reason} if reason else None
        phase_after = 1 if held.phases == 3 else 3
        if partial == "phase":
            station.partial_offer = (phase_after, 0)
        elif partial == "restore":
            station.partial_offer = (phase_after, 6)
        elif partial == "current":
            station.partial_offer = (held.phases, held.amps - 1)
        start = len(station.profiles)
        caplog.clear()
        await propose(grid, profile, same_phase=same_phase)
        assert len(station.profiles) == start + 1
        assert grid.status == "rejected" and grid.reason == (reason or "Rejected")
        assert grid.retry_deadline is None and grid.pv_phase_deadline is None
        assert grid.acknowledged == old_ack
        assert_unconfirmed(grid, held)
        assert grid.attributes()["physical_phase_mode"] == held.phases
        if profile != "NETZ":
            rejection = [r for r in records(caplog) if r["event"] == "pv_execution"][-1]
            assert rejection["command_status"] == "rejected"
            assert rejection["commanded_point"] is not None
            assert rejection["acknowledged_point"] is None
            assert rejection["confirmed_point"] is None
            assert rejection["physical_phases"] == held.phases
            assert "continuation_point" not in rejection

        adapter, epoch, transaction = (
            grid.listener.adapter,
            grid.epoch,
            grid.transaction(),
        )
        station.readback = False
        assert await grid.read_point(adapter, epoch, transaction) is None
        assert_unconfirmed(grid, held)
        station.readback = True
        if station.phases != held.phases:
            assert await grid.read_point(adapter, epoch, transaction) is None
            assert_unconfirmed(grid, held)
            grid.state.observe(
                grid.state.generation,
                ("station", grid.station_scope, "physical_phases"),
                station.phases,
                "fake:delayed_station_phase",
            )
            assert grid.attributes()["physical_phase_mode"] == station.phases
            assert_unconfirmed(grid, held)

        # A scoped, consistent read can recover a different actual offer, or
        # independently confirm that the station retained the previous one.
        actual = await grid.read_point(adapter, epoch, transaction)
        assert actual == Point(
            station.amps, station.phases, station.amps * station.phases * 230
        )
        assert not grid.point_uncertain
        assert grid.attributes()["applied_current_a"] == station.amps
        assert grid.attributes()["applied_phase_count"] == station.phases
        assert len(station.profiles) == start + 1


@pytest.mark.parametrize(
    "cause", ["operator", "soc", "budget", "permission", "authority"]
)
async def test_existing_pv_fences_and_stop_during_uncertainty(ha, cause):
    entry = await install(ha)
    async with peer(entry, station_type=PartialStation) as station:
        grid = await prepare(entry, station, "PV_OPTIMUM")
        held = grid.confirmed
        station.profile_result = "Rejected"
        await propose(grid, "PV_OPTIMUM")
        assert_unconfirmed(grid, held)
        station.profile_result, station.readback = "Accepted", False
        start = len(station.profiles)

        # An ordinary unresolved tick may read back but must not force zero.
        await tick(grid.pv)
        assert not periods(station, start)
        assert_unconfirmed(grid, held)
        if cause == "operator":
            await invoke(ha, entry, context(grid))
        elif cause == "permission":
            assert (
                await grid.listener.control.request("ChargingEnabled", "false")
                == "confirmed"
            )
            await tick(grid.pv)
            assert not periods(station, start)
            assert_unconfirmed(grid, held)
            await invoke(ha, entry, context(grid))
        elif cause == "authority":
            grid.state.observe(
                grid.state.generation,
                ("station", grid.station_scope, "control_authority"),
                "local",
                "fake:authority_change",
            )
            await tick(grid.pv)
            assert not grid.settings.get("charge_release")
            assert not periods(station, start)
            assert_unconfirmed(grid, held)
            return
        else:
            if cause == "soc":
                grid.pv.values["soc_speicher_aktuell"] = 0
            else:
                grid.pv.budget.wh = grid.settings.get("pv_grid_import_budget_wh")
            await tick(grid.pv)
        await settled(grid)
        assert periods(station, start) == [(None, 0)]
        assert grid.acknowledged.amps == 0
        assert_unconfirmed(grid, held)
        assert grid.settings.get("charge_release") is (cause in ("soc", "budget"))


@pytest.mark.parametrize("atomic_reason", ["PhaseSwitchLockout", "ReenableLockout"])
async def test_later_atomic_rejection_keeps_prior_unresolved_command(ha, atomic_reason):
    entry = await install(ha)
    async with peer(entry, station_type=PartialStation) as station:
        grid = await prepare(entry, station, "NETZ")
        held = grid.confirmed
        station.profile_result = "Rejected"
        await propose(grid, "NETZ", same_phase=True)
        assert_unconfirmed(grid, held)
        prior = grid.point_uncertain
        station.readback = False
        station.profile_info = {"reason_code": atomic_reason}
        start = len(station.profiles)
        await propose(grid, "NETZ")
        assert grid.point_uncertain is prior
        assert_unconfirmed(grid, held)
        assert periods(station, start) == [(1, 9)]
        assert grid.retry_deadline is not None


@pytest.mark.parametrize("reason", ["PhaseSwitchLockout", "RestoreFailed"])
@pytest.mark.parametrize(
    "superseded_by", ["readback", "dispatch", "transaction", "to_netz", "to_pv"]
)
async def test_late_rejection_preserves_newer_context(
    ha, monkeypatch, reason, superseded_by
):
    entry = await install(ha)
    async with peer(entry, station_type=PartialStation) as station:
        grid = await prepare(
            entry, station, "NETZ" if superseded_by == "to_pv" else "PV_OPTIMUM"
        )
        adapter = grid.listener.adapter
        epoch, transaction = grid.epoch, grid.transaction()
        station.profile_result = "Rejected"
        station.profile_info = {"reason_code": reason}
        entered, resume = asyncio.Event(), asyncio.Event()
        request = adapter.request

        async def delayed_response(payload):
            response = await request(payload)
            if isinstance(payload, call.SetChargingProfile) and not entered.is_set():
                entered.set()
                await resume.wait()
            return response

        monkeypatch.setattr(adapter, "request", delayed_response)
        point = Point(7, 3, 4830)
        if superseded_by in ("readback", "dispatch"):
            # Exercise the wire-result handler directly. Normal Grid.launch
            # coalesces same-epoch reconciliation tasks.
            old = asyncio.create_task(
                grid.dispatch_point(point, epoch, adapter, transaction)
            )
        elif superseded_by == "to_pv":
            await grid.settings.set("soll_power", 2)
            await grid.settings._approve_release()
            old = grid.task
        else:
            grid.allow_issue = True
            grid.launch(pv_point=point)
            old = grid.task
        try:
            await asyncio.wait_for(entered.wait(), 5)
            station.profile_result, station.profile_info = "Accepted", None
            if superseded_by == "readback":
                assert (
                    await grid.read_point(adapter, epoch, transaction) == grid.confirmed
                )
                assert not grid.point_uncertain
            elif superseded_by == "dispatch":
                # Exercise the response-processing overlap after the wire
                # released the old CALL. Each submission keeps its own record.
                await grid.dispatch_point(
                    Point(18, 1, 4140), epoch, adapter, transaction
                )
                assert grid.point_uncertain
            elif superseded_by == "transaction":
                for event, trigger, seq, tx in (
                    ("Ended", "EVDeparted", 1, transaction),
                    ("Started", "CablePluggedIn", 0, "new-transaction"),
                ):
                    await station.call(
                        call.TransactionEvent(
                            event_type=event,
                            timestamp=datetime.now(UTC).isoformat(),
                            trigger_reason=trigger,
                            seq_no=seq,
                            transaction_info={"transaction_id": tx},
                            evse={"id": 1, "connector_id": 1},
                        )
                    )
                await eventually(lambda: grid.transaction() == "new-transaction")
            else:
                await grid.settings.set(
                    "charging_profile",
                    "NETZ" if superseded_by == "to_netz" else "PV_OPTIMUM",
                )
                await settled(grid)
                assert not grid.settings.get("charge_release")
            point, uncertainty, deadline = (
                grid.confirmed,
                grid.point_uncertain,
                grid.pv_phase_deadline,
            )
            status = grid.status
        finally:
            resume.set()
            await old
        assert grid.confirmed == point
        if superseded_by != "transaction" or reason == "RestoreFailed":
            assert grid.point_uncertain is uncertainty
        assert grid.pv_phase_deadline is deadline
        assert grid.status == status


@pytest.mark.parametrize("profile", ["NETZ", "PV_OPTIMUM"])
@pytest.mark.parametrize("lose_response", [False, True])
async def test_ack_without_readback_and_lost_response_remain_uncertain(
    ha, monkeypatch, profile, lose_response
):
    entry = await install(ha)
    async with peer(entry, station_type=PartialStation) as station:
        grid = await prepare(entry, station, profile)
        held = grid.confirmed
        adapter = grid.listener.adapter
        request = adapter.request

        async def no_confirmation(payload):
            response = await request(payload)
            if isinstance(payload, call.SetChargingProfile):
                station.readback = False
                if lose_response:
                    raise TimeoutError
            return response

        monkeypatch.setattr(adapter, "request", no_confirmation)
        start = len(station.profiles)
        await propose(grid, profile)
        assert len(station.profiles) == start + 1
        assert grid.status == (
            "communication_failure" if lose_response else "accepted_unconfirmed"
        )
        assert grid.acknowledged == (held if lose_response else grid.requested)
        assert_unconfirmed(grid, held)
