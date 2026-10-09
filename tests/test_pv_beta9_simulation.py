"""Deterministic 1 s controller / 5 s site / 10 s EV closed-loop experiments.

The beta.8 comparison uses a frozen test-only reference and its floor rule.
Hardware timings and proposed acceptance bounds are documented in the report.
"""

import json
from dataclasses import dataclass

import pytest
from beta8_regulator_reference import Beta8Regulator
from test_pv_beta9 import evidence, point

from custom_components.wallbox_manager.pv_regulators import FastDischargeRegulator


@dataclass
class Metrics:
    commands: int
    reversals: int
    settling_s: int
    final_import_w: float
    import_peak_w: float
    excess_import_seconds: int
    battery_peak_w: float
    unsafe_selections: int | None
    final_ev_w: float


def simulate(
    *,
    legacy=False,
    scenario="constant",
    ack_delay=1,
    response_delay=4,
    ev_offset=0,
    variable_latency=False,
):
    regulator = Beta8Regulator() if legacy else FastDischargeRegulator()
    physical = 1380
    confirmed = point(6)
    pending = None
    responses = []
    discharge = 1880.0
    ev, d, g, load, pv = physical, discharge, 0, physical + 500, 0
    ev_time, site_time = 0, 0
    points, grid_trace, batteries = [], [], []
    commands = reversals = unsafe = 0
    last_direction = 0
    for second in range(360):
        household = 500
        production = 0
        if scenario == "load_up" and second >= 120:
            household = 1700
        elif scenario == "load_down":
            household = 1700 if second < 120 else 500
        elif scenario == "pv":
            production = 1800 if 120 <= second < 240 else 0
        elif scenario == "near_limit":
            household = 2000
        if pending and second >= pending[0]:
            confirmed = pending[1]
            pending = None
        while responses and second >= responses[0][0]:
            physical = float(responses.pop(0)[1].offered_power_w)
        net_load = household + physical - production
        desired_discharge = min(3500, max(0, net_load))
        # Battery response is delayed too; the meter sees the residual grid flow.
        discharge += (desired_discharge - discharge) / 2
        grid = net_load - discharge
        if second % 5 == 0:
            d, g, load, pv = discharge, grid, household + physical, production
            site_time = second
        if second % 10 == ev_offset:
            ev, ev_time = physical, second
        kwargs = (
            {}
            if legacy
            else dict(
                evidence=evidence(ev_time, site_time, pv=pv, load=load),
                confirmed=confirmed,
                pending=pending is not None,
            )
        )
        watts = regulator.request(
            ev,
            d,
            3500,
            max(0, g),
            max(0, -g),
            now=second,
            interval=5,
            **kwargs,
        )
        minimum = point(6)
        amps = max(6, min(16, int(watts / 230)))
        selected = point(amps)
        if not legacy:
            if selected.offered_power_w > regulator.hard_max:
                amps = min(16, int(regulator.hard_max / 230))
                selected = point(amps if amps >= 6 else 0)
            if selected.offered_power_w <= 1380 and not regulator.minimum_allowed(
                minimum, now=second, actual=ev
            ):
                selected = point(0)
            unsafe += int(selected.offered_power_w > regulator.hard_max)
        if pending is None and not selected.same_setpoint(confirmed):
            delta = selected.offered_power_w - confirmed.offered_power_w
            direction = 1 if delta > 0 else -1
            reversals += int(bool(last_direction) and last_direction != direction)
            last_direction = direction
            commands += 1
            ack = 1 + commands % 3 if variable_latency else ack_delay
            response = 4 + (commands * 5) % 12 if variable_latency else response_delay
            pending = (second + ack, selected)
            responses.append((second + ack + response, selected))
        points.append(float(confirmed.offered_power_w))
        grid_trace.append(max(0, grid))
        batteries.append(discharge)
    # Last change in the final stationary segment; 120 s after final disturbance
    # is a deliberately loose regression bound, not an existing product promise.
    disturbance = 240 if scenario == "pv" else 120 if scenario != "constant" else 0
    changes = [s for s in range(disturbance + 1, 360) if points[s] != points[s - 1]]
    settling = max(changes, default=disturbance) - disturbance
    metrics = Metrics(
        commands,
        reversals,
        settling,
        round(sum(grid_trace[-60:]) / 60, 2),
        round(max(grid_trace), 2),
        sum(v > 100 for v in grid_trace),
        round(max(batteries), 2),
        None if legacy else unsafe,
        points[-1],
    )
    return metrics, points


@pytest.mark.parametrize(
    "scenario", ["constant", "load_up", "load_down", "pv", "near_limit"]
)
@pytest.mark.parametrize("ack_delay,response_delay", [(1, 4), (3, 8), (1, 15)])
@pytest.mark.parametrize("ev_offset", [0, 3])
def test_asynchronous_streams_settle(scenario, ack_delay, response_delay, ev_offset):
    metrics, points = simulate(
        scenario=scenario,
        ack_delay=ack_delay,
        response_delay=response_delay,
        ev_offset=ev_offset,
    )
    assert metrics.unsafe_selections == 0
    assert metrics.battery_peak_w <= 3500
    assert len(set(points[-60:])) == 1
    assert metrics.settling_s <= 120
    # At a positive minimum the explicitly allowed site import is assessed.
    assert metrics.final_import_w <= max(
        100, points[-1] / 2 if points[-1] == 1380 else 0
    )


@pytest.mark.parametrize(
    "scenario", ["constant", "load_up", "load_down", "pv", "near_limit"]
)
def test_command_latencies_vary_within_the_same_trace(scenario):
    metrics, points = simulate(scenario=scenario, ev_offset=3, variable_latency=True)
    assert metrics.unsafe_selections == 0
    assert metrics.battery_peak_w <= 3500
    assert len(set(points[-60:])) == 1
    assert metrics.settling_s <= 120


if __name__ == "__main__":
    experiments = {
        f"{scenario}/{ack}/{response}" + ("/offset3" if offset else ""): dict(
            scenario=scenario, ack_delay=ack, response_delay=response, ev_offset=offset
        )
        for scenario in ("constant", "load_up", "load_down", "pv", "near_limit")
        for ack, response in ((1, 4), (3, 8), (1, 15))
        for offset in (0, 3)
    }
    experiments.update(
        {
            f"{scenario}/variable/offset3": dict(
                scenario=scenario, ev_offset=3, variable_latency=True
            )
            for scenario in ("constant", "load_up", "load_down", "pv", "near_limit")
        }
    )
    print(
        json.dumps(
            {
                name: {
                    label: vars(simulate(legacy=legacy, **params)[0])
                    for label, legacy in (("beta8", True), ("beta9", False))
                }
                for name, params in experiments.items()
            },
            indent=2,
        )
    )
