"""Profile mappings, target policy and common execution remain compatible."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from itertools import product
from types import SimpleNamespace

import pytest
from homeassistant.util import dt as dt_util
from ocpp.v21 import call_result
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_optimum import setup_optimum
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import grid_measurements, measurements

from custom_components.wallbox_manager.control.requests import Direction
from custom_components.wallbox_manager.control.runtime import PowerSettings
from custom_components.wallbox_manager.profiles import GridProfiles
from custom_components.wallbox_manager.pv_optimum import PVDay
from custom_components.wallbox_manager.select import ChargingProfile

LEGACY_POWER = ("leistung_pv", "leistung_verbraucher")
GRID_POWER = ("grid_import_power", "grid_export_power")
BATTERY_EXECUTION = (
    "min_soc_speicher",
    "soc_speicher_aktuell",
    *LEGACY_POWER,
    "storage_discharge_power",
    *GRID_POWER,
)
DYNAMIC_PLANNING = ("storage_capacity", "remaining_pv_energy")


def available(references):
    """Availability depends on configured mappings, rather than live readings."""
    return GridProfiles.available_profiles(SimpleNamespace(references=references), None)


def mapped(keys):
    return {key: f"sensor.{key}" for key in keys}


@pytest.mark.parametrize("enabled", tuple(product((False, True), repeat=4)))
def test_surplus_preserves_legacy_and_accepts_measured_grid_without_battery(enabled):
    keys = (*LEGACY_POWER, *GRID_POWER)
    references = mapped(
        key for key, present in zip(keys, enabled, strict=True) if present
    )
    surplus = all(enabled[:2]) or all(enabled[2:])
    assert available(references) == (["NETZ", "PV_SURPLUS"] if surplus else ["NETZ"])


@pytest.mark.parametrize("missing", BATTERY_EXECUTION)
def test_maximum_requires_each_existing_battery_execution_mapping(missing):
    references = mapped(BATTERY_EXECUTION)
    references.pop(missing)
    assert "PV_MAXIMUM" not in available(references)
    assert "PV_OPTIMUM" not in available(references)


@pytest.mark.parametrize("missing", (*BATTERY_EXECUTION, *DYNAMIC_PLANNING))
def test_optimum_requires_complete_battery_and_dynamic_forecast_mapping(missing):
    references = mapped((*BATTERY_EXECUTION, *DYNAMIC_PLANNING))
    references.pop(missing)
    assert "PV_OPTIMUM" not in available(references)


def test_battery_without_forecast_offers_only_surplus_and_maximum():
    assert available(mapped(BATTERY_EXECUTION)) == ["NETZ", "PV_SURPLUS", "PV_MAXIMUM"]


def test_complete_dynamic_mapping_preserves_existing_public_profile_order():
    assert available(mapped((*BATTERY_EXECUTION, *DYNAMIC_PLANNING))) == [
        "NETZ",
        "PV_SURPLUS",
        "PV_OPTIMUM",
        "PV_MAXIMUM",
    ]


@pytest.mark.parametrize("keys", [LEGACY_POWER, GRID_POWER, BATTERY_EXECUTION])
async def test_public_profile_entity_options_follow_this_entry_mappings(grid, keys):
    p, t, _ = grid
    p.references = mapped(keys)
    profile = ChargingProfile(p.control, p.entry_id, t)
    assert profile.options == available(p.references)
    assert "PV_SURPLUS" in profile.options
    assert "PV_OPTIMUM" not in profile.options
    # Unknown live state must inhibit execution, without making options flicker.
    for entity in p.references.values():
        p.hass.states.async_set(entity, "unavailable")
    assert profile.options == available(p.references)


@pytest.mark.parametrize(
    "configured,expected", [(-5, 7), (7, 7), (35, 35), (98, 98), (100, 98)]
)
async def test_surplus_target_projects_to_reserve_hysteresis_bounds(
    grid, configured, expected
):
    p, t, _ = grid
    setup_optimum(p, t)
    p.setting(t).update(profile="PV_SURPLUS", soll_soc_speicher=configured)
    assert p.pv_target(t, datetime.now(UTC)) == expected
    assert p.setting(t)["soll_soc_speicher"] == expected


@pytest.mark.parametrize("requested", [6, 99])
async def test_surplus_public_target_control_rejects_values_outside_bounds(
    grid, requested
):
    p, t, _ = grid
    setup_optimum(p, t)
    p.setting(t)["profile"] = "PV_SURPLUS"
    before = p.setting(t)["soll_soc_speicher"]
    with pytest.raises(ValueError):
        await p.set_value(t, "soll_soc_speicher", requested)
    assert p.setting(t)["soll_soc_speicher"] == before


@pytest.mark.parametrize("reserve,hysteresis", [(0, 2), (5, 2), (20, 5), (90, 5)])
async def test_maximum_target_is_live_reserve_plus_hysteresis(
    grid, reserve, hysteresis
):
    p, t, _ = grid
    setup_optimum(p, t)
    p.hass.states.async_set("number.reserve", reserve)
    p.setting(t).update(profile="PV_MAXIMUM", soc_hysterese=hysteresis)
    assert p.pv_target(t, datetime.now(UTC)) == reserve + hysteresis


@pytest.mark.parametrize(
    "soc,previous,pv,load,actual,imported,discharge",
    [
        (6, "FAST_DISCHARGE", 8000, 5000, 3000, 0, 1000),
        (7, "STOP", 8000, 5000, 3000, 0, 1000),
        (8, "STOP", 8000, 5000, 3000, 0, 1000),
        (8, "PV_BALANCE", 1000, 5000, 3000, 250, 1000),
        (10, "STOP", 8000, 5000, 3000, 0, 1000),
        (10, "FAST_DISCHARGE", 1000, 5000, 3000, 700, 2500),
    ],
)
async def test_surplus_matches_maximum_at_equal_target_and_identical_inputs(
    grid, soc, previous, pv, load, actual, imported, discharge
):
    p, t, _ = grid
    setup_optimum(p, t, soc=soc, pv=pv, load=load, actual=actual)
    grid_measurements(p, imported=imported)
    p.hass.states.async_set(
        "sensor.storage_discharge_power", discharge, {"unit_of_measurement": "W"}
    )
    p.setting(t).update(soll_soc_speicher=7, approximation="up")
    p.monotonic = lambda: 100
    session = p.control.runtime.sessions.get(t)
    p.pv_sessions[t] = session.session_id
    snapshots = []
    for profile in ("PV_MAXIMUM", "PV_SURPLUS"):
        p.setting(t)["profile"] = profile
        p.optimum_modes[t] = previous
        p.pv_ongoing[t] = previous != "STOP"
        p.optimum_initializations.discard(t)
        p.optimum_regulators.pop(t, None)
        p.pv_start_since.pop(t, None)
        p.pv_stop_since.pop(t, None)
        power, direction, status, result = p.pv_plan(t, advance=False)
        point = result.point if result else None
        snapshots.append(
            (
                p.optimum_targets[t],
                p.optimum_modes[t],
                power,
                direction,
                status,
                (point.mode, point.current_a, point.offered_power_w) if point else None,
            )
        )
        assert p.shared_pv_execution(t)
        assert direction == Direction.DOWN
    assert snapshots[0] == snapshots[1]


@pytest.mark.parametrize(
    "actual,imported,exported,expected",
    [(1840, 240, 0, 1600), (0, 0, 2300, 2300), (1840, 2300, 0, 0)],
)
async def test_surplus_without_battery_uses_actual_grid_and_selected_ev_only(
    grid, actual, imported, exported, expected
):
    p, t, _ = grid
    measurements(p, t, actual=actual)
    grid_measurements(p, imported=imported, exported=exported)
    for key in (*LEGACY_POWER, "min_soc_speicher", "soc_speicher_aktuell"):
        p.references.pop(key, None)
    # Deliberately unusable PV/load readings cannot affect this supported mapping.
    p.hass.states.async_set("sensor.pv", "unavailable")
    p.hass.states.async_set("sensor.load", "unavailable")
    p.setting(t)["profile"] = "PV_SURPLUS"
    assert "PV_SURPLUS" in p.available_profiles(t)
    power, _, status = p.pv_request(t)
    assert power == expected
    assert status != "measurements_unavailable"
    assert not p.common_pv(t)


@pytest.mark.parametrize(
    "capacity,capacity_unit,forecast,forecast_unit",
    [
        ("11059", "Wh", "4423.6", "Wh"),
        ("11.059", "kWh", "4.4236", "kWh"),
        ("0.011059", "MWh", "0.0044236", "MWh"),
    ],
)
async def test_optimum_preserves_dynamic_algorithm_and_normalizes_energy_to_wh(
    grid, capacity, capacity_unit, forecast, forecast_unit
):
    p, t, _ = grid
    now = setup_optimum(p, t, soc=90, actual=3000)
    p.optimum_days[t] = PVDay(dt_util.as_local(now).date().isoformat(), "DYNAMIC")
    p.hass.states.async_set(
        "sensor.storage_capacity", capacity, {"unit_of_measurement": capacity_unit}
    )
    p.hass.states.async_set(
        "sensor.remaining_pv_energy", forecast, {"unit_of_measurement": forecast_unit}
    )
    desired = p.pv_target(t, datetime.now(UTC))
    assert desired == Fraction(40)
    assert p.optimum_forecasts[t]["remaining_pv_wh"] == 4423.6
    assert p.optimum_day_for(t).state == "DYNAMIC"
    assert "PV_OPTIMUM" in p.available_profiles(t)


async def test_forecast_invalid_energy_unit_inhibits_request_without_hiding_option(
    grid,
):
    p, t, _ = grid
    now = setup_optimum(p, t)
    p.optimum_days[t] = PVDay(dt_util.as_local(now).date().isoformat(), "DYNAMIC")
    p.hass.states.async_set(
        "sensor.remaining_pv_energy", 4423.6, {"unit_of_measurement": "W"}
    )
    assert "PV_OPTIMUM" in p.available_profiles(t)
    assert p.pv_request(t)[2] == "measurements_unavailable"


@pytest.mark.parametrize("older_phase_retry", [False, True])
async def test_economic_off_busy_honors_retry_without_delaying_first_off(
    grid, older_phase_retry
):
    from test_pv_economy_control import exhausted, lock_current_phase
    from test_pv_optimum_hold import period

    from custom_components.wallbox_manager.control.commands import CommandStatus
    from custom_components.wallbox_manager.pv_economy import LIMIT_WS

    p, t, (c, _, peer, *_), clock = await exhausted(grid)
    policy = p.economic_policies[t]
    if older_phase_retry:
        lock_current_phase(c, t)
        c.intent(t).phase_retry = True
        p.pv_retry_until[t] = 120
        p.pv_retry_request[t] = PowerSettings(2300, Direction.DOWN)
        p.pv_phase_retry[t] = True
    peer.profile_response = lambda _: call_result.SetChargingProfile(
        status="Rejected", status_info={"reason_code": "Busy"}
    )
    parked, gate = asyncio.Queue(), asyncio.Event()

    async def wait(_):
        parked.put_nowait(None)
        await gate.wait()
        gate.clear()

    p.wait = wait
    p.launch = GridProfiles.launch.__get__(p)
    before = len(peer.requests)
    p.launch(t)
    await asyncio.wait_for(parked.get(), 2)
    assert len(peer.requests) == before + 1
    assert period(peer.requests[-1])["limit"] == 0
    assert c.intent(t).command_result.status == CommandStatus.TEMPORARILY_REJECTED
    assert c.confirmed_point(t).charging
    assert policy.active and policy.off_pending and policy.energy_ws == LIMIT_WS
    assert p.pv_retry_until[t] == 60
    for second in (1, 59):
        clock[0] = second
        gate.set()
        await asyncio.wait_for(parked.get(), 2)
        assert len(peer.requests) == before + 1
        assert policy.active and policy.off_pending and policy.energy_ws == LIMIT_WS
    clock[0] = 60
    peer.profile_response = None
    gate.set()
    await asyncio.wait_for(parked.get(), 2)
    assert len(peer.requests) == before + 2
    assert c.intent(t).command_result.status == CommandStatus.APPLIED
    assert not c.confirmed_point(t).charging
    assert policy.paused and not policy.active
    assert policy.energy_ws == LIMIT_WS
    assert t not in p.pv_retry_until
    clock[0] = 61
    gate.set()
    await asyncio.wait_for(parked.get(), 2)
    assert len(peer.requests) == before + 2


async def test_retained_disabled_offer_cannot_waive_startup_response_reserve(grid):
    from test_pv_optimum_hold import configure, samples

    from custom_components.wallbox_manager.control.commands import CommandStatus

    p, t, (c, _, peer, *_), _ = configure(grid)
    samples(p, t, actual=2300, discharge=3400)
    await p.permission(t, True)
    await p.permission(t, False)
    assert c.runtime.enabled(t) is False
    # Establish a fresh positive setpoint acknowledgement while permission is
    # disabled, as the existing explicit startup preparation can legitimately do.
    p.setting(t)["profile"] = "NETZ"
    c._edit(t, {"target_w": Fraction(2300)})
    assert (await c.apply_stored(t, prepare=True)).status == CommandStatus.APPLIED
    p.setting(t)["profile"] = "PV_OPTIMUM"
    assert c._confirmed_points[t][0].charging
    assert c.confirmed_point(t) is None  # Disabled permission is not continuation.
    p.setting(t)["optimum_max_discharge_w"] = 2000
    samples(p, t, actual=0, pv=0, load=0, discharge=0)
    assert await p.economic_prepare(t, resume=False)
    before = len(peer.requests)
    result = p.pv_edit(t)
    assert c.intent(t).hard_max_w == 1900
    assert p.status[t] == "startup_response_reserve"
    assert result is not None and not result.point.charging
    assert c.intent(t).minimum_exception is None
    assert len(peer.requests) == before


@pytest.mark.parametrize("profile", ["PV_MAXIMUM", "PV_SURPLUS", "PV_OPTIMUM"])
async def test_fast_battery_floor_start_keeps_existing_house_import_eligibility(
    grid, profile
):
    from test_pv_economy_control import cohort

    from custom_components.wallbox_manager.control.commands import CommandStatus

    p, t, (c, _, _, *_) = grid
    clock = [0]
    p.monotonic = lambda: clock[0]
    setup_optimum(p, t, soc=90, pv=500, load=2000, actual=0)
    p.setting(t).update(profile=profile, soll_soc_speicher=7)
    # Establish today's existing planner phase before reporting a new cohort;
    # a date-initialization callback must not consume a partial fixture cohort.
    p.optimum_days[t] = PVDay(
        dt_util.as_local(datetime.now(UTC)).date().isoformat(), "BEFORE_SURPLUS"
    )
    cohort(
        p,
        t,
        clock,
        0,
        source=datetime.now(UTC),
        actual=0,
        imported=1000,
        discharge=500,
        pv=500,
        load=2000,
    )
    p.launch = lambda *args, **kwargs: None
    # Normal FAST optimization still corrects site import toward zero; the
    # original independently proved startup floor can use battery allocation.
    assert p.pv_request(t)[0] == 0
    assert c.intent(t).hard_max_w == 2900
    assert (await p.permission(t, True)).status == CommandStatus.APPLIED
    assert c.confirmed_point(t).current_a == 6
    assert c.confirmed_point(t).mode.count == 1
    assert c.runtime.enabled(t) is True
    assert p.economic_policies[t].energy_ws == 0


@pytest.mark.parametrize("profile", ["PV_MAXIMUM", "PV_SURPLUS", "PV_OPTIMUM"])
async def test_permission_before_transaction_starts_floor_then_requires_ev_response(
    grid, profile
):
    from test_ocpp21_control import transaction
    from test_pv_economy_control import cohort

    from custom_components.wallbox_manager.control.commands import CommandStatus

    p, t, (c, _, peer, *_) = grid
    clock = [0]
    p.monotonic = lambda: clock[0]
    setup_optimum(p, t, soc=90, pv=500, load=2000, actual=0)
    p.setting(t).update(profile=profile, soll_soc_speicher=7)
    p.optimum_days[t] = PVDay(
        dt_util.as_local(datetime.now(UTC)).date().isoformat(), "BEFORE_SURPLUS"
    )
    await transaction(peer, connector=1, kind="Ended")
    await p.hass.async_block_till_done()

    startup_waits, regulation_waits = asyncio.Queue(), asyncio.Queue()
    startup_release, regulation_release = asyncio.Event(), asyncio.Event()

    async def startup_wait(seconds):
        startup_waits.put_nowait(seconds)
        await startup_release.wait()
        startup_release.clear()

    async def regulation_wait(seconds):
        regulation_waits.put_nowait(seconds)
        await regulation_release.wait()
        regulation_release.clear()

    async def wait(seconds):
        if t in p.pv_startups:
            await startup_wait(seconds)
        else:
            await regulation_wait(seconds)

    p.wait = wait
    peer.operations.clear()
    assert (await p.permission(t, True)).status == CommandStatus.APPLIED
    assert await asyncio.wait_for(startup_waits.get(), 1) == 60
    assert c.runtime.enabled(t) is True
    assert c.blocker(t) == "transaction_unavailable"
    assert c.confirmed_point(t) is None and not peer.requests
    assert peer.operations == ["permission", "enabled_get"]
    assert t not in p.economic_policies

    await transaction(peer, identity="floor-start", connector=1)
    cohort(
        p,
        t,
        clock,
        60,
        source=datetime.now(UTC),
        actual=0,
        imported=1000,
        discharge=500,
        pv=500,
        load=2000,
    )
    await p.hass.async_block_till_done()
    startup_release.set()
    await asyncio.wait_for(regulation_waits.get(), 1)
    point = c.confirmed_point(t)
    assert point.current_a == 6 and point.mode.count == 1
    assert p.pv_ongoing[t] and t not in p.pv_startups
    assert t not in p.enable_requests
    assert peer.operations == ["permission", "enabled_get", "profile"]
    assert peer.requests[-1][1]["transaction_id"] == "floor-start"
    assert p.economic_identities[t].transaction_id == "floor-start"
    regulator = p.optimum_regulators[t]
    assert not regulator.response_observed  # Point confirmation is not EV response.
    assert p.economic_policies[t].energy_ws == 0

    # Fresh headroom with unchanged EV power cannot turn the ACK into an increase.
    count = len(peer.requests)
    cohort(
        p,
        t,
        clock,
        61,
        source=datetime.now(UTC),
        actual=0,
        discharge=0,
        pv=5000,
        load=500,
    )
    await p.hass.async_block_till_done()
    regulation_release.set()
    await asyncio.wait_for(regulation_waits.get(), 1)
    assert c.confirmed_point(t).same_setpoint(point)
    assert len(peer.requests) == count and not regulator.response_observed

    # A fresh measured response and the ordinary regulation interval allow a rise.
    cohort(
        p,
        t,
        clock,
        66,
        source=datetime.now(UTC),
        actual=1380,
        discharge=1000,
        pv=5000,
        load=1880,
    )
    await p.hass.async_block_till_done()
    regulation_release.set()
    await asyncio.wait_for(regulation_waits.get(), 1)
    assert c.confirmed_point(t).offered_power_w > point.offered_power_w
    assert len(peer.requests) == count + 1
    assert not regulator.response_observed  # New offer needs its own response.

    # F2: a real battery deficit still reduces the offer during response settling.
    cohort(
        p,
        t,
        clock,
        67,
        source=datetime.now(UTC),
        actual=1380,
        discharge=6000,
        pv=5000,
        load=1880,
    )
    await p.hass.async_block_till_done()
    regulation_release.set()
    await asyncio.wait_for(regulation_waits.get(), 1)
    assert c.confirmed_point(t).current_a == 6
    assert len(peer.requests) == count + 2
    assert c.minimum_exception_valid(t, c.confirmed_point(t))
    assert c.runtime.enabled(t) is True


@pytest.mark.parametrize("profile", ["PV_MAXIMUM", "PV_SURPLUS", "PV_OPTIMUM"])
async def test_floor_start_uncertain_ack_cannot_become_a_new_startup(grid, profile):
    from custom_components.wallbox_manager.control.commands import CommandStatus

    await test_fast_battery_floor_start_keeps_existing_house_import_eligibility(
        grid, profile
    )
    p, t, (c, _, peer, _, source, _) = grid
    await p.permission(t, False)
    parked, release = asyncio.Queue(), asyncio.Event()

    async def wait(seconds):
        parked.put_nowait(seconds)
        await release.wait()
        release.clear()

    p.wait = wait

    def uncertain(_):
        source.snapshot = replace(
            source.snapshot, revision=source.snapshot.revision + 1
        )
        return call_result.SetChargingProfile(status="Accepted")

    peer.profile_response = uncertain
    before = len(peer.requests)
    assert (await p.permission(t, True)).status == CommandStatus.APPLIED
    assert await asyncio.wait_for(parked.get(), 1) == 60
    assert len(peer.requests) == before + 1
    assert c.runtime.enabled(t) is True and c.confirmed_point(t) is None
    assert t in c._unconfirmed_targets and t in p.pv_startups
    # A real accepted write with invalid final context requires reconciliation;
    # the still-pending explicit activation cannot pass the first-start guard.
    assert p.pv_edit(t) is None
    assert p.status[t] == "awaiting_physical_response"
    assert c.confirmed_point(t) is None and len(peer.requests) == before + 1


@pytest.mark.parametrize("improvement", [100, 500])
async def test_economic_restart_uses_only_exact_minimum_after_genuine_improvement(
    grid, improvement
):
    from test_pv_economy_control import cohort, paused_reset
    from test_pv_optimum_hold import evaluate

    from custom_components.wallbox_manager.control.commands import CommandStatus

    p, t, (c, _, peer, *_), clock = await paused_reset(grid)
    policy = p.economic_policies[t]
    reference = policy.restart_reference
    p.pv_request(t)
    cohort(
        p,
        t,
        clock,
        18,
        source=datetime.now(UTC),
        actual=0,
        discharge=0,
        imported=0,
        exported=improvement,
        pv=1000 + improvement,
        load=1000,
    )
    await p.hass.async_block_till_done()
    power, direction, _ = p.pv_request(t)
    preferred = c.resolve(t, request=PowerSettings(power, direction), reachable=True)[
        1
    ].point
    minimum = p.economic_minimum(t).point
    assert preferred.offered_power_w > minimum.offered_power_w
    assert not p.economic_restart_permits(t, preferred)
    before = len(peer.requests)
    assert (await evaluate(p, t)).status == CommandStatus.APPLIED
    if improvement > 100:
        assert c.confirmed_point(t).current_a == 6
        assert c.confirmed_point(t).mode.count == 1
        assert p.status[t] == "optimum_minimum_hold"
        assert not policy.restart_required
        assert policy.restart_reference is None
        assert len(peer.requests) == before + 1
    else:
        assert not c.confirmed_point(t).charging
        assert p.status[t] == "economic_restart_inhibited"
        assert policy.restart_required
        assert policy.restart_reference == reference
        assert len(peer.requests) == before
