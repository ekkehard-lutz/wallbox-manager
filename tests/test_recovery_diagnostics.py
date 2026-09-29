"""Detailed recovery evidence remains read-only and opt-in."""

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from ocpp.v21 import call, call_result
from test_ownership_recovery import prepared
from test_profile_ownership import site as site


def records(caplog):
    return [
        json.loads(r.message.removeprefix("WBMGR subsystem=recovery "))
        for r in caplog.records
        if r.message.startswith("WBMGR subsystem=recovery ")
    ]


@pytest.mark.parametrize(
    "case,reason",
    [
        ("Rejected", "phase_readback_rejected"),
        ("UnknownVariable", "phase_readback_rejected"),
        ("invalid_phase", "phase_value_invalid"),
        ("phase_timeout", "phase_readback_timeout"),
        ("schedule_rejected", "composite_schedule_rejected"),
        ("schedule_timeout", "composite_schedule_timeout"),
        ("schedule_invalid", "composite_schedule_unavailable_or_invalid"),
        ("mismatch", "schedule_phase_mismatch"),
        ("missing_count", "schedule_phase_missing"),
        ("evse", "schedule_evse_mismatch"),
        ("unit", "schedule_unit_unsupported"),
        ("periods", "schedule_periods_ambiguous"),
        ("missing_phase", "phase_evidence_unavailable"),
        ("expired_phase", "phase_evidence_unavailable"),
        ("missing_voltage", "voltage_missing"),
        ("stale_voltage", "voltage_stale"),
        ("generation", "generation_changed"),
        ("fence", "command_fence_stale"),
    ],
)
async def test_recovery_rejection_is_observable_without_commands(
    site, caplog, monkeypatch, case, reason
):
    _, c, bound, peer, p, _ = await prepared(site)
    p.entry.options["pv_diagnostic_logging"] = True
    caplog.set_level(logging.INFO)
    c._confirmed_points.clear()
    # Supply normalized evidence before the attempt, so fences compare it unchanged.
    original_inputs = c.inputs
    if case in ("missing_voltage", "stale_voltage"):

        def inputs(target):
            value = original_inputs(target)
            old = datetime.now(UTC) - timedelta(minutes=5)
            phases = (
                ()
                if case == "missing_voltage"
                else tuple(
                    replace(v, observed_at=old, valid_until=old + timedelta(seconds=1))
                    for v in value.voltage.phases
                )
            )
            return replace(value, voltage=replace(value.voltage, phases=phases))

        monkeypatch.setattr(c, "inputs", inputs)
    if case in (
        "Rejected",
        "UnknownVariable",
        "phase_timeout",
        "missing_phase",
        "expired_phase",
    ):
        state = c.runtime.get(bound.target.station)
        phases = ()
        if case == "expired_phase":
            old = datetime.now(UTC) - timedelta(seconds=10)
            phases = tuple(
                replace(o, observed_at=old, valid_until=old + timedelta(seconds=5))
                for o in state.physical_phases
            )
        c.runtime._publish(replace(state, physical_phases=phases))
    original_call = bound.adapter.call

    async def response(request, **kwargs):
        if (
            isinstance(request, call.GetVariables)
            and request.get_variable_data[0]["variable"]["name"] == "PhaseRotation"
        ):
            if case == "phase_timeout":
                raise TimeoutError
            data = request.get_variable_data[0]
            return call_result.GetVariables(
                get_variable_result=[
                    {
                        "component": data["component"],
                        "variable": data["variable"],
                        "attribute_status": case
                        if case in ("Rejected", "UnknownVariable")
                        else "Rejected"
                        if case in ("missing_phase", "expired_phase")
                        else "Accepted",
                        "attribute_value": "invalid-secret-must-not-appear"
                        if case == "invalid_phase"
                        else "Rxx",
                    }
                ]
            )
        if isinstance(request, call.GetCompositeSchedule):
            if case == "generation":
                c._edit(bound.target, {"target_w": 0})
            if case == "schedule_timeout":
                raise TimeoutError
            if case == "schedule_rejected":
                return call_result.GetCompositeSchedule(status="Rejected")
            result = peer.composite_response(int(bound.target.evse.value), 60)
            schedule = result.schedule
            if case == "schedule_invalid":
                del schedule["schedule_start"]
            elif case == "mismatch":
                schedule["charging_schedule_period"][0]["number_phases"] = 3
            elif case == "missing_count":
                del schedule["charging_schedule_period"][0]["number_phases"]
            elif case == "evse":
                schedule["evse_id"] = 99
            elif case == "unit":
                schedule["charging_rate_unit"] = "W"
            elif case == "periods":
                schedule["charging_schedule_period"] *= 2
            return result
        return await original_call(request, **kwargs)

    monkeypatch.setattr(bound.adapter, "call", response)
    counts = len(peer.requests), len(peer.permissions), len(peer.authority_requests)
    settings = dict(p.setting(bound.target))
    result = await c.reconcile_applied(bound.target, fence=lambda: case != "fence")
    assert result is None and c.confirmed_point(bound.target) is None
    lines = records(caplog)
    assert any(r.get("reason") == reason for r in lines), lines
    assert not any(r.get("result") == "point_adopted" for r in lines)
    assert "invalid-secret-must-not-appear" not in str(lines)
    if case == "expired_phase":
        assert any(
            o["state"] == "expired" for r in lines for o in r.get("observations", [])
        )
    if case == "stale_voltage":
        assert any(not v["fresh"] for r in lines for v in r.get("voltages", []))
    assert settings == p.setting(bound.target)
    assert counts == (
        len(peer.requests),
        len(peer.permissions),
        len(peer.authority_requests),
    )


async def test_identical_waiting_states_are_logged_once(site, caplog):
    owner, c, bound, peer, p, _ = await prepared(site)
    p.entry.options["pv_diagnostic_logging"] = True
    caplog.set_level(logging.INFO)
    c._confirmed_points.clear()
    c.inputs = lambda _: None
    calls = 0

    async def wait(_):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise asyncio.CancelledError

    p.wait = wait
    with pytest.raises(asyncio.CancelledError):
        await p.recover(bound.target, owner.record)
    waits = [r for r in records(caplog) if r["stage"] == "waiting"]
    assert len(waits) == 1
    assert waits[0]["pending"] is True
    assert waits[0]["reason"] == "recovery_waiting_runtime"


async def test_diagnostics_do_not_create_missing_profile(site, caplog):
    from custom_components.wallbox_manager.profiles import target_key

    owner, c, bound, peer, p, _ = await prepared(site)
    p.entry.options["pv_diagnostic_logging"] = True
    caplog.set_level(logging.INFO)
    p.settings.pop(target_key(bound.target))
    before = len(peer.requests), len(peer.permissions), len(peer.authority_requests)
    await p.recover(bound.target, owner.record)
    assert target_key(bound.target) not in p.settings
    assert any(r.get("reason") == "recovery_missing_profile" for r in records(caplog))
    assert before == (
        len(peer.requests),
        len(peer.permissions),
        len(peer.authority_requests),
    )


async def test_failed_diagnostic_logging_cannot_prevent_adoption(site, monkeypatch):
    from custom_components.wallbox_manager import diagnostics

    _, c, bound, peer, p, _ = await prepared(site)
    p.entry.options["pv_diagnostic_logging"] = True
    point = c.confirmed_point(bound.target)
    c._confirmed_points.clear()
    before = len(peer.requests), len(peer.permissions), len(peer.authority_requests)

    def fail(*args, **kwargs):
        raise ValueError("diagnostic-only failure")

    monkeypatch.setattr(diagnostics._LOGGER, "info", fail)
    result = await c.reconcile_applied(bound.target, fence=lambda: True)
    assert point.same_setpoint(result)
    assert before == (
        len(peer.requests),
        len(peer.permissions),
        len(peer.authority_requests),
    )


async def test_switch_is_checked_live_during_recovery(site, caplog):
    from custom_components.wallbox_manager.diagnostics import (
        diagnostic_recovery,
        recovery_record,
        recovery_snapshot,
    )

    _, peers, _ = site
    _, bound, _, p, _ = peers[0]
    caplog.set_level(logging.INFO)

    @diagnostic_recovery
    async def attempt(profile, target):
        recovery_snapshot("disabled", lambda: pytest.fail("must stay lazy"))
        profile.entry.options["pv_diagnostic_logging"] = True
        recovery_record("enabled")
        profile.entry.options["pv_diagnostic_logging"] = False
        recovery_record("disabled")

    await attempt(p, bound.target)
    assert [r["stage"] for r in records(caplog)] == ["enabled"]
