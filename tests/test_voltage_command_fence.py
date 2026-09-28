"""Fresh voltage may change watts, but not a command's discrete phase/current."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from ocpp.v21 import call_result
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_beta2 import apply, prepare
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.control.commands import (
    CommandReason,
    CommandStatus,
)
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.core.capabilities import CurrentLimit
from custom_components.wallbox_manager.core.models import PhaseMode
from custom_components.wallbox_manager.core.telemetry import Quantity

PLANNED = Fraction("229.91243")
DRIFTED = Fraction("229.60152")


def voltage(control, target, value, *, invalid=None):
    state = control.runtime.get(target.station)
    now = datetime.now(UTC)
    observations = []
    for obs in state.observations:
        if obs.channel.quantity == Quantity.VOLTAGE_L1:
            if invalid == "missing":
                continue
            obs = replace(
                obs,
                value=value,
                observed_at=(
                    now + timedelta(seconds=10)
                    if invalid == "future"
                    else now - timedelta(seconds=60)
                    if invalid == "expired"
                    else now
                ),
                valid_until=now - timedelta(seconds=1)
                if invalid == "expired"
                else now + timedelta(seconds=60),
            )
        observations.append(obs)
    control.runtime._publish(replace(state, observations=tuple(observations)))


async def setup(grid):
    p, t, context, clock = await prepare(grid, soc=96)
    p.setting(t)["approximation"] = "up"
    measurements(p, t, pv=1300, load=0, actual=0, soc=96)
    voltage(context[0], t, PLANNED)
    return p, t, context, clock


async def test_first_start_accepts_normal_voltage_drift(grid):
    p, t, (c, _, peer, *_), _ = await setup(grid)
    planned = p.pv_plan(t)[3].point
    assert planned.current_a == 6 and planned.phase_voltages_v == (PLANNED,)

    def response(_):
        voltage(c, t, DRIFTED)
        return call_result.SetChargingProfile(status="Accepted")

    peer.profile_response = response
    result = await p.permission(t, True)
    assert result.status == CommandStatus.APPLIED
    point = c.confirmed_point(t)
    assert point.current_a == 6 and point.mode.count == 1
    assert point.offered_power_w == 6 * DRIFTED != planned.offered_power_w
    assert c.runtime.enabled(t) is True and p.pv_ongoing[t]
    assert not p.pv_startups and t not in c._unconfirmed_targets
    assert c.intent(t).fence_reason is None


async def test_regulation_accepts_same_discrete_point_at_new_voltage(grid):
    p, t, (c, _, peer, *_), _ = await setup(grid)
    await p.permission(t, True)
    previous = c.confirmed_point(t)
    measurements(p, t, pv=1700, load=0, actual=0, soc=96)

    def response(_):
        voltage(c, t, DRIFTED)
        return call_result.SetChargingProfile(status="Accepted")

    peer.profile_response = response
    await apply(p, t)
    point = c.confirmed_point(t)
    assert previous.current_a == 6 and point.current_a == 8
    assert point.mode == previous.mode and point.phase_voltages_v == (DRIFTED,)
    assert p.pv_ongoing[t]


@pytest.mark.parametrize("invalid", [None, "missing", "expired", "future", "zero"])
async def test_material_change_or_invalid_voltage_requires_reconciliation(
    grid, invalid
):
    p, t, (c, _, peer, *_), _ = await setup(grid)

    def response(_):
        voltage(c, t, 0 if invalid == "zero" else 200, invalid=invalid)
        return call_result.SetChargingProfile(status="Accepted")

    peer.profile_response = response
    result = await p.permission(t, True)
    assert result.reason == CommandReason.STALE
    assert c.intent(t).fence_reason == (
        "electrical_setpoint_changed" if invalid is None else "voltage_unavailable"
    )
    assert c.confirmed_point(t) is None and c.runtime.enabled(t) is False
    assert t in c._unconfirmed_targets and p.pv_retry_until[t] == 60
    if invalid is None:
        assert c.resolve(t)[1].point.current_a == 7


@pytest.mark.parametrize("change", ["capability", "limit", "authority", "generation"])
async def test_voltage_drift_does_not_hide_other_fence_changes(grid, change):
    p, t, (c, bound, peer, _, source, _), _ = await setup(grid)

    def response(_):
        voltage(c, t, DRIFTED)
        if change == "capability":
            source.snapshot = replace(
                source.snapshot, revision=source.snapshot.revision + 1
            )
        elif change == "limit":
            source.permitted = (CurrentLimit(PhaseMode.canonical(1), 0, 20, "test"),)
        elif change == "authority":
            c.runtime.observe_authority(
                bound.token,
                AuthorityObservation(
                    t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
                ),
            )
        else:
            c._edit(t, {"target_w": 0})
        return call_result.SetChargingProfile(status="Accepted")

    peer.profile_response = response
    result = await p.permission(t, True)
    assert result.reason == CommandReason.STALE
    assert c.confirmed_point(t) is None and c.runtime.enabled(t) is False


async def test_small_drift_across_current_step_is_still_material(grid):
    p, t, (c, _, peer, *_), _ = await setup(grid)
    # No percentage tolerance: even the same 0.31 V change is material when
    # NOT_BELOW would require 7 A rather than the previously resolved 6 A.
    measurements(p, t, pv=1379, load=0, actual=0, soc=96)
    assert p.pv_plan(t)[3].point.current_a == 6

    def response(_):
        voltage(c, t, DRIFTED)
        return call_result.SetChargingProfile(status="Accepted")

    peer.profile_response = response
    result = await p.permission(t, True)
    assert result.reason == CommandReason.STALE
    assert c.intent(t).fence_reason == "electrical_setpoint_changed"
    assert c.resolve(t)[1].point.current_a == 7
    assert p.pv_retry_until[t] == 60
