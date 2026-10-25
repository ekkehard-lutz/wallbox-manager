"""PV V3 pure policies, deterministic cycle replay, and superseded boundaries."""

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from test_grid_profile import ranges

from custom_components.wallbox_manager.grid_point import Point
from custom_components.wallbox_manager.pv_core import (
    Controller,
    Inputs,
    signed_storage,
)
from custom_components.wallbox_manager.pv_economics import Budget
from custom_components.wallbox_manager.pv_plan import (
    Planner,
    remaining_day_seconds,
    remaining_house_energy,
    target_soc,
)
from custom_components.wallbox_manager.pv_soc import clamp_target, soc_policy
from custom_components.wallbox_manager.settings import NUMBERS


def config(**changes):
    return {**{key: spec[1] for key, spec in NUMBERS.items()}, **changes}


def point(amps=6, phases=1):
    return Point(amps, phases, amps * phases * 230)


def inputs(**changes):
    return replace(Inputs(1380, 0, 0, True, 60, 50, 0, False), **changes)


@pytest.mark.parametrize("previous", ["STOP", "BALANCE", "FAST"])
@pytest.mark.parametrize("soc", [47.99, 48, 48.99, 49, 50, 50.99, 51, 51.01, 51.99, 52])
@pytest.mark.parametrize("surplus", [False, True])
def test_soc_full_matrix(previous, soc, surplus):
    # The beta.18 <=L STOP and inclusive M re-entry are explicitly superseded.
    expected = (
        "STOP"
        if soc < 48
        else "FAST"
        if soc >= 52
        else ("BALANCE" if soc > 51 and surplus else "STOP")
        if previous == "STOP"
        else "BALANCE"
        if previous == "FAST" and soc < 51
        else previous
    )
    result = soc_policy(50, 2, soc, previous, surplus=surplus)
    assert result.mode == expected
    assert (result.stop, result.low, result.middle, result.upper) == (48, 49, 51, 52)


def test_target_clamp_and_impossible_interval():
    assert clamp_target(0, 20, 2) == 22
    assert clamp_target(100, 20, 2) == 98
    with pytest.raises(ValueError):
        clamp_target(90, 99, 2)


@pytest.mark.parametrize(
    "charge,discharge,expected",
    [(0, 0, 0), (1, 0, 1), (0, 1, -1), (1, 1, 0), (500, 90, 0)],
)
def test_storage_pair_normalization(charge, discharge, expected):
    assert signed_storage(charge, discharge) == expected


@pytest.mark.parametrize("charge,discharge", [(None, 0), (0, None), (-1, 0)])
def test_storage_invalid_is_not_zero(charge, discharge):
    with pytest.raises(ValueError):
        signed_storage(charge, discharge)


@pytest.mark.parametrize(
    "watts,amps,phases",
    [(4000, 6, 3), (3911, 6, 3), (3910, 17, 1), (2300, 10, 1), (4830, 7, 3)],
)
def test_phase_first_ideal(watts, amps, phases):
    assert ranges().regulation_target(watts, point(6, 3)) == point(amps, phases)


@pytest.mark.parametrize("grid", [-101, -100, -1, 0, 1, 100])
def test_fast_explores_including_deadband_and_night(grid):
    core = Controller()
    c = point(6, 3)
    core.up_cycles = 2
    result = core.decide(
        inputs(ev=4140, imported=max(0, grid), exported=max(0, -grid)),
        config(),
        ranges(),
        c,
    )
    assert result.point == point(7, 3)
    assert result.reason == "grid_exploration"


def test_cadence_counts_eligible_cycles_and_never_unrelated_source_generations():
    core, c = Controller(), point(6, 3)
    for _ in range(2):
        assert core.decide(inputs(ev=4140), config(), ranges(), c).point == c
    result = core.decide(inputs(ev=4140), config(), ranges(), c)
    assert result.point == point(7, 3)
    core.sent(result.point, c)
    assert core.up_cycles == 0
    for _ in range(50):
        assert (
            core.decide(
                inputs(ev=4830), config(), ranges(), result.point, pending=True
            ).point
            == result.point
        )
    assert core.up_cycles == 0


@pytest.mark.parametrize(
    "uptake,threshold,allow",
    [
        (0, 50, False),
        (49.9, 50, False),
        (50, 50, True),
        (60, 50, True),
        (50, 70, False),
        (70, 70, True),
    ],
)
def test_full_ten_cycle_startup_and_uptake(uptake, threshold, allow):
    core, c = Controller(), point()
    settings = config(pv_ev_uptake_threshold_pct=threshold)
    core.sent(c, point(0))
    i = inputs(ev=1380 * uptake / 100)
    for cycle in range(1, 11):
        result = core.decide(i, settings, ranges(), c)
        assert result.point == c, cycle
        assert result.reason == "startup_hold"
    result = core.decide(i, settings, ranges(), c)
    assert result.point == (point(7) if allow else c)


def test_startup_blocks_downward_then_underconsumption_does_not():
    core, c = Controller(), point(16, 3)
    core.sent(c, point(0))
    i = inputs(ev=1000, imported=1500)
    for _ in range(10):
        assert core.decide(i, config(), ranges(), c).point == c
    assert core.decide(i, config(), ranges(), c).point.watts < c.watts


@pytest.mark.parametrize("cycle", [1, 3, 9, 10])
@pytest.mark.parametrize("cause", ["soc", "economic"])
def test_urgent_stops_override_startup(cycle, cause):
    core, c = Controller(), point()
    core.sent(c, point(0))
    core.startup = cycle - 1
    result = core.decide(
        inputs(soc=47 if cause == "soc" else 60),
        config(),
        ranges(),
        c,
        economic_stop=cause == "economic",
    )
    assert result.point.amps == 0


def test_unlock_and_cell_balancing_do_not_rearm_startup_or_ramp():
    core, c = Controller(), point(16, 3)
    for _ in range(6):
        result = core.decide(inputs(ev=0), config(), ranges(), c)
        assert result.point == c and result.reason == "ev_underconsumption"
    assert core.startup is None
    assert (
        core.decide(inputs(ev=0, imported=2000), config(), ranges(), c).point.watts
        < c.watts
    )


@pytest.mark.parametrize("delay", [0, 1, 3])
def test_downward_delay_additional_cycles_and_disappearing_condition(delay):
    core, c = Controller(), point(16, 3)
    settings = config(pv_decrease_delay_cycles=delay)
    for _ in range(delay):
        assert (
            core.decide(inputs(ev=11040, imported=3000), settings, ranges(), c).point
            == c
        )
    assert core.decide(
        inputs(ev=11040, imported=3000), settings, ranges(), c
    ).point == point(12, 3)
    core.decide(inputs(ev=11040), settings, ranges(), c)
    assert core.down_cycles is None


@pytest.mark.parametrize(
    "soc,expected", [(48.9, 11), (49, 12), (50, 12), (51, 12), (51.1, 13)]
)
def test_balance_bias(soc, expected):
    core, c = Controller(), point(12)
    core.mode, core.up_cycles = "BALANCE", 3
    result = core.decide(inputs(soc=soc, ev=2760), config(), ranges(), c)
    assert result.point == point(expected)


@pytest.mark.parametrize(
    "soc,storage,applied,pending,startup,stop",
    [
        (48.9, -1, point(), False, None, True),
        (49, -1, point(), False, None, False),
        (48.9, 0, point(), False, None, False),
        (48.9, 1, point(), False, None, False),
        (48.9, -1, point(7), False, None, False),
        (48.9, -1, point(), True, None, False),
        (48.9, -1, point(), False, 0, False),
        (47.9, 0, point(7, 3), True, 0, True),
    ],
)
def test_balance_stop_a_and_b(soc, storage, applied, pending, startup, stop):
    core = Controller()
    core.mode, core.startup = "BALANCE", startup
    result = core.decide(
        inputs(soc=soc, storage=storage), config(), ranges(), applied, pending=pending
    )
    assert (result.point.amps == 0) is stop
    if stop:
        assert core.mode == "STOP"
        assert core.decide(inputs(soc=51), config(), ranges(), point(0)).point.amps == 0


def test_sudden_forecast_target_increase_stops_immediately():
    core = Controller()
    core.mode = "BALANCE"
    assert (
        core.decide(inputs(soc=60, target=70), config(), ranges(), point(16)).point.amps
        == 0
    )


def test_battery_free_exact_contract_and_no_zero_start():
    core = Controller()
    i = inputs(
        battery=False, ev=0, imported=0, exported=0, soc=None, storage=None, target=None
    )
    assert core.decide(i, config(), ranges(), point(0)).point.amps == 0
    assert (
        core.decide(replace(i, exported=1), config(), ranges(), point(0)).point
        == point()
    )
    assert core.decide(
        replace(i, ev=2760, exported=0, imported=690), config(), ranges(), point(12)
    ).point == point(9)


def test_technical_modes_optional_2p_and_caps():
    core = Controller()
    core.up_cycles = 3
    e = ranges(modes=(1, 2, 3), caps={1: 6, 2: 12, 3: 0})
    result = core.decide(inputs(), config(), e, point())
    assert result.point == point(6, 2)


def test_planner_latches_forecast_updates_bounds_and_equal_t_equivalence():
    p = Planner()
    now = datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Vienna"))
    settings = config(estimated_daily_house_consumption_kwh=12)

    def plan(surplus=False, forecast=10000, clock=0):
        return p.calculate(
            "PV_OPTIMUM",
            settings,
            20,
            now,
            clock,
            surplus=surplus,
            forecast=forecast,
            capacity=10000,
        )

    assert plan() == 80
    assert p.phase == "BEFORE_SURPLUS"
    assert plan(True) == 40
    assert plan(False, 8000, 5) == 60  # New Solcast forecast replans on next tick.
    assert plan(False, 6000, 10) == 80
    assert p.phase == "FINISHED"
    assert plan(True, 20000, 15) == 80
    restored = Planner.restore(p.saved())
    assert restored.phase == "FINISHED"
    p = Planner()
    targets = [
        p.calculate(
            profile,
            config(soll_soc_speicher=22, optimum_lower_soc=22, optimum_upper_soc=22),
            20,
            now,
            0,
            forecast=10000,
            capacity=10000,
        )
        for profile in ("PV_SURPLUS", "PV_MAXIMUM", "PV_OPTIMUM")
    ]
    assert targets == [22, 22, 22]
    decisions = [
        Controller().decide(inputs(target=float(t)), config(), ranges(), point())
        for t in targets
    ]
    assert decisions[0] == decisions[1] == decisions[2]


@pytest.mark.parametrize("month,day,hours", [(3, 29, 23), (10, 25, 25), (6, 1, 24)])
def test_local_midnight_and_dst(month, day, hours):
    now = datetime(2026, month, day, tzinfo=ZoneInfo("Europe/Vienna"))
    assert remaining_day_seconds(now) == hours * 3600
    assert remaining_house_energy(24, remaining_day_seconds(now)) == hours * 1000
    assert target_soc(20, 80, 10000, 10000, 6000) == 40


def test_budget_entire_power_threshold_strict_reset_and_no_backfill():
    b = Budget()
    b.bind(["scope"])
    b.sample(0, 1000, 1380, active=True)
    b.sample(180, 1000, 1380, active=True)
    assert b.wh == 50 and b.exhaust(50)
    b.sample(185, 690, 1380, active=False)
    b.sample(200, 690, 1380, active=False)
    assert not b.reset
    b.sample(200.01, 690, 1380, active=False)
    assert b.wh == 0 and b.reset and b.stopped
    b.interrupt()
    b.sample(10000, 1000, 1380, active=True)
    assert b.wh == 0
    b.sample(10005, 1000, 1380, active=True)
    assert b.wh == pytest.approx(1000 * 5 / 3600)


def test_budget_scope_recovery_and_edit_does_not_erase_stop():
    b = Budget(scope=["tx"], wh=51)
    assert b.exhaust(50) and b.exhaust(100)
    restored = Budget.restore(b.saved())
    assert restored.wh == 51 and restored.stopped and restored.previous is None
    assert not restored.bind(["different-tx"])
    assert restored.bind(["new-tx"], ended=True)
    assert restored.wh == 0 and not restored.stopped
    assert Budget.restore({"corrupt": True}).stopped


@pytest.mark.parametrize(
    "storage,household,mode,expected",
    [
        (1, 500, "BALANCE", "battery_charging"),
        (0, 499, "FAST", "fast_low_household"),
        (-1000, 200, "FAST", "fast_low_household"),
        (0, 500, "FAST", None),
        (-1, 499, "BALANCE", None),
        (None, None, "FAST", None),
    ],
)
def test_economic_restart_no_extra_edge_or_timer(storage, household, mode, expected):
    b = Budget(stopped=True, reset=True)
    args = dict(off=True, mode=mode, battery_w=storage, household=household)
    assert b.restart_reason(**args) == expected
    assert b.restart_reason(**args) == expected
    assert b.restart_reason(**{**args, "off": False}) is None
    b.reset = False
    assert b.restart_reason(**args) is None


def test_balance_closed_loop_household_pulse_and_unlock_replay():
    core, c = Controller(), point()
    core.mode = "BALANCE"
    core.sent(c, point(0))
    requests = []
    for tick in range(300):
        # Inverter power compensates actual EV consumption, including six ticks
        # of vehicle unlock. No new sensor generations are invented by the core.
        ev = 0 if 60 <= tick < 66 else float(c.watts)
        house = 4000 if 40 <= tick < 43 else 1000
        battery_w = 5000 - house - ev
        d = core.decide(inputs(soc=50, ev=ev, storage=battery_w), config(), ranges(), c)
        if d.point.watts > c.watts:
            assert d.point.amps == c.amps + 1
            assert d.point.phases == c.phases
            assert ev >= float(c.watts) / 2
        if d.point != c:
            core.sent(d.point, c)
            requests.append((tick, d.point))
            c = d.point
        if 60 <= tick < 66:
            assert d.point.amps <= c.amps
    assert c == point(17)
    assert all(p == point(17) for t, p in requests if t >= 100)
    assert len(requests) < 40


def test_planning_300_second_cadence_and_midnight_daily_reset():
    from datetime import timedelta

    p = Planner()
    now = datetime(2026, 6, 1, 12, tzinfo=ZoneInfo("Europe/Vienna"))
    settings = config(estimated_daily_house_consumption_kwh=12)
    first = p.calculate(
        "PV_OPTIMUM", settings, 20, now, 0, surplus=True, forecast=10000, capacity=10000
    )
    assert (
        p.calculate(
            "PV_OPTIMUM",
            settings,
            20,
            now + timedelta(seconds=299),
            299,
            forecast=10000,
            capacity=10000,
        )
        == first
    )
    assert (
        p.calculate(
            "PV_OPTIMUM",
            settings,
            20,
            now + timedelta(seconds=300),
            300,
            forecast=10000,
            capacity=10000,
        )
        < first
    )
    p.phase = "FINISHED"
    tomorrow = now + timedelta(days=1)
    assert (
        p.calculate(
            "PV_OPTIMUM", settings, 20, tomorrow, 86400, forecast=10000, capacity=10000
        )
        == 80
    )
    assert p.phase == "BEFORE_SURPLUS"


def legacy_economics():
    return dict(
        version=1,
        records=[
            dict(
                identity=dict(
                    entry_id="entry",
                    station="Wallbox01",
                    evse="1",
                    connector="1",
                    transaction="tx",
                    station_identity=dict(
                        vendor="Test", model="Passive", serial="serial-1", firmware=None
                    ),
                ),
                policy=dict(
                    version=2, scope="pv_charging", reset_seconds=15, reset_strict=True
                ),
                state=dict(
                    version=2,
                    energy_ws=dict(numerator=183600, denominator=1),
                    paused=True,
                    restart_required=True,
                    off_pending=False,
                    off_in_flight=False,
                ),
            )
        ],
    )


def test_beta18_journals_preserve_wh_stop_identity_and_finished_day():
    import json

    from custom_components.wallbox_manager.pv_migration import legacy_record

    binding = ("Wallbox01", "1", "1")
    days = dict(
        version=2,
        days={json.dumps(list(binding)): dict(date="2026-10-25", state="FINISHED")},
    )
    record = legacy_record(legacy_economics(), days, "entry", binding)
    b = Budget.restore(record["economics"])
    assert b.wh == 51 and b.stopped and b.previous is None and not b.reset
    assert b.scope == [*binding, "remote", "tx"]
    assert record["station_identity"]["serial_number"] == "serial-1"
    assert Planner.restore(record["day"]).phase == "FINISHED"


@pytest.mark.parametrize(
    "invalid", ["version", "identity", "energy", "flags", "hardware"]
)
def test_invalid_beta18_history_inhibits_automatic_positive(invalid):
    from custom_components.wallbox_manager.pv_migration import legacy_record

    old = legacy_economics()
    item = old["records"][0]
    if invalid == "version":
        old["version"] = 99
    elif invalid == "identity":
        item["identity"]["entry_id"] = "other"
    elif invalid == "energy":
        item["state"]["energy_ws"]["denominator"] = 0
    elif invalid == "hardware":
        item["identity"]["station_identity"] = {}
    else:
        item["state"]["restart_required"] = None
    record = legacy_record(old, None, "entry", ("Wallbox01", "1", "1"))
    b = Budget.restore(record["economics"])
    assert b.stopped and not b.bind(["Wallbox01", "1", "1", "remote", "tx"])
