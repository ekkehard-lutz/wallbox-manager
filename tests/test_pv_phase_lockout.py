"""PV-LOCK-01–08 and PV-DIAG-11–14 through the beta.21 OCPP path."""

import asyncio
import json
import logging
import math
from datetime import UTC, datetime

import pytest
from ocpp.routing import on
from ocpp.v21 import call
from test_grid_profile import FakeClock, GridStation
from test_protocol import peer
from test_pv_runtime import install, power, release, tick

from custom_components.wallbox_manager.grid_point import Point


class PhaseStation(GridStation):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.clock = FakeClock()
        self.track = False
        self.composite_reads = 0
        self.until = 0

    @on("GetCompositeSchedule")
    def composite(self, evse_id, duration, charging_rate_unit=None):
        self.composite_reads += 1
        return super().composite(evse_id, duration, charging_rate_unit)

    @on("SetChargingProfile")
    def profile(self, evse_id, charging_profile):
        period = charging_profile["charging_schedule"][0]["charging_schedule_period"][0]
        changes_phase = period.get("number_phases", self.phases) != self.phases
        if not self.track or not changes_phase:
            return super().profile(evse_id, charging_profile)
        if self.clock.now >= self.until:
            result = super().profile(evse_id, charging_profile)
            if result.status == "Accepted" and self.apply_profile:
                self.until = self.clock.now + 300
            return result
        self.profile_result = "Rejected"
        remaining = math.ceil(self.until - self.clock.now)
        self.profile_info = {
            "reason_code": "PhaseSwitchLockout",
            "additional_info": f"remainingSeconds={remaining}",
        }
        try:
            return super().profile(evse_id, charging_profile)
        finally:
            self.profile_result, self.profile_info = "Accepted", None


def periods(station, start=0):
    periods = [
        profile["charging_schedule"][0]["charging_schedule_period"][0]
        for _, profile in station.profiles[start:]
    ]
    return [(p.get("number_phases"), p["limit"]) for p in periods]


def records(caplog):
    marker = "WBMGR subsystem=pv "
    return [
        json.loads(r.message.split(marker, 1)[1])
        for r in caplog.records
        if marker in r.message
    ]


async def setup(entry, station):
    g, pv = await release(entry, station, profile="PV_OPTIMUM")
    # The replay owns tick delivery. Lifecycle publications must not arm a
    # second tick on the same fake clock while the test advances it.
    pv.arm = lambda: None
    if pv.timer:
        pv.timer.cancel()
        pv.timer = None
    g.wall_time, g.call_later = lambda: station.clock.now, station.clock.later
    pv.monotonic = g.wall_time
    pv.values.update(
        soc_speicher_aktuell=100,
        grid_import_power=0,
        grid_export_power=0,
    )
    return g, pv


async def advance(pv, station, seconds):
    await station.clock.advance(seconds)
    await tick(pv)


async def establish(g, pv, station, phases, amps):
    point = Point(amps, phases, amps * phases * 230)
    g.allow_issue = True
    g.launch(pv_point=point)
    await g.task
    assert g.current_point() == point
    await power(station, point.watts)
    pv.core.startup = None
    pv.core.up_cycles = 3


async def pending(entry, station):
    g, pv = await setup(entry, station)
    await establish(g, pv, station, 1, 20)
    station.until = station.clock.now + 33
    station.track = True
    await tick(pv)
    assert g.status == "phase_lockout_pending" and g.retry_deadline is None
    assert g.pv_phase_deadline == station.until + 5
    assert periods(station)[-1] == (3, 7)
    assert (station.phases, station.amps) == (1, 20)
    return g, pv


async def test_recorded_fast_ramp_preserves_1p20a_until_fresh_retry(ha, caplog):
    caplog.set_level(logging.INFO, logger="custom_components.wallbox_manager.pv")
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        g, pv = await setup(entry, station)
        assert (station.phases, station.amps) == (3, 0)
        start = len(station.profiles)
        station.track = True
        # An export offer initiates 1p/6 A through the ordinary FAST controller.
        pv.values["grid_export_power"] = 1380
        await tick(pv)
        assert (station.phases, station.amps) == (1, 6)
        assert station.until == 1300
        pv.values["grid_export_power"] = 0
        for _ in range(50):
            await power(station, station.phases * station.amps * 230)
            await advance(pv, station, 5)
        assert periods(station, start) == [(1, a) for a in range(6, 21)]
        assert station.clock.now == 1250
        await power(station, 4600)
        for seconds in (5, 5, 7):
            await advance(pv, station, seconds)
        assert periods(station, start)[-2:] == [(1, 20), (3, 7)]
        assert g.requested.phases == 3 and g.current_point().amps == 20
        assert g.pv_phase_deadline == 1305
        count = len(station.profiles)
        reads = station.composite_reads
        for _ in range(300):
            await tick(pv)
        assert len(station.profiles) == count
        assert station.composite_reads == reads
        await advance(pv, station, 37)
        assert len(station.profiles) == count
        await station.clock.advance(1)
        assert len(station.profiles) == count  # No deadline-owned control pass.
        await tick(pv)
        assert periods(station, count) == [(3, 7)]
        assert (g.current_point().phases, g.current_point().amps) == (3, 7)

    events = records(caplog)
    execution = [
        r
        for r in events
        if r["event"] == "pv_execution" and r["commanded_point"] == [3, 7]
    ]
    assert [r["command_status"] for r in execution] == [
        "submitted",
        "rejected",
        "phase_lockout_pending",
        "submitted",
        "accepted",
        "confirmed",
    ]
    proposed = next(
        r
        for r in events
        if r["event"] == "pv_decision" and r["requested_point"] == [3, 7]
    )
    assert proposed["previous_decision_point"] == proposed["old_point"] == [1, 20]
    assert proposed["confirmed_point"] == [1, 20] and proposed["physical_phases"] == 1
    rejection, held, ack, confirmed = execution[1], execution[2], *execution[-2:]
    assert rejection["command_reason"] == "PhaseSwitchLockout"
    assert rejection["acknowledged_point"] is None
    assert held["continuation_point"] == [1, 20]
    assert (held["retry_after_s"], held["retry_at"], held["retry_source"]) == (
        38,
        1305,
        "station_remaining",
    )
    assert ack["acknowledged_point"] == [3, 7] and ack["confirmed_point"] is None
    assert ack["physical_phases"] == 1
    assert confirmed["confirmed_point"] == [3, 7] and confirmed["physical_phases"] == 3
    assert all("commanded_point" not in r for r in events if r["event"] == "pv_sample")


async def test_reverse_rejection_preserves_3p7a_and_retries_fresh_demand(ha):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        g, pv = await setup(entry, station)
        await establish(g, pv, station, 3, 7)
        station.until, station.track = 1033, True
        pv.values["grid_import_power"] = 2500
        start = len(station.profiles)
        await tick(pv)
        assert periods(station, start) == [(1, 10)]
        assert (station.phases, station.amps) == (3, 7)
        # A changed demand no longer justifies reducing current or phases.
        pv.values["grid_import_power"] = 0
        pv.core.up_cycles = 0
        await advance(pv, station, 37)
        assert periods(station, start) == [(1, 10)]
        pv.values["grid_import_power"] = 2500
        await advance(pv, station, 1)
        assert periods(station, start) == [(1, 10), (1, 10)]
        assert (station.phases, station.amps) == (1, 10)


async def test_same_phase_down_and_up_do_not_clear_phase_restriction(ha):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        g, pv = await pending(entry, station)
        deadline, start = g.pv_phase_deadline, len(station.profiles)
        pv.values["grid_import_power"] = 920
        await advance(pv, station, 5)
        assert periods(station, start) == [(1, 16)]
        await power(station, 3680)
        pv.values["grid_import_power"] = 0
        for _ in range(3):
            await advance(pv, station, 5)
        assert periods(station, start)[-1] == (1, 17)
        assert g.pv_phase_deadline == deadline
        # Expiry does not replay 3p/7 A. A fresh downward proposal wins.
        await power(station, 3910)
        pv.values["grid_import_power"] = 230
        await advance(pv, station, deadline - station.clock.now)
        assert periods(station, start)[-1] == (1, 16)
        assert all(phases == 1 for phases, _ in periods(station, start))


@pytest.mark.parametrize(
    "cause", ["soc", "budget", "operator", "cap", "permission", "authority", "phase"]
)
async def test_phase_wait_never_blocks_safer_intent(ha, cause):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        g, pv = await pending(entry, station)
        start = len(station.profiles)
        if cause == "soc":
            pv.values["soc_speicher_aktuell"] = 0
        elif cause == "budget":
            pv.budget.wh = 50
        elif cause == "operator":
            await g.stop_release()
        elif cause == "cap":
            await g.settings.set("allowed_current_1p", 15)
        elif cause == "permission":
            await g.listener.control.request("ChargingEnabled", "false")
        else:
            role = "control_authority" if cause == "authority" else "physical_phases"
            key = ("station", g.station_scope, role)
            g.state.observe(
                g.state.generation, key, "local" if cause == "authority" else 3, "test"
            )
        await tick(pv)
        if cause == "cap":
            assert periods(station, start) == [(1, 15)]
            assert g.pv_phase_waiting()
        elif cause in ("permission", "authority"):
            assert not periods(station, start) and g.pv_phase_deadline is None
        else:
            assert periods(station, start) == [(None, 0)]
        await station.clock.advance(40)
        assert all(phases != 3 or amps == 0 for phases, amps in periods(station, start))


@pytest.mark.parametrize(
    "outcome",
    [
        "generic",
        "communication",
        "unconfirmed",
        "missing_time",
        "invalid_time",
        "missing_point",
    ],
)
async def test_only_recognized_atomic_rejection_can_claim_continuation(
    ha, caplog, monkeypatch, outcome
):
    caplog.set_level(logging.INFO, logger="custom_components.wallbox_manager.pv")
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        g, pv = await setup(entry, station)
        await establish(g, pv, station, 1, 20)
        caplog.clear()
        if outcome == "communication":
            request = g.listener.adapter.request

            async def fail(payload):
                if isinstance(payload, call.SetChargingProfile):
                    raise TimeoutError
                return await request(payload)

            monkeypatch.setattr(g.listener.adapter, "request", fail)
        elif outcome == "unconfirmed":
            station.readback = station.apply_profile = False
        else:
            station.profile_result = "Rejected"
            if outcome != "generic":
                station.profile_info = {"reason_code": "PhaseSwitchLockout"}
            if outcome == "invalid_time":
                station.profile_info["additional_info"] = "remainingSeconds=999999"
            if outcome == "missing_point":
                g.confirmed = None
                station.readback = False
        count = len(station.profiles)
        if outcome == "missing_point":
            g.allow_issue = True
            g.launch(pv_point=Point(7, 3, 4830))
            await g.task
        else:
            await tick(pv)
        actual = periods(station, count)
        assert actual == ([] if outcome == "communication" else [(3, 7)])
        trace = [r for r in records(caplog) if r["event"] == "pv_execution"]
        if outcome in ("generic", "communication", "unconfirmed"):
            assert g.pv_phase_deadline is None
            assert not any("continuation_point" in r for r in trace)
            if outcome == "unconfirmed":
                assert trace[-1]["command_status"] == "accepted_unconfirmed"
                assert trace[-1]["acknowledged_point"] == [3, 7]
                assert trace[-1]["confirmed_point"] is None
            if outcome == "communication":
                assert trace[-1]["commanded_point"] is None
                assert trace[-1]["command_status"] == "communication_failure"
        else:
            assert g.pv_phase_deadline == 1060
            assert trace[-1]["retry_source"] == "fallback"
            assert trace[-1]["continuation_point"] == (
                None if outcome == "missing_point" else [1, 20]
            )


async def test_late_lockout_reply_cannot_survive_operator_stop(ha, monkeypatch):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        g, pv = await setup(entry, station)
        await establish(g, pv, station, 1, 20)
        station.track, station.until = True, 1033
        ready, finish = asyncio.Event(), asyncio.Event()
        request = g.listener.adapter.request

        async def delay(payload):
            response = await request(payload)
            if (
                isinstance(payload, call.SetChargingProfile)
                and response.status == "Rejected"
            ):
                ready.set()
                await finish.wait()
            return response

        monkeypatch.setattr(g.listener.adapter, "request", delay)
        work = asyncio.create_task(tick(pv))
        try:
            async with asyncio.timeout(5):
                await ready.wait()
            await g.stop_release()
        finally:
            finish.set()
            await work
        if g.task:
            await g.task
        assert g.pv_phase_deadline is None and station.amps == 0
        count = len(station.profiles)
        await advance(pv, station, 60)
        assert len(station.profiles) == count


@pytest.mark.parametrize("level", [0, 1, 2])
async def test_execution_trace_level_and_regulation_invariance(ha, caplog, level):
    caplog.set_level(logging.INFO, logger="custom_components.wallbox_manager.pv")
    entry = await install(ha)
    ha.config_entries.async_update_entry(
        entry, options={**entry.options, "diagnostic_level": level}
    )
    async with peer(entry, station_type=PhaseStation) as station:
        await pending(entry, station)
    trace = [r for r in records(caplog) if r["event"] == "pv_execution"]
    assert bool(trace) is (level == 2)


async def test_battery_limited_fast_exploration_still_alternates_3p11a_and_3p12a(ha):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        g, pv = await setup(entry, station)
        await establish(g, pv, station, 3, 11)
        pv.values["storage_discharge_power"] = 5000
        start = len(station.profiles)
        for _ in range(3):
            pv.values["grid_import_power"] = 0
            for _ in range(3):
                if station.amps != 11:
                    break
                await advance(pv, station, 5)
            assert station.amps == 12
            await power(station, 8280)
            pv.values["grid_import_power"] = 690
            await advance(pv, station, 5)
            assert station.amps == 11
            await power(station, 7590)
        assert periods(station, start) == [(3, 12), (3, 11)] * 3
        assert pv.core.mode == "FAST" and g.pv_phase_deadline is None


async def test_transaction_replacement_discards_phase_wait_without_replay(ha):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        g, pv = await pending(entry, station)
        start = len(station.profiles)
        for event_type, reason, transaction, seq in (
            ("Ended", "EVDeparted", "net-test", 1),
            ("Started", "CablePluggedIn", "replacement", 0),
        ):
            await station.call(
                call.TransactionEvent(
                    event_type=event_type,
                    timestamp=datetime.now(UTC).isoformat(),
                    trigger_reason=reason,
                    seq_no=seq,
                    transaction_info={"transaction_id": transaction},
                    evse={"id": 1, "connector_id": 1},
                )
            )
        assert g.transaction() == "replacement" and g.pv_phase_deadline is None
        await station.clock.advance(60)
        assert not periods(station, start)
        pv.values["soc_speicher_aktuell"] = 0
        await tick(pv)
        assert periods(station, start) == [(None, 0)]


async def test_delayed_confirmation_reports_the_same_acknowledged_command(ha, caplog):
    caplog.set_level(logging.INFO, logger="custom_components.wallbox_manager.pv")
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        g, pv = await setup(entry, station)
        await establish(g, pv, station, 1, 20)
        caplog.clear()
        station.readback = False
        await tick(pv)
        assert g.status == "accepted_unconfirmed" and g.point_uncertain
        station.readback = True
        g.launch(confirm_only=True)
        await g.task
        trace = [r for r in records(caplog) if r["event"] == "pv_execution"]
        assert [r["command_status"] for r in trace] == [
            "submitted",
            "accepted",
            "accepted_unconfirmed",
            "confirmed",
        ]
        assert trace[-1]["requested_point"] == trace[-1]["confirmed_point"] == [3, 7]
        assert trace[-1]["command_epoch"] == trace[0]["command_epoch"]
