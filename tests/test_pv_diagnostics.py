"""Opt-in records use the real PV/controller path without changing commands."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_control_runtime import manual as manual
from test_grid_profiles import grid as base_grid  # noqa: F401
from test_ocpp21_control import connected as connected
from test_pv_surplus import grid as grid  # noqa: F401
from test_pv_surplus import measurements

from custom_components.wallbox_manager.pv_diagnostics import entity_sample


def records(caplog):
    return [
        json.loads(r.message.removeprefix("WBMGR subsystem=pv "))
        for r in caplog.records
        if r.message.startswith("WBMGR subsystem=pv ")
    ]


@pytest.mark.parametrize("enabled", [False, True])
async def test_one_record_per_cycle_and_identical_command_behavior(
    grid, caplog, enabled
):
    p, t, (c, _, peer, *_) = grid
    p.entry.options = {"pv_diagnostic_logging": enabled}
    caplog.set_level(logging.INFO)
    measurements(p, t, soc=96)
    await p.select(t, "PV_SURPLUS")
    seen = []

    async def wait(_):
        seen.append(len(peer.requests))
        if len(seen) == 3:
            await asyncio.Event().wait()

    p.wait = wait
    await p.permission(t, True)
    await asyncio.sleep(0)
    assert len(seen) == 3 and len(set(seen)) == 1
    assert c.confirmed_point(t).charging
    lines = records(caplog)
    assert len(lines) == (4 if enabled else 0)  # permission + 3 regulation cycles
    if enabled:
        (permission,) = [r for r in lines if r["trigger"] == "permission"]
        assert permission["decision"] == "START"
        assert permission["ongoing_before"] is False
        assert permission["ongoing_after"] is True
        assert all(
            r["decision"] == "HOLD" for r in lines if r["trigger"] == "regulation"
        )
        assert all(r["reason"] == "actively_charging" for r in lines)
        assert lines[0]["external"]["leistung_pv"]["entity_id"] == "sensor.pv"
        assert lines[0]["external"]["leistung_pv"]["value"] == 8000
        assert lines[0]["external"]["leistung_pv"]["age_s"] >= 0
        assert lines[0]["external"]["leistung_pv"]["age_basis"] == "last_reported"
        assert lines[0]["raw_pv_power_w"] == 8000
        assert lines[0]["smoothed_pv_power_w"] == 8000
        assert lines[0]["raw_consumption_power_w"] == 5000
        assert lines[0]["smoothed_consumption_power_w"] == 5000
        assert lines[0]["smoothing_window_s"] == 0
        assert lines[0]["smoothing_enabled"] is False
        assert lines[0]["surplus_w"] == 6000
        assert lines[0]["site_load_w"] == 2000
        assert all(
            "\n" not in r.message
            for r in caplog.records
            if r.message.startswith("WBMGR subsystem=pv ")
        )


@pytest.mark.parametrize("value", ["unavailable", "unknown", "not a number", "nan"])
async def test_invalid_values_are_safe_and_do_not_change_policy(grid, caplog, value):
    p, t, _ = grid
    measurements(p, t, pv=value)
    p.setting(t)["profile"] = "PV_SURPLUS"
    baseline = p.pv_plan(t)
    p.entry.options = {"pv_diagnostic_logging": True}
    caplog.set_level(logging.INFO)
    assert p.pv_plan(t) == baseline
    (line,) = records(caplog)
    assert line["external"]["leistung_pv"]["value"] is None
    assert line["external"]["leistung_pv"]["state"] == value
    assert line["policy_reason"] == "measurements_unavailable"


def test_age_uses_report_not_last_change_and_exposes_stale():
    now = datetime.now(UTC)
    state = SimpleNamespace(
        state="42",
        attributes={},
        last_updated=now - timedelta(hours=1),
        last_reported=now - timedelta(seconds=2),
    )
    assert entity_sample(state, "sensor.pv", now)["age_s"] == 2
    state.last_reported = now - timedelta(seconds=91)
    assert entity_sample(state, "sensor.pv", now)["freshness"] == "stale"
    assert entity_sample(None, "sensor.missing", now)["freshness"] == "missing"


async def test_options_toggle_persists_without_authority_reload_or_writes(grid):
    from homeassistant.config_entries import ConfigEntries
    from test_ha_lifecycle import entry

    from custom_components.wallbox_manager.config_flow import ReferenceOptionsFlow
    from custom_components.wallbox_manager.core.authority import (
        AuthorityObservation,
        ControlAuthority,
    )

    p, t, (c, bound, peer, *_) = grid
    c.runtime.observe_authority(
        bound.token,
        AuthorityObservation(
            t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
        ),
    )
    config = entry()
    p.hass.config_entries = ConfigEntries(p.hass, {})
    p.hass.config_entries._entries[config.entry_id] = config
    p.entry = config
    flow = ReferenceOptionsFlow()
    flow.hass, flow.handler = p.hass, config.entry_id
    count = len(peer.requests)
    result = await flow.async_step_init({"diagnostic_level": "3"})
    assert not flow.automatic_reload
    # Exercise HA's actual options persistence boundary.
    await p.hass.config_entries.options.async_finish_flow(flow, result)
    assert config.options["diagnostic_level"] == 3
    assert len(peer.requests) == count
    assert not p.tasks
    again = ReferenceOptionsFlow()
    again.hass, again.handler = p.hass, config.entry_id
    form = await again.async_step_init()
    assert (
        form["data_schema"]({"general": {}, "regulation": {}})["general"][
            "diagnostic_level"
        ]
        == "3"
    )


async def test_diagnostic_collector_failure_does_not_change_plan(
    grid, caplog, monkeypatch
):
    from custom_components.wallbox_manager.pv_diagnostics import Cycle

    p, t, _ = grid
    measurements(p, t)
    baseline = p.pv_plan(t)
    p.entry.options = {"pv_diagnostic_logging": True}
    caplog.set_level(logging.INFO)

    def broken(*args):
        raise RuntimeError("diagnostic failure")

    monkeypatch.setattr(Cycle, "_capture_inputs", broken)
    assert p.pv_plan(t) == baseline
    (line,) = records(caplog)
    assert line["diagnostic_error"] == "snapshot_failed"


async def test_cancelled_cycle_emits_once(grid, caplog):
    from custom_components.wallbox_manager.pv_diagnostics import cycle

    p, t, _ = grid
    p.entry.options = {"pv_diagnostic_logging": True}
    caplog.set_level(logging.INFO)
    with pytest.raises(asyncio.CancelledError), cycle(p, t, "regulation"):
        raise asyncio.CancelledError
    (line,) = records(caplog)
    assert line["decision"] == "CANCELLED"


async def test_phase_lockout_reports_fallback_and_retry(grid, caplog):
    from test_phase_lockout import setup

    p, t, manual = grid
    c, _, _, _, _, _ = await setup(manual)
    p.entry.options = {"pv_diagnostic_logging": True}
    p.setting(t).update(profile="PV_SURPLUS", approximation="up")
    measurements(p, t, pv=1700, load=0, actual=0)
    p.monotonic = lambda: 100
    reached = asyncio.Event()

    async def wait(_):
        reached.set()
        await asyncio.Event().wait()

    p.wait = wait
    caplog.set_level(logging.INFO)
    p.launch(t)
    await asyncio.wait_for(reached.wait(), 1)
    (line,) = [r for r in records(caplog) if r["trigger"] == "regulation"]
    assert line["decision"] == "WAIT_PHASE_LOCKOUT"
    assert line["reason"] == "phase_switch_lockout"
    assert line["retry_remaining_s"] == 60
    assert line["selected"]["phases"] == 1
    assert line["applied"]["phases"] == 3
    assert c.intent(t).phase_retry


@pytest.mark.parametrize("delay", [0, 20])
async def test_waiting_start_is_explicit(grid, caplog, delay):
    p, t, _ = grid
    p.entry.options = {"pv_diagnostic_logging": True}
    p.setting(t).update(profile="PV_SURPLUS", pv_start_delay=delay)
    measurements(p, t, soc=96 if delay else 95)
    caplog.set_level(logging.INFO)
    p.pv_plan(t)
    (line,) = records(caplog)
    assert line["decision"] == ("START_PENDING" if delay else "PLANNED")
    assert line["reason"] == ("pv_start_delay" if delay else "waiting_battery_soc")
    if delay:
        assert 0 < line["delays"]["pv_start_delay"]["remaining_s"] <= 20


async def test_command_failure_is_not_reported_as_start(grid, caplog, monkeypatch):
    from custom_components.wallbox_manager.control.commands import (
        CommandReason,
        CommandResult,
        CommandStatus,
    )

    p, t, (c, _, _, *_) = grid
    p.entry.options = {"pv_diagnostic_logging": True}
    p.setting(t)["profile"] = "PV_SURPLUS"
    measurements(p, t)

    async def failed(*args, **kwargs):
        result = CommandResult(CommandStatus.FAILED, reason=CommandReason.TIMEOUT)
        c.intent(t).command_result = result
        c.intent(t).status = "failed"
        return result

    monkeypatch.setattr(c, "request_enabled", failed)
    caplog.set_level(logging.INFO)
    await p.permission(t, True)
    (line,) = records(caplog)
    assert line["decision"] == "COMMAND_FAILED"
    assert line["reason"] == "timeout"
    assert line["applied"] is None


async def test_soc_safety_event_has_explicit_reason(grid, caplog):
    from homeassistant.core import Event

    p, t, _ = grid
    p.entry.options = {"pv_diagnostic_logging": True}
    p.setting(t)["profile"] = "PV_SURPLUS"
    p.references["soc_speicher_aktuell"] = "sensor.soc"
    p.hass.states.async_set("sensor.soc", "80", {"unit_of_measurement": "%"})
    caplog.set_level(logging.INFO)
    p.pv_soc_changed(
        Event(
            "state_changed",
            {"entity_id": "sensor.soc", "new_state": p.hass.states.get("sensor.soc")},
        )
    )
    (line,) = records(caplog)
    assert line["reason"] == "stopped_battery_soc"
    assert line["soc_allows_charging"] is False
    assert line["soc_event"]["value"] == 80


async def test_no_authority_is_explicit(grid, caplog):
    from custom_components.wallbox_manager.core.authority import (
        AuthorityObservation,
        ControlAuthority,
    )

    p, t, (c, bound, _, *_) = grid
    p.entry.options = {"pv_diagnostic_logging": True}
    c.runtime.observe_authority(
        bound.token,
        AuthorityObservation(
            t.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
        ),
    )
    caplog.set_level(logging.INFO)
    p.pv_plan(t)
    (line,) = records(caplog)
    assert line["decision"] == "NO_AUTHORITY"
    assert line["reason"] == "no_authority"


async def test_diagnostics_default_disabled(grid):
    from homeassistant.config_entries import ConfigEntries
    from test_ha_lifecycle import entry

    from custom_components.wallbox_manager.config_flow import ReferenceOptionsFlow

    p, _, _ = grid
    config = entry()
    p.hass.config_entries = ConfigEntries(p.hass, {})
    p.hass.config_entries._entries[config.entry_id] = config
    flow = ReferenceOptionsFlow()
    flow.hass, flow.handler = p.hass, config.entry_id
    form = await flow.async_step_init()
    assert (
        form["data_schema"]({"general": {}, "regulation": {}})["general"][
            "diagnostic_level"
        ]
        == "0"
    )


def test_diagnostic_translations_are_generic():
    from pathlib import Path

    root = Path(__file__).parents[1] / "custom_components/wallbox_manager"
    for filename, label in [
        ("strings.json", "Diagnostic level"),
        ("translations/en.json", "Diagnostic level"),
        ("translations/de.json", "Diagnosestufe"),
    ]:
        step = json.loads((root / filename).read_text())["options"]["step"]["init"][
            "sections"
        ]["general"]
        assert step["data"]["diagnostic_level"] == label
        help_text = step["data_description"]["diagnostic_level"]
        assert "PV" not in help_text
        assert "Wallbox" in help_text
        assert "deaktiviert" in help_text or "Disabled by default" in help_text
