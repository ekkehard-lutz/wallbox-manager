"""NETZ planning and real OCPP 2.1 peer regression; simulated acceptance only."""

import asyncio
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


def ranges(
    *, minimum=6, maximum=27, asymmetry=20, modes=(1, 3), caps=None, volts=230, step=1
):
    return Envelope(minimum, maximum, asymmetry, step, modes, caps or {}, [volts] * 3)


@pytest.mark.parametrize(
    "minimum,cap,volts", [(6, 20, 230), (7, 16, 240), (8, 24, 220)]
)
def test_dynamic_midpoints_and_exact_ties(minimum, cap, volts):
    e = ranges(minimum=minimum, caps={1: cap}, volts=volts, asymmetry=27)
    boundary = Fraction(cap * volts + minimum * volts * 3, 2)
    assert e.resolve(boundary).phases == 1
    assert e.resolve(boundary + Fraction(1, 100)).phases == 3
    assert (
        e.resolve(5000, ongoing=3).phases == 3 if 3 * volts * minimum <= 5000 else True
    )


def test_generic_three_modes_disabled_modes_and_stable_overlap():
    e = ranges(modes=(1, 2, 3), caps={1: 20, 2: 12, 3: 27})
    assert e.resolve((4600 + 2760) / 2).phases == 1
    assert e.resolve((5520 + 4140) / 2).phases == 2
    assert e.resolve(5000).phases == 3
    e = ranges(caps={1: 20, 3: 0})
    assert set(e.ranges) == {1}
    assert e.resolve(11000).phases == 1
    e = ranges(modes=(1, 2, 3), caps={2: 0})
    assert set(e.ranges) == {1, 3}
    assert e.resolve(4370).phases == 1
    assert e.resolve(4371).phases == 3
    assert e.resolve(4200, ongoing=3).phases == 3
    assert e.resolve(4200, ongoing=1).phases == 1
    assert e.resolve(4000, ongoing=3).phases == 1
    assert ranges(caps={1: 0}).resolve(1000).phases == 3
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
    e = Envelope(6, 27, 20, 1, (1, 3), {}, [220, 230, 240])
    assert e.ranges[3][1] == 4140
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
    return entry.runtime_data.grid


async def test_complete_profile_confirmation_no_telemetry_repeat_and_debounce(ha):
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
        assert grid.retry_at == 18 and grid.status == "rejected"
        assert grid.confirmed.amps == 0 and grid.requested.amps == 16
        grid.timer.cancel()
        grid.timer = None
        station.profile_result = "Accepted"
        station.phases, station.amps = 1, 6
        grid.launch()
        await eventually(lambda: grid.status == "confirmed")
        assert len(station.profiles) == 2
        await entry.runtime_data.settings.set("soll_power", 5)
        assert grid.debounce
        await entry.runtime_data.server.control.request("ChargingEnabled", "false")
        assert grid.timer is grid.debounce is None
        await asyncio.sleep(1.1)
        assert len(station.profiles) == 2


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
        control, settings = (
            entry.runtime_data.server.control,
            entry.runtime_data.settings,
        )
        await control.request("ChargingEnabled", "false")
        clock = FakeClock()
        grid.wall_time, grid.call_later = lambda: clock.now, clock.later
        await settings.set("grid_start_delay", "00:02")
        await settings.set("grid_duration", "00:03")
        await control.request("ChargingEnabled", "true")
        await eventually(
            lambda: grid.status == "confirmed" and grid.confirmed.amps == 0
        )
        assert grid.phase()[0] == "waiting"
        assert settings.get("grid_duration") is None
        await clock.advance(120)
        await eventually(
            lambda: grid.status == "confirmed" and grid.confirmed.amps == 16
        )
        await clock.advance(180)
        await eventually(lambda: station.values["ChargingEnabled"] == "false")
        assert settings.values["grid_request"] is None
        await settings.set("grid_duration", "00:00")
        await control.request("ChargingEnabled", "true")
        await clock.advance(0)
        await eventually(lambda: station.values["ChargingEnabled"] == "false")
        await settings.set("grid_duration", "00:01")
        await control.request("ChargingEnabled", "true")
        state = entry.runtime_data.state
        state.session["ended_at"] = datetime.now(UTC).isoformat()
        state.publish(("session", state.binding, "active"))
        await clock.advance(60)
        await eventually(lambda: station.values["ChargingEnabled"] == "false")


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
        assert len(station.profiles) == 1
        await clock.advance(59)
        assert len(station.profiles) == 1
        station.profile_result = "Accepted"
        await clock.advance(1)
        await eventually(lambda: grid.status == "confirmed")
        assert len(station.profiles) == 2
        station.profile_result = "Rejected"
        await entry.runtime_data.settings.set("soll_power", 5)
        await clock.advance(1)
        await eventually(lambda: grid.retry_at == 60)
        state = entry.runtime_data.state
        state.reset(connected=True, boot=True)
        await clock.advance(60)
        assert len(station.profiles) == 3
        assert grid.timer is None


async def test_reconnect_reconciles_same_offer_without_replaying(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        assert len(station.profiles) == 1
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
        await eventually(lambda: grid.status == "confirmed")
        assert not station.profiles
        assert grid.confirmed.amps == 16
        assert control.attributes("ControlAuthority")["command_status"] == "confirmed"


def test_disjoint_and_non_monotonic_boundaries_are_deterministic():
    disjoint = ranges(minimum=10, caps={1: 10, 3: 11})
    assert disjoint.resolve(4600).phases == 1
    assert disjoint.resolve(4601).phases == 3
    nonmonotonic = ranges(modes=(1, 2, 3), asymmetry=27, caps={1: 27, 2: 6, 3: 27})
    assert nonmonotonic.resolve(4000).phases == 1
    assert nonmonotonic.resolve(4486).phases == 3


@pytest.mark.parametrize(
    "direction,watts,amps",
    [
        ("down", 0, 0),
        ("down", 100, 0),
        ("down", 1379, 0),
        ("down", 1380, 6),
        ("down", 1500, 6),
        ("down", 1609, 6),
        ("down", 1610, 7),
        ("up", 0, 0),
        ("up", 100, 6),
        ("up", 1380, 6),
        ("up", 1400, 7),
        ("up", 1610, 7),
        ("nearest", 0, 0),
        ("nearest", 689, 0),
        ("nearest", 690, 6),
        ("nearest", 1494, 6),
        ("nearest", 1495, 7),
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

    interim = e.on_phase(1400, 3, "up")
    assert interim.phases == 3 and interim.amps == 6
    final = e.resolve(1400, "up")
    assert final.phases == 1 and final.amps == 7

    # A mode disabled by configuration does not become an eligible fallback.
    assert 3 not in ranges(caps={3: 0}).ranges


async def test_grid_down_below_minimum_sends_zero_offer(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        await entry.runtime_data.settings.set("power_approximation", "down")
        await entry.runtime_data.settings.set("soll_power", 0.1)
        await eventually(lambda: grid.status == "confirmed" and grid.confirmed.amps == 0)
        last = station.profiles[-1][1]["charging_schedule"][0]["charging_schedule_period"][0]
        assert last["limit"] == 0
        assert "number_phases" not in last
        count = len(station.profiles)
        await asyncio.sleep(0)
        assert len(station.profiles) == count
