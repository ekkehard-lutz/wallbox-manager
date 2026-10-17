"""Isolated work counters for canonical metering and the single PV cycle."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_beta12 import authority
from test_pv_optimum import setup_optimum
from test_pv_optimum_hold import configure
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager.core.authority import ControlAuthority
from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.core.sessions import (
    SessionEvent,
    SessionEventKind,
)
from custom_components.wallbox_manager.core.telemetry import (
    Channel,
    Observation,
    Quantity,
    State,
)
from custom_components.wallbox_manager.protocols.ocpp.v21 import enabled
from custom_components.wallbox_manager.runtime import Runtime


async def test_internal_power_needs_ledger_lookup_and_no_ha_sensor_scan(
    grid, monkeypatch
):
    (
        p,
        target,
        (control, *_),
    ) = grid
    setup_optimum(p, target, actual=3000)
    ledger = control.runtime.sessions
    lookup = Mock(wraps=ledger.measurement)
    scan = Mock(side_effect=AssertionError("internal policy scanned HA sensors"))
    monkeypatch.setattr(ledger, "measurement", lookup)
    monkeypatch.setattr(type(p.hass.states), "async_all", scan)
    expected = p.pv_measurements(target, details=True)
    old = p.hass.states.get("sensor.selected")
    p.hass.states.async_set(
        "sensor.renamed_output", "unavailable", dict(old.attributes)
    )
    p.hass.states.async_set("sensor.duplicate_output", 999999, dict(old.attributes))
    p.hass.states.async_remove("sensor.selected")
    assert p.pv_measurements(target, details=True) == expected
    assert expected[2] == 3000
    assert lookup.call_count > 0
    scan.assert_not_called()


async def test_measurement_storm_waits_for_one_existing_regulation_cycle(
    grid, monkeypatch
):
    p, target, (control, bound, peer, *_), clock = configure(grid)
    waits = asyncio.Queue()
    release = asyncio.Event()

    async def wait(seconds):
        waits.put_nowait(seconds)
        await release.wait()
        release.clear()

    p.wait = wait
    del p.launch  # Use the real cycle rather than configure()'s manual-step stub.
    await p.permission(target, True)
    assert (
        await asyncio.wait_for(waits.get(), 2)
        == p.setting(target)["regulation_interval"]
    )
    task = p.tasks[target]
    edit = Mock(wraps=p.pv_edit)
    plan = Mock(wraps=p.pv_plan)
    monkeypatch.setattr(p, "pv_edit", edit)
    monkeypatch.setattr(p, "pv_plan", plan)
    before = list(peer.operations)
    for index in range(2000):
        p.hass.states.async_set("sensor.pv", 4000 + index, {"unit_of_measurement": "W"})
        now = datetime.now(UTC)
        control.runtime.observe(
            bound.token,
            (
                Observation(
                    Channel(target, Quantity.POWER),
                    0,
                    now,
                    now,
                    now + timedelta(seconds=90),
                    "meter",
                ),
            ),
        )
    await p.hass.async_block_till_done()
    edit.assert_not_called()
    plan.assert_not_called()
    assert peer.operations == before
    clock[0] += 5
    release.set()
    await asyncio.wait_for(waits.get(), 2)
    assert edit.call_count == 1
    assert p.tasks[target] is task


async def test_passive_permission_poll_remains_read_only_and_five_seconds(
    grid, monkeypatch
):
    (
        p,
        target,
        (control, bound, peer, *_),
    ) = grid
    setup_optimum(p, target)
    authority(control, target, ControlAuthority.LOCAL)
    policy = Mock(side_effect=AssertionError("permission poll ran PV policy"))
    monkeypatch.setattr(p, "pv_plan", policy)
    waits = []

    async def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            control.runtime.disconnect(bound.token)

    monkeypatch.setattr(enabled, "asyncio", SimpleNamespace(sleep=wait))
    before = len(peer.operations)
    await enabled.poll_enabled(bound.adapter, bound.token)
    assert waits == [5, 5]
    assert peer.operations[before:] == ["enabled_get", "enabled_get"]
    assert not peer.requests and not peer.permissions and not peer.authority_requests
    policy.assert_not_called()


def test_ledger_subscribers_read_the_matching_runtime_snapshot():
    runtime = Runtime()
    token = runtime.connect(StationId("coherent"))
    target = ConnectorId(EvseId(token.station, "1"), "1")
    now = datetime.now(UTC)
    runtime.session_event(
        token,
        SessionEvent(
            target, "transaction", SessionEventKind.STARTED, now - timedelta(seconds=1)
        ),
        live=True,
    )
    charging = Observation(
        Channel(target, Quantity.CHARGING_STATE),
        State.CHARGING,
        now,
        now,
        None,
        "station",
    )
    power = Observation(
        Channel(target, Quantity.POWER),
        2300,
        now,
        now,
        now + timedelta(seconds=90),
        "meter",
    )
    seen = []
    runtime.sessions.subscribe(
        lambda: seen.append(
            (
                runtime.get(token.station).observation(charging.channel),
                runtime.sessions.get(target).current_power_w,
            )
        )
    )
    runtime.observe(token, (charging, power))
    assert seen == [(charging, 2300)]
