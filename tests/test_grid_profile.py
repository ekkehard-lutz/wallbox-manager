"""NETZ planning and real OCPP 2.1 peer regression; simulated acceptance only."""

import asyncio
import time
from datetime import UTC, datetime
from fractions import Fraction

import pytest
from conftest import eventually
from ocpp.routing import on
from ocpp.v21 import call, call_result
from test_active_control import Controllable, ready
from test_protocol import peer, row

from custom_components.wallbox_manager.grid import retry_delay
from custom_components.wallbox_manager.grid_point import Envelope


def ranges(*, minimum=6, maximum=27, asymmetry=20, modes=(1, 3), caps=None, step=1):
    return Envelope(minimum, maximum, asymmetry, step, modes, caps or {})


@pytest.mark.parametrize("minimum,cap", [(6, 20), (7, 16), (8, 24)])
def test_dynamic_nominal_midpoints_and_exact_ties(minimum, cap):
    e = ranges(minimum=minimum, caps={1: cap}, asymmetry=27)
    low_max = ((cap * 230 + 50) // 100) * 100
    high_min = ((minimum * 3 * 230 + 50) // 100) * 100
    boundary = Fraction(low_max + high_min, 2)
    assert e.resolve(boundary).phases == 1
    assert e.resolve(boundary + Fraction(1, 100)).phases == 3
    if high_min <= 5000:
        assert e.resolve(5000, ongoing=3).phases == 3


def test_generic_three_modes_disabled_modes_and_stable_overlap():
    e = ranges(modes=(1, 2, 3), caps={1: 20, 2: 12, 3: 27})
    assert e.resolve((4600 + 2800) / 2).phases == 1
    assert e.resolve((5500 + 4100) / 2).phases == 2
    assert e.resolve(5000).phases == 3
    e = ranges(caps={1: 20, 3: 0})
    assert set(e.ranges) == {1}
    assert e.resolve(11000).phases == 1
    e = ranges(modes=(1, 2, 3), caps={2: 0})
    assert set(e.ranges) == {1, 3}
    assert e.resolve(4350).phases == 1
    assert e.resolve(4351).phases == 3
    assert e.resolve(4200, ongoing=3).phases == 3
    assert e.resolve(4200, ongoing=1).phases == 1
    assert e.resolve(4000, ongoing=3).phases == 1
    assert ranges(caps={1: 0}).resolve(1000).amps == 0
    assert ranges(caps={1: 0}).resolve(1000, "up").phases == 3
    with pytest.raises(ValueError, match="disabled"):
        ranges(caps={1: 0, 3: 0}).resolve(1000)
    assert ranges(caps={1: 0, 3: 0}).resolve(0).amps == 0


def test_quantization_legacy_migration_and_unequal_voltage():
    e = ranges(caps={1: "19/3", 3: 100})
    assert e.ranges[1][3] == 6
    assert e.ranges[3][3] == 27
    assert e.migration[1] == {"stored": "19/3", "effective": 6}
    assert ranges(caps={1: 5}).ranges.keys() == {3}
    for direction, expected in [("down", 8), ("nearest", 9), ("up", 9)]:
        assert ranges(modes=(1,)).resolve(2000, direction).amps == expected
    e = Envelope(6, 27, 20, 1, (1, 3), {})
    assert e.ranges[3][1] == 4100
    assert ranges(step=2).resolve(11000).amps % 2 == 0


@pytest.mark.parametrize(
    "info,expected",
    [
        (
            {
                "reason_code": "PhaseSwitchLockout",
                "additional_info": "remainingSeconds=31",
            },
            36,
        ),
        (
            {"reason_code": "ReenableLockout", "additional_info": "remainingSeconds=0"},
            5,
        ),
        ({"reason_code": "ReenableLockout"}, 60),
        (
            {
                "reason_code": "ReenableLockout",
                "additional_info": "remainingSeconds=-1",
            },
            60,
        ),
        (
            {
                "reason_code": "ReenableLockout",
                "additional_info": "remainingSeconds=1.5",
            },
            60,
        ),
        ({"reason_code": "Busy", "additional_info": "remainingSeconds=30"}, None),
        ({"reason_code": "Rejected"}, None),
        (None, None),
    ],
)
def test_retry_contract(info, expected):
    assert retry_delay(info) == expected


class GridStation(Controllable):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = self.rows[:4] + [
            row("EVSE", "CurrentStep", "1", {"id": 1}),
            row("EVSE", "SupportedPhaseModes", "1,3", {"id": 1}),
            row("Connector", "MaximumCurrent", "27", {"id": 1, "connector_id": 1}),
            row(
                "Connector",
                "MaximumAsymmetryCurrent",
                "20",
                {"id": 1, "connector_id": 1},
            ),
        ]
        self.amps, self.phases = 0, 1
        self.profiles = []
        self.profile_info = None
        self.profile_result = "Accepted"
        self.readback = True
        self.apply_profile = True

    @on("GetCompositeSchedule")
    def composite(self, evse_id, duration, charging_rate_unit=None):
        if not self.readback:
            return call_result.GetCompositeSchedule(status="Rejected")
        return call_result.GetCompositeSchedule(
            status="Accepted",
            schedule={
                "evse_id": evse_id,
                "duration": duration,
                "schedule_start": datetime.now(UTC).isoformat(),
                "charging_rate_unit": "A",
                "charging_schedule_period": [
                    {
                        "start_period": 0,
                        "limit": self.amps,
                        "number_phases": self.phases,
                    }
                ],
            },
        )

    @on("SetChargingProfile")
    def profile(self, evse_id, charging_profile):
        self.profiles.append((evse_id, charging_profile))
        period = charging_profile["charging_schedule"][0]["charging_schedule_period"][0]
        if self.profile_result == "Accepted" and self.apply_profile:
            self.amps = period["limit"]
            self.phases = period.get("number_phases", self.phases)
            self.values["PhaseRotation"] = "Rxx" if self.phases == 1 else "RST"
        return call_result.SetChargingProfile(
            status=self.profile_result, status_info=self.profile_info
        )


async def start_grid(entry, station, transaction="net-test"):
    # Business schedules now exist from Start even without configured duration.
    # Install the deterministic business clock before creating that epoch.
    grid = entry.runtime_data.grid
    if grid.wall_time is time.time:
        clock = FakeClock()
        grid.wall_time, grid.call_later = lambda: clock.now, clock.later
    control = await ready(entry, station)
    await control.request("ControlAuthority", "OCPP")
    await station.call(
        call.TransactionEvent(
            event_type="Started",
            timestamp=datetime.now(UTC).isoformat(),
            trigger_reason="CablePluggedIn",
            seq_no=0,
            transaction_info={
                "transaction_id": transaction,
                "charging_state": "EVConnected",
            },
            evse={"id": 1, "connector_id": 1},
        )
    )
    await entry.runtime_data.settings.initialize_power()
    await entry.runtime_data.settings.set("soll_power", 11)
    try:
        await entry.runtime_data.grid.action()
    except ValueError:
        # Rejection tests inspect the truthful backend outcome below.
        if entry.runtime_data.grid.status not in (
            "rejected",
            "unsupported",
            "communication_failure",
        ):
            raise
    return entry.runtime_data.grid


async def test_complete_profile_confirmation_no_telemetry_repeat_and_set(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        assert len(station.profiles) == 1
        evse, profile = station.profiles[0]
        assert evse == 1 and profile["transaction_id"] == "net-test"
        assert profile["charging_profile_kind"] == "Absolute"
        assert profile["charging_profile_purpose"] == "TxProfile"
        assert profile["stack_level"] == 0
        assert grid.confirmed.amps == 16 and grid.confirmed.phases == 3
        assert grid.attributes()["applied_offered_power_w"] == 11040
        for i in range(4):
            await station.call(
                call.MeterValues(
                    evse_id=1,
                    meter_value=[
                        {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "sampled_value": [
                                {
                                    "measurand": "Power.Active.Import",
                                    "value": i * 1000,
                                    "unit_of_measure": {"unit": "W"},
                                }
                            ],
                        }
                    ],
                )
            )
        assert len(station.profiles) == 1
        settings = entry.runtime_data.settings
        for value in (8, 7, 6):
            await settings.set("soll_power", value)
        assert len(station.profiles) == 1
        await settings._approve_release()
        await eventually(lambda: len(station.profiles) == 2)
        await eventually(lambda: grid.status == "confirmed")
        assert grid.confirmed.phases == 3 and grid.confirmed.amps == 9
        roles = {
            e.attributes.get("wallbox_manager_role") for e in ha.states.async_all()
        }
        assert "allowed_current_1p" in roles and "allowed_current_3p" in roles
        assert (
            "allowed_current_2p" not in roles
            and "phase_switch_deviation_pct" not in roles
        )
        with pytest.raises(ValueError):
            await settings.set("allowed_current_1p", 6.5)
        with pytest.raises(ValueError):
            await settings.set("allowed_current_1p", 21)
        await settings.set("allowed_current_3p", 0)
        await eventually(
            lambda: grid.status == "confirmed" and grid.confirmed.phases == 1
        )


async def test_lockout_retry_recomputes_and_off_cancels(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.profile_result = "Rejected"
        station.profile_info = {
            "reason_code": "PhaseSwitchLockout",
            "additional_info": "remainingSeconds=13",
        }
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.timer is not None)
        assert grid.retry_at == 18 and grid.status == "phase_lockout_pending"
        assert grid.confirmed.amps == 0 and grid.requested.amps == 16
        grid.timer.cancel()
        grid.timer = None
        station.profile_result = "Accepted"
        station.phases, station.amps = 1, 6
        grid.launch()
        await eventually(lambda: grid.status == "confirmed")
        count = len(station.profiles)
        assert count >= 2
        await entry.runtime_data.settings.set("soll_power", 5)
        await grid.stop_release()
        assert grid.timer is None
        await asyncio.sleep(1.1)
        assert len(station.profiles) == count + 1
        assert station.amps == 0 and station.values["ChargingEnabled"] == "true"


async def test_generic_rejection_and_accepted_unconfirmed_never_retry(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.profile_result = "Rejected"
        station.profile_info = {"reason_code": "Busy"}
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "rejected")
        assert grid.timer is None
        station.profile_result = "Accepted"
        station.apply_profile = False
        await entry.runtime_data.settings.set("soll_power", 8)
        await entry.runtime_data.settings._approve_release()
        await eventually(lambda: grid.status == "accepted_unconfirmed")
        assert grid.confirmed.amps == 0
        assert grid.acknowledged.amps == 12
        assert grid.timer is None


class FakeClock:
    def __init__(self):
        self.now = 1000
        self.calls = []

    def later(self, delay, callback):
        from types import SimpleNamespace

        handle = SimpleNamespace(
            at=self.now + delay, callback=callback, cancelled=False
        )
        handle.cancel = lambda: setattr(handle, "cancelled", True)
        self.calls.append(handle)
        return handle

    async def advance(self, seconds):
        self.now += seconds
        due = [h for h in self.calls if not h.cancelled and h.at <= self.now]
        for handle in due:
            handle.cancelled = True
            handle.callback()
        await asyncio.sleep(0)


async def test_start_delay_explicit_zero_duration_and_expiration_without_vehicle(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        settings = entry.runtime_data.settings
        await grid.stop_release()
        clock = FakeClock()
        grid.wall_time, grid.call_later = lambda: clock.now, clock.later
        await settings.set("grid_start_delay", "00:02")
        await settings.set("grid_duration", "00:03")
        await settings._approve_release()
        await grid.action()
        await eventually(
            lambda: grid.status == "confirmed" and grid.confirmed.amps == 0
        )
        assert grid.phase()[0] == "waiting"
        assert settings.approved("grid_duration") == 180
        assert settings.values["grid_request"]["start_at"] == 1120
        assert settings.values["grid_request"]["end_at"] == 1300
        await clock.advance(120)
        await eventually(
            lambda: grid.status == "confirmed" and grid.confirmed.amps == 16
        )
        await clock.advance(180)
        await eventually(lambda: settings.get("charge_release") is False)
        assert settings.values["grid_request"] is None
        await settings.set("grid_start_delay", "")
        await settings.set("grid_duration", "00:00")
        await settings._approve_release()
        await grid.action()
        await clock.advance(0)
        await eventually(lambda: settings.get("charge_release") is False)
        await settings.set("grid_duration", "00:01")
        await settings._approve_release()
        await grid.action()
        state = entry.runtime_data.state
        state.session["ended_at"] = datetime.now(UTC).isoformat()
        state.publish(("session", state.binding, "active"))
        await clock.advance(60)
        await eventually(lambda: settings.get("charge_release") is False)


@pytest.mark.parametrize("reason", ["PhaseSwitchLockout", "ReenableLockout"])
async def test_fallback_retry_deadline_and_generation_fence(ha, reason):
    entry = await ha.install()
    clock = FakeClock()
    grid = entry.runtime_data.grid
    grid.wall_time, grid.call_later = lambda: clock.now, clock.later
    async with peer(entry, station_type=GridStation) as station:
        station.profile_result = "Rejected"
        station.profile_info = {"reason_code": reason}
        await start_grid(entry, station)
        await eventually(lambda: grid.retry_at == 60)
        initial_count = len(station.profiles)
        assert initial_count == (2 if reason == "PhaseSwitchLockout" else 1)
        await clock.advance(59)
        assert len(station.profiles) == initial_count
        station.profile_result = "Accepted"
        await clock.advance(1)
        await eventually(lambda: grid.status == "confirmed")
        assert len(station.profiles) == initial_count + 1
        station.profile_result = "Rejected"
        await entry.runtime_data.settings.set("soll_power", 5)
        await entry.runtime_data.settings._approve_release()
        await clock.advance(1)
        await eventually(lambda: grid.retry_at == 60)
        state = entry.runtime_data.state
        state.reset(connected=True, boot=True)
        await clock.advance(60)
        assert len(station.profiles) >= initial_count + 2
        assert grid.timer is None


async def test_reconnect_reconciles_positive_offer_to_zero_without_positive_replay(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        assert len(station.profiles) == 1
        await (
            grid.stop_release()
        )  # Explicit OFF; transport loss preserves business schedules.
    async with peer(entry, station_type=GridStation) as station:
        station.values.update(ControlAuthority="OCPP", PhaseRotation="RST")
        station.amps, station.phases = 16, 3
        control = await ready(entry, station)
        await station.call(
            call.TransactionEvent(
                event_type="Updated",
                timestamp=datetime.now(UTC).isoformat(),
                trigger_reason="TxResumed",
                seq_no=1,
                transaction_info={
                    "transaction_id": "net-test",
                    "charging_state": "EVConnected",
                },
                evse={"id": 1, "connector_id": 1},
            )
        )
        await eventually(lambda: grid.status == "confirmed" and grid.task.done())
        assert entry.runtime_data.settings.get("charge_release") is False
        assert len(station.profiles) == 1
        period = station.profiles[0][1]["charging_schedule"][0][
            "charging_schedule_period"
        ][0]
        assert period == {"start_period": 0, "limit": 0}
        assert grid.confirmed.amps == 0 and station.phases == 3
        assert control.attributes("ControlAuthority")["command_status"] == "confirmed"


def test_disjoint_and_non_monotonic_boundaries_are_deterministic():
    disjoint = ranges(minimum=10, caps={1: 10, 3: 11})
    assert disjoint.resolve(4600).phases == 1
    assert disjoint.resolve(4601).phases == 3
    nonmonotonic = ranges(modes=(1, 2, 3), asymmetry=27, caps={1: 27, 2: 6, 3: 27})
    assert nonmonotonic.resolve(4000).phases == 1
    assert nonmonotonic.resolve(4501).phases == 3


@pytest.mark.parametrize(
    "direction,watts,amps",
    [
        ("down", 0, 0),
        ("down", 100, 0),
        ("down", 1399, 0),
        ("down", 1400, 6),
        ("down", 1500, 6),
        ("down", 1599, 6),
        ("down", 1600, 7),
        ("up", 0, 0),
        ("up", 100, 6),
        ("up", 1380, 6),
        ("up", 1400, 6),
        ("up", 1600, 7),
        ("nearest", 0, 0),
        ("nearest", 699, 0),
        ("nearest", 700, 6),
        ("nearest", 1499, 6),
        ("nearest", 1500, 7),
        ("nearest", 1500, 7),
    ],
)
def test_grid_zero_is_a_real_approximation_candidate(direction, watts, amps):
    e = ranges(modes=(1,), caps={1: 20})
    point = e.resolve(watts, direction)
    assert point.amps == amps
    assert point.phases == (1 if amps else None)
    assert point.watts == amps * 230


def test_phase_lockout_interim_point_uses_same_directional_approximation():
    e = ranges()
    assert e.resolve(1500, "down", ongoing=3).phases == 1
    zero = e.on_phase(1500, 3, "down")
    assert zero.amps == 0 and zero.phases is None
    final = e.resolve(1500, "down")
    assert final.phases == 1 and final.amps == 6

    interim = e.on_phase(1500, 3, "up")
    assert interim.phases == 3 and interim.amps == 6
    final = e.resolve(1500, "up")
    assert final.phases == 1 and final.amps == 7

    # A mode disabled by configuration does not become an eligible fallback.
    assert 3 not in ranges(caps={3: 0}).ranges


async def test_grid_down_below_minimum_sends_zero_offer(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        await entry.runtime_data.settings.set("power_approximation", "down")
        with pytest.raises(ValueError, match="positive technical range"):
            await entry.runtime_data.settings.set("soll_power", 0.1)
        await grid.stop_release()
        await eventually(
            lambda: grid.status == "confirmed" and grid.confirmed.amps == 0
        )
        assert station.values["ChargingEnabled"] == "true"


class TemporaryPhaseLockoutStation(GridStation):
    locked = False

    @on("SetChargingProfile")
    def profile(self, evse_id, charging_profile):
        period = charging_profile["charging_schedule"][0]["charging_schedule_period"][0]
        requested_mode = period.get("number_phases", self.phases)
        if self.locked and requested_mode != self.phases:
            self.profile_result = "Rejected"
            self.profile_info = {
                "reason_code": "PhaseSwitchLockout",
                "additional_info": "remainingSeconds=1",
            }
        else:
            self.profile_result = "Accepted"
            self.profile_info = None
        return super().profile(evse_id, charging_profile)


@pytest.mark.parametrize(
    "direction,requested,interim_amps,final_amps",
    [("down", 1.5, 0, 6), ("up", 1.5, 6, 7)],
)
async def test_lockout_applies_interim_on_current_phases_and_retries_desired(
    ha, direction, requested, interim_amps, final_amps
):
    entry = await ha.install()
    async with peer(entry, station_type=TemporaryPhaseLockoutStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        assert station.phases == 3
        station.locked = True
        await entry.runtime_data.settings.set("power_approximation", direction)
        await entry.runtime_data.settings.set("soll_power", requested)
        await entry.runtime_data.settings._approve_release()
        await eventually(lambda: grid.timer is not None and grid.retry_at == 6)
        assert grid.requested.phases == 1
        assert grid.requested.amps == final_amps
        assert grid.confirmed.phases == 3
        assert grid.confirmed.amps == interim_amps
        assert grid.status == "phase_lockout_pending"
        schedule = station.profiles[-1][1]["charging_schedule"][0]
        last = schedule["charging_schedule_period"][0]
        assert last["limit"] == interim_amps
        if interim_amps == 0:
            assert "number_phases" not in last
        else:
            assert last["number_phases"] == 3

        # Trigger the scheduled attempt explicitly without six real seconds.
        grid.timer.cancel()
        grid.timer = None
        station.locked = False
        grid.launch()
        await eventually(lambda: grid.status == "confirmed")
        assert grid.confirmed.phases == 1
        assert grid.confirmed.amps == final_amps


class ReviewedLockoutStation(TemporaryPhaseLockoutStation):
    interim_result = "Accepted"
    interim_reason = None
    fail_interim_read = False
    reenable = False

    @on("SetChargingProfile")
    def profile(self, evse_id, charging_profile):
        period = charging_profile["charging_schedule"][0]["charging_schedule_period"][0]
        mode = period.get("number_phases", self.phases)
        if self.locked and mode == self.phases:
            self.profile_result = self.interim_result
            self.profile_info = self.interim_reason
            result = GridStation.profile(self, evse_id, charging_profile)
            if self.fail_interim_read and self.profile_result == "Accepted":
                self.readback = False
            return result
        if self.reenable and self.amps == 0 and period["limit"] > 0:
            self.profile_result = "Rejected"
            self.profile_info = {
                "reason_code": "ReenableLockout",
                "additional_info": "remainingSeconds=9",
            }
            return GridStation.profile(self, evse_id, charging_profile)
        return super().profile(evse_id, charging_profile)


async def pending_interim(entry, station, direction="down", power=1.5):
    grid = await start_grid(entry, station)
    await eventually(lambda: grid.status == "confirmed")
    clock = FakeClock()
    grid.wall_time, grid.call_later = lambda: clock.now, clock.later
    station.locked = True
    await entry.runtime_data.settings.set("power_approximation", direction)
    await eventually(lambda: grid.task.done())
    await entry.runtime_data.settings.set("soll_power", power)
    await entry.runtime_data.settings._approve_release()
    await clock.advance(1)
    await eventually(lambda: grid.task.done())
    return grid, clock


async def test_interim_acknowledgement_and_retry_diagnostics(ha):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        grid, clock = await pending_interim(entry, station)
        assert grid.requested.amps == 6 and grid.requested.phases == 1
        assert grid.confirmed.amps == 0 and grid.confirmed.phases == 3
        assert grid.acknowledged.amps == 0
        count = len(station.profiles)
        await clock.advance(6)
        await eventually(lambda: grid.task.done())
        # Already applied interim zero is not sent again.
        assert len(station.profiles) == count + 1
        station.locked = False
        station.reenable = True
        await clock.advance(6)
        await eventually(lambda: grid.retry_at == 14)
        assert grid.reason == "ReenableLockout"
        assert grid.requested.amps == 6 and grid.confirmed.amps == 0
        station.reenable = False
        await clock.advance(13)
        assert grid.confirmed.amps == 0
        await clock.advance(1)
        await eventually(lambda: grid.status == "confirmed")
        assert grid.retry_at is None and grid.timer is None
        assert grid.confirmed.phases == 1 and grid.confirmed.amps == 6
        count = len(station.profiles)
        await clock.advance(600)
        assert len(station.profiles) == count


@pytest.mark.parametrize("direction,power", [("down", 1.5), ("up", 1.5)])
@pytest.mark.parametrize("reason,delay", [("Busy", None), ("ReenableLockout", 14)])
async def test_interim_rejection_is_truthful_and_only_lockouts_retry(
    ha, direction, power, reason, delay
):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        station.interim_result = "Rejected"
        station.interim_reason = {
            "reason_code": reason,
            "additional_info": "remainingSeconds=9",
        }
        grid, clock = await pending_interim(entry, station, direction, power)
        assert grid.reason == reason
        assert grid.retry_at == delay
        assert grid.confirmed.phases == 3 and grid.confirmed.amps == 16
        assert grid.acknowledged.amps == 16
        count = len(station.profiles)
        await clock.advance(60 if delay is None else delay - 1)
        assert len(station.profiles) == count
        if delay is not None:
            station.locked = False
            await clock.advance(1)
            await eventually(lambda: grid.status == "confirmed")


async def test_failed_interim_confirmation_preserves_recognized_retry(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        original = entry.runtime_data.grid.read_point

        async def read(*args):
            if station.fail_interim_read and station.locked and station.amps == 0:
                raise TimeoutError
            return await original(*args)

        monkeypatch.setattr(entry.runtime_data.grid, "read_point", read)
        station.fail_interim_read = True
        grid, clock = await pending_interim(entry, station)
        assert grid.timer is not None and grid.retry_at == 6
        assert grid.acknowledged.amps == 0
        assert grid.confirmed.amps == 16
        station.fail_interim_read = False
        station.readback = True
        station.locked = False
        await clock.advance(6)
        await eventually(lambda: grid.status == "confirmed")


async def test_cancelled_retry_callback_cannot_escape_new_set_revision(ha):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        grid, _ = await pending_interim(entry, station)
        stale = grid.timer.callback
        count = len(station.profiles)
        station.locked = False
        settings = entry.runtime_data.settings
        await settings.set("soll_power", 2)
        assert len(station.profiles) == count
        await settings._approve_release()
        await eventually(lambda: grid.status == "confirmed")
        count = len(station.profiles)
        stale()
        await asyncio.sleep(0.05)
        assert len(station.profiles) == count


@pytest.mark.parametrize("mode", [1, 2, 3])
@pytest.mark.parametrize("minimum,step,cap", [(6, 1, 11), (7, 2, 14)])
@pytest.mark.parametrize("direction", ["down", "up", "nearest"])
def test_quantization_against_exhaustive_executable_candidates(
    mode, minimum, step, cap, direction
):
    e = ranges(minimum=minimum, step=step, modes=(mode,), caps={mode: cap})
    currents = [0, *range(minimum, cap + 1, step)]
    offers = [(((amps * mode * 230 + 50) // 100) * 100, amps) for amps in currents]
    probes = {Fraction(0), Fraction(1), Fraction(offers[-1][0] + 1000)}
    for (low, _), (high, _) in zip(offers, offers[1:], strict=False):
        for boundary in (Fraction(low), Fraction(high), Fraction(low + high, 2)):
            probes.update(
                [
                    max(0, boundary - Fraction(1, 100)),
                    boundary,
                    boundary + Fraction(1, 100),
                ]
            )
    for watts in probes:
        if direction == "down":
            expected = max((p for p in offers if p[0] <= watts), default=offers[0])
        elif direction == "up":
            expected = min((p for p in offers if p[0] >= watts), default=offers[-1])
        else:
            expected = min(offers, key=lambda p: (abs(p[0] - watts), -p[0]))
        point = e.resolve(watts, direction)
        assert (point.display_watts, point.amps) == expected
        assert point.watts == point.amps * mode * 230


async def test_failed_read_retains_confirmed_positive_phase_for_new_intent(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        station.readback = False
        await entry.runtime_data.settings.set("soll_power", 4.2)
        await entry.runtime_data.settings._approve_release()
        await eventually(lambda: grid.task.done())
        period = station.profiles[-1][1]["charging_schedule"][0][
            "charging_schedule_period"
        ][0]
        assert period["number_phases"] == 3
        assert period["limit"] == 6


@pytest.mark.parametrize("change", ["off", "local", "end", "boot", "profile", "unload"])
async def test_pending_interim_retry_fenced_by_lifecycle(ha, change):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        grid, clock = await pending_interim(entry, station)
        stale = grid.timer.callback
        count = len(station.profiles)
        state = entry.runtime_data.state
        if change == "off":
            await entry.runtime_data.server.control.request("ChargingEnabled", "false")
        elif change == "local":
            key = entry.runtime_data.server.control.key("ControlAuthority")
            state.observe(state.generation, key, "local", source="test")
        elif change == "end":
            await station.call(
                call.TransactionEvent(
                    event_type="Ended",
                    timestamp=datetime.now(UTC).isoformat(),
                    trigger_reason="EVDeparted",
                    seq_no=1,
                    transaction_info={
                        "transaction_id": "net-test",
                        "charging_state": "Idle",
                    },
                    evse={"id": 1, "connector_id": 1},
                )
            )
        elif change == "boot":
            state.reset(connected=True, boot=True)
        elif change == "profile":
            # The configured profile enum requires PV input mappings. The runtime
            # callback is tested here without adding unrelated HA mappings.
            entry.runtime_data.settings.values["profile"] = "PV_SURPLUS"
            grid.edited("charging_profile")
        else:
            from custom_components.wallbox_manager import async_unload_entry

            assert await async_unload_entry(ha, entry)
            del entry.runtime_data
        stale()
        await clock.advance(600)
        await asyncio.sleep(0.02)
        assert len(station.profiles) == count
        assert grid.timer is None


async def test_disabled_current_mode_interim_is_zero_only(ha):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        station.locked = True
        await entry.runtime_data.settings.set("allowed_current_3p", 0)
        await eventually(lambda: grid.timer is not None)
        assert grid.confirmed.phases == 3 and grid.confirmed.amps == 0
        assert grid.requested.phases == 1
        period = station.profiles[-1][1]["charging_schedule"][0][
            "charging_schedule_period"
        ][0]
        assert period == {"start_period": 0, "limit": 0}


async def test_held_matching_offer_does_not_retransmit_after_failed_read(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        count = len(station.profiles)
        station.readback = False
        await entry.runtime_data.settings.set("soll_power", 11.04)
        await entry.runtime_data.settings._approve_release()
        await eventually(lambda: grid.task.done())
        assert grid.status == "confirmed"
        assert len(station.profiles) == count


async def test_above_mode_maximum_reports_limitation(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        await entry.runtime_data.settings.set("allowed_current_3p", 6)
        await eventually(
            lambda: grid.status == "confirmed" and grid.confirmed.amps == 6
        )
        assert grid.reason == "power_above_mode_maximum"


async def test_superseded_interim_cannot_dispatch_after_new_intent(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        original = grid.dispatch_point
        waiting, release = asyncio.Event(), asyncio.Event()

        async def dispatch(point, *args):
            if point.amps == 0:
                waiting.set()
                await release.wait()
            return await original(point, *args)

        monkeypatch.setattr(grid, "dispatch_point", dispatch)
        station.locked = True
        await entry.runtime_data.settings.set("power_approximation", "down")
        await eventually(lambda: grid.task.done())
        await entry.runtime_data.settings.set("soll_power", 1.5)
        await entry.runtime_data.settings._approve_release()
        await asyncio.wait_for(waiting.wait(), 5)
        old_task = grid.task
        station.locked = False
        await entry.runtime_data.settings.set("soll_power", 2)
        await entry.runtime_data.settings._approve_release()
        release.set()
        await old_task
        await eventually(lambda: grid.status == "confirmed")
        periods = [
            p[1]["charging_schedule"][0]["charging_schedule_period"][0]
            for p in station.profiles
        ]
        assert all(period["limit"] > 0 for period in periods)
        assert grid.requested.amps == 8 and grid.confirmed.amps == 8


class DelayedGridStation(GridStation):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delay = False
        self.waiting, self.profile_release = asyncio.Event(), asyncio.Event()

    @on("SetChargingProfile")
    async def profile(self, evse_id, charging_profile):
        response = super().profile(evse_id, charging_profile)
        if self.delay:
            self.delay = False
            self.waiting.set()
            await self.profile_release.wait()
        return response


async def test_held_match_cannot_hide_superseded_inflight_opposite_write(ha):
    entry = await ha.install()
    async with peer(entry, station_type=DelayedGridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        station.delay = True
        await entry.runtime_data.settings.set("soll_power", 5)
        await entry.runtime_data.settings._approve_release()
        await asyncio.wait_for(station.waiting.wait(), 5)
        assert station.amps == 7
        station.readback = False
        await entry.runtime_data.settings.set("soll_power", 11)
        await entry.runtime_data.settings._approve_release()
        station.profile_release.set()
        await eventually(lambda: grid.task.done())
        assert station.amps == 16
        assert grid.status == "accepted_unconfirmed"
        assert grid.acknowledged.amps == 16


async def test_duplicate_launches_coalesce_inflight_reconciliation(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        count = len(station.profiles)
        await entry.runtime_data.settings.set("soll_power", 5)
        await entry.runtime_data.settings._approve_release()
        grid.launch()
        task = grid.task
        grid.launch()
        assert grid.task is task
        await eventually(lambda: grid.status == "confirmed")
        assert len(station.profiles) == count + 1
        assert grid.timer is None


async def test_uncertain_interim_hides_actual_until_supported_confirmation(ha):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        station.fail_interim_read = True
        grid, clock = await pending_interim(entry, station)
        assert station.amps == 0 and station.phases == 3
        assert grid.confirmed.amps == 16 and grid.current_point().phases == 3
        assert grid.point_uncertain
        assert grid.attributes()["applied_current_a"] is None
        assert grid.attributes()["applied_phase_count"] is None
        assert grid.attributes()["applied_offered_power_w"] is None
        assert grid.requested.amps == 6 and grid.requested.phases == 1
        assert grid.acknowledged.amps == 0
        timer, count = grid.timer, len(station.profiles)
        station.readback = True
        await grid.reconcile(
            grid.epoch, entry.runtime_data.server.adapter, confirm_only=True
        )
        assert grid.attributes()["applied_current_a"] == 0
        assert grid.attributes()["applied_phase_count"] == 3
        assert not grid.point_uncertain
        assert grid.status == "phase_lockout_pending"
        assert grid.requested.amps == 6 and grid.requested.phases == 1
        assert grid.timer is timer and grid.retry_at == 6
        assert len(station.profiles) == count
        await clock.advance(5)
        assert len(station.profiles) == count


async def test_atomic_interim_rejection_preserves_confirmed_actual(ha):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        station.interim_result = "Rejected"
        station.interim_reason = {"reason_code": "Busy"}
        grid, clock = await pending_interim(entry, station)
        assert grid.reason == "Busy" and grid.timer is None
        assert not grid.point_uncertain
        assert grid.attributes()["applied_current_a"] == 16
        assert grid.attributes()["applied_phase_count"] == 3
        count = len(station.profiles)
        await clock.advance(600)
        assert len(station.profiles) == count


async def test_rejected_retry_cannot_clear_prior_accepted_uncertainty(ha):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        station.fail_interim_read = True
        grid, clock = await pending_interim(entry, station)
        uncertainty = grid.point_uncertain
        assert uncertainty
        station.interim_result = "Rejected"
        station.interim_reason = {"reason_code": "Busy"}
        await clock.advance(6)
        await eventually(lambda: grid.task.done())
        assert grid.reason == "Busy" and grid.timer is None
        assert grid.point_uncertain is uncertainty
        assert grid.confirmed.amps == 16
        assert grid.attributes()["applied_current_a"] is None
        assert grid.attributes()["applied_phase_count"] is None


async def test_uncertain_actual_reconnect_requires_new_generation_confirmation(ha):
    entry = await ha.install()
    async with peer(entry, station_type=ReviewedLockoutStation) as station:
        station.fail_interim_read = True
        grid, _ = await pending_interim(entry, station)
        assert grid.attributes()["applied_current_a"] is None
    await grid.stop_release()
    assert grid.attributes()["applied_phase_count"] is None
    async with peer(entry, station_type=GridStation) as station:
        station.values.update(ControlAuthority="OCPP", PhaseRotation="RST")
        station.phases, station.amps, station.readback = 3, 0, False
        await ready(entry, station)
        await station.call(
            call.TransactionEvent(
                event_type="Updated",
                timestamp=datetime.now(UTC).isoformat(),
                trigger_reason="TxResumed",
                seq_no=1,
                transaction_info={
                    "transaction_id": "net-test",
                    "charging_state": "EVConnected",
                },
                evse={"id": 1, "connector_id": 1},
            )
        )
        await eventually(lambda: grid.status == "unconfirmed" and grid.task.done())
        assert grid.attributes()["applied_current_a"] is None
        assert grid.attributes()["applied_phase_count"] is None
        assert not station.profiles and grid.timer is None
        station.readback = True
        await grid.reconcile(
            grid.epoch, entry.runtime_data.server.adapter, confirm_only=True
        )
        assert grid.attributes()["applied_current_a"] == 0
        assert grid.attributes()["applied_phase_count"] == 3
        assert entry.runtime_data.settings.get("charge_release") is False
        assert not station.profiles and grid.timer is None


@pytest.mark.parametrize("old_result", ["Accepted", "Rejected"])
async def test_superseded_response_cannot_restore_stale_actual(
    ha, monkeypatch, old_result
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        adapter = entry.runtime_data.server.adapter
        original = adapter.request
        waiting, release = asyncio.Event(), asyncio.Event()
        delayed = False

        async def request(payload):
            nonlocal delayed
            response = await original(payload)
            if isinstance(payload, call.SetChargingProfile) and not delayed:
                delayed = True
                waiting.set()
                await release.wait()
            return response

        monkeypatch.setattr(adapter, "request", request)
        count = len(station.profiles)
        station.profile_result = old_result
        await entry.runtime_data.settings.set("soll_power", 5)
        await entry.runtime_data.settings._approve_release()
        await asyncio.wait_for(waiting.wait(), 5)
        old_task = grid.task
        station.profile_result = "Accepted"
        station.readback = False
        await entry.runtime_data.settings.set("soll_power", 8)
        await entry.runtime_data.settings._approve_release()
        await eventually(lambda: grid.task.done())
        assert grid.status == "accepted_unconfirmed"
        uncertainty = grid.point_uncertain
        assert uncertainty and grid.confirmed.amps == 16
        assert grid.attributes()["applied_current_a"] is None
        assert grid.attributes()["applied_phase_count"] is None
        release.set()
        await old_task
        assert grid.point_uncertain is uncertainty
        assert grid.attributes()["applied_current_a"] is None
        assert grid.attributes()["applied_phase_count"] is None
        station.readback = True
        await grid.reconcile(grid.epoch, adapter, confirm_only=True)
        assert grid.status == "confirmed"
        assert grid.attributes()["applied_current_a"] == 12
        assert grid.attributes()["applied_phase_count"] == 3
        assert len(station.profiles) == count + 2 and grid.timer is None


async def test_overlapping_atomic_rejections_restore_prior_actual(ha, monkeypatch):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        adapter = entry.runtime_data.server.adapter
        original = adapter.request
        waiting = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        calls = 0

        async def request(payload):
            nonlocal calls
            response = await original(payload)
            if isinstance(payload, call.SetChargingProfile):
                index = calls
                calls += 1
                waiting[index].set()
                await release[index].wait()
            return response

        monkeypatch.setattr(adapter, "request", request)
        count = len(station.profiles)
        station.profile_result = "Rejected"
        await entry.runtime_data.settings.set("soll_power", 5)
        await entry.runtime_data.settings._approve_release()
        await asyncio.wait_for(waiting[0].wait(), 5)
        old_task = grid.task
        station.readback = False
        await entry.runtime_data.settings.set("soll_power", 8)
        await entry.runtime_data.settings._approve_release()
        await asyncio.wait_for(waiting[1].wait(), 5)
        release[0].set()
        await old_task
        assert grid.attributes()["applied_current_a"] is None
        release[1].set()
        await grid.task
        assert not grid.point_uncertain
        assert grid.attributes()["applied_current_a"] == 16
        assert grid.attributes()["applied_phase_count"] == 3
        assert grid.timer is None and len(station.profiles) == count + 2
