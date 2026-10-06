"""Cumulative levels share semantic event identity, never charging behavior."""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_optimum import setup_optimum
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid
from test_pv_surplus import measurements

from custom_components.wallbox_manager.diagnostics import (
    diagnostic_event,
    diagnostic_level,
    migrate_diagnostics,
    reset_diagnostics,
)


def events(caplog, subsystem=None):
    return [
        json.loads(r.message.split(" ", 2)[2])
        for r in caplog.records
        if r.message.startswith("WBMGR subsystem=")
        and (subsystem is None or r.message.startswith(f"WBMGR subsystem={subsystem} "))
    ]


@pytest.mark.parametrize(
    "options,level",
    [
        ({}, 0),
        ({"pv_diagnostic_logging": False}, 0),
        ({"pv_diagnostic_logging": True}, 3),
        ({"pv_diagnostic_logging": True, "diagnostic_level": 1}, 1),
        ({"pv_diagnostic_logging": False, "diagnostic_level": "2"}, 2),
    ],
)
def test_legacy_migration_and_explicit_precedence(options, level):
    normalized = migrate_diagnostics(options)
    assert normalized == {"diagnostic_level": level}
    assert migrate_diagnostics(normalized) == normalized
    assert diagnostic_level(SimpleNamespace(options=normalized)) == level


@pytest.mark.parametrize("level", [0, 1, 2, 3])
def test_event_identity_excludes_explanatory_data_and_voltage(level, caplog):
    caplog.set_level(logging.INFO)
    entry = SimpleNamespace(options={"diagnostic_level": level})
    for n in range(5):
        diagnostic_event(
            entry,
            "pv",
            station="a",
            event="decision",
            reason="hold",
            desired={"phases": 1, "current_a": 8, "power_w": 1840 + n},
            soc=84 + n / 100,
            raw_watts=n,
            retry_seconds=60 - n,
        )
    lines = events(caplog)
    assert len(lines) == {0: 0, 1: 1, 2: 1, 3: 5}[level]
    if level:
        assert ("soc" in lines[0]) is (level >= 2)
    for reason, current in (("hold", 9), ("pause", 0)):
        diagnostic_event(
            entry,
            "pv",
            station="a",
            event="decision",
            reason=reason,
            desired={"phases": 1, "current_a": current},
        )
    assert len(events(caplog)) == ({0: 0, 1: 3, 2: 3, 3: 7}[level])
    reset_diagnostics(entry)


@pytest.mark.parametrize("level", [1, 2])
async def test_cycles_deduplicate_raw_measurements_and_emit_mode_change(
    grid, caplog, level
):
    p, t, _ = grid
    setup_optimum(p, t, soc=84, pv=0, load=500, actual=0)
    p.monotonic = lambda: 0
    p.optimum_policy(t)
    p.entry.options = {"diagnostic_level": level}
    caplog.set_level(logging.INFO)
    p.pv_plan(t)
    for n in range(4):
        measurements(p, t, soc=84, pv=n, load=500 + n, actual=0)
        p.pv_plan(t)
    assert len(events(caplog, "pv")) == 1
    measurements(p, t, soc=80, pv=0, load=500, actual=0)
    p.pv_plan(t)
    assert len(events(caplog, "pv")) == 2
    assert any(e.get("event") == "soc_mode" for e in events(caplog))


@pytest.mark.parametrize("level", [1, 2])
async def test_enable_lifecycle_and_real_commands_are_observable(grid, caplog, level):
    p, t, (c, *_rest) = grid
    setup_optimum(p, t, pv=0, soc=84, load=500, actual=0)
    p.entry.options = {"diagnostic_level": level}
    p.wait = lambda _: asyncio.Event().wait()
    caplog.set_level(logging.INFO)
    blocker = c.blocker
    c.blocker = lambda _: "transaction_unavailable"
    await p.permission(t, True)
    assert any(e.get("event") == "enable_pending" for e in events(caplog))
    pending = next(e for e in events(caplog) if e.get("event") == "enable_pending")
    assert pending["reason"] == "transaction_unavailable"
    assert ("epoch" in pending) is (level == 2)
    await p.permission(t, False)
    assert any(e.get("event") == "enable_cancelled" for e in events(caplog))
    c.blocker = blocker
    await p.permission(t, True)
    assert any(e.get("event") == "enable_confirmed" for e in events(caplog))
    count = sum(e.get("event") == "command_attempt" for e in events(caplog))
    await p.permission(t, True)
    assert sum(e.get("event") == "command_attempt" for e in events(caplog)) == count


def test_live_level_change_resets_dedup_and_baseline(caplog):
    caplog.set_level(logging.INFO)
    entry = SimpleNamespace(options={"diagnostic_level": 1})
    for level in (1, 1, 0, 2, 2, 3, 1):
        entry.options = {"diagnostic_level": level}
        diagnostic_event(entry, "pv", event="decision", reason="hold", watts=123)
    assert len(events(caplog)) == 4
    reset_diagnostics(entry)


async def test_normal_errors_survive_level_zero(grid, caplog):
    from custom_components.wallbox_manager.control.commands import (
        CommandReason,
        CommandResult,
        CommandStatus,
    )
    from custom_components.wallbox_manager.diagnostics import command_event

    p, t, (c, *_rest) = grid
    p.entry.options = {"diagnostic_level": 0}
    caplog.set_level(logging.INFO)
    command_event(
        c,
        t,
        "command_outcome",
        result=CommandResult(CommandStatus.FAILED, reason=CommandReason.TIMEOUT),
    )
    assert not events(caplog)
    assert any(
        r.levelno == logging.ERROR and "timeout" in r.message for r in caplog.records
    )


async def test_serialization_failure_does_not_change_control(grid, monkeypatch):
    p, t, (c, *_rest) = grid
    setup_optimum(p, t, pv=0, soc=84, load=500, actual=0)
    p.entry.options = {"diagnostic_level": 2}
    p.wait = lambda _: asyncio.Event().wait()

    def fail(*args, **kwargs):
        raise ValueError("diagnostic failure")

    monkeypatch.setattr(
        "custom_components.wallbox_manager.diagnostics.event_record", fail
    )
    await p.permission(t, True)
    assert c.runtime.enabled(t) is True and c.confirmed_point(t).charging
