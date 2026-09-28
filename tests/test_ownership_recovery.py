"""Persisted ownership is reconciled with a fresh runtime and live OCPP peer."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from ocpp.v21 import call_result
from test_control_authority import inventory
from test_control_runtime import measured, physical_report
from test_ocpp21_control import transaction
from test_profile_ownership import site as site
from test_pv_surplus import measurements

from custom_components.wallbox_manager.battery import BatteryReserve
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.ownership import ProfileOwnership
from custom_components.wallbox_manager.profiles import GridProfiles
from custom_components.wallbox_manager.protocols.ocpp.v21.control_runtime import (
    create_control_runtime,
)
from custom_components.wallbox_manager.runtime import Runtime


def schedule(peer, point):
    def response(evse, duration):
        return call_result.GetCompositeSchedule(
            status="Accepted",
            schedule={
                "evse_id": evse,
                "duration": duration,
                "schedule_start": datetime.now(UTC).isoformat(),
                "charging_rate_unit": "A",
                "charging_schedule_period": [
                    {
                        "start_period": 0,
                        "limit": float(point.current_a or 0),
                        "number_phases": point.mode.count if point.mode else 1,
                    }
                ],
            },
        )

    peer.composite_response = response


async def recreate(
    owner,
    control,
    bound,
    peer,
    profile,
    hass,
    *,
    local=False,
    mismatch=False,
    reload=False,
    wait_recovery=True,
):
    old = control.runtime.get(bound.target.station)
    session = control.runtime.sessions.get(bound.target)
    transaction_id = session.external_transaction_id
    entry = owner.entries[control.entry_id][0]
    await profile.close()  # Same persistence/suspension entry point as HA unload.
    control.close()
    runtime = Runtime()
    token = runtime.connect(
        bound.target.station, protocol="ocpp", protocol_version="2.1"
    )
    token = runtime.boot(
        token, replace(old.identity, serial="different") if mismatch else old.identity
    )
    live = bound.adapter
    live.runtime, live.token = runtime, token
    # Fresh discovery and actual readback from the unchanged simulated station.
    rows = inventory()
    for row in rows:
        name = row["variable"]["name"]
        if name == "ControlAuthority":
            row["variable_attribute"][0]["value"] = "Local" if local else "OCPP"
        if name == "ChargingEnabled":
            row["variable_attribute"][0]["value"] = str(peer.enabled).lower()
    runtime.discover(
        token,
        discovery=old.discovery,
        charging_schedule=old.charging_schedule,
        connectors=(bound.target,),
        electrical=live.electrical_inventory(token, rows),
    )
    live.inventory_completed(token, rows, datetime.now(UTC))
    if live.enabled_poll_task:
        live.enabled_poll_task.cancel()
    measured(runtime, token, bound.target)
    await transaction(peer, connector=1, identity=transaction_id)
    await physical_report(peer, "Rxx")
    new_control = create_control_runtime(
        runtime,
        SimpleNamespace(sessions={bound.target.station: SimpleNamespace(adapter=live)}),
    )
    await new_control.adapter(bound.target).read_enabled()
    new_owner = owner if reload else ProfileOwnership(hass)
    await new_owner.load()
    remove = new_owner.register(entry, new_control)
    new_profile = GridProfiles(hass, entry, new_control, BatteryReserve(hass, entry))
    await new_profile.load()
    new_control.profiles = new_profile
    parked = asyncio.Event()

    async def wait(_):
        parked.set()
        await asyncio.Event().wait()

    new_profile.wait = wait
    new_profile.recovery_parked = parked
    measurements(new_profile, bound.target, pv=2300, load=0, actual=0, soc=96)
    new_owner.changed()
    task = new_profile.recoveries.get(bound.target)
    if task and wait_recovery:
        # Supported readback completes recovery; waiting cases are asserted separately.
        await asyncio.wait_for(asyncio.shield(task), 2)
    return new_owner, new_control, new_profile, remove


async def prepared(site, *, enabled=True):
    owner, (a, _), hass = site
    c, bound, peer, p, key = a
    p.references.update(leistung_pv="sensor.pv", leistung_verbraucher="sensor.load")
    p.entry.options.update(p.references)
    await owner.activate(key)
    await p.select(bound.target, "PV_SURPLUS")
    measurements(p, bound.target, pv=2300, load=0, actual=0, soc=96)
    p.entry.options.update(p.references)
    p.wait = lambda _: asyncio.Event().wait()
    if enabled:
        await p.permission(bound.target, True)
        await asyncio.sleep(0)
        schedule(peer, c.confirmed_point(bound.target))
    return owner, c, bound, peer, p, hass


@pytest.mark.parametrize("reload", [False, True])
async def test_repeated_restart_restores_owner_on_profile_without_off_on(site, reload):
    owner, c, bound, peer, p, hass = await prepared(site)
    permissions = len(peer.permissions)
    commands = len(peer.requests)
    takeovers = len(peer.authority_requests)
    removers = []
    try:
        for _ in range(2):
            owner, c, p, remove = await recreate(
                owner, c, bound, peer, p, hass, reload=reload
            )
            removers.append(remove)
            assert owner.ready and owner.status == "restored_ownership", (
                owner.status,
                owner.record,
                c.runtime.get(bound.target.station),
            )
            assert c.profile_permitted(bound.target)
            assert p.setting(bound.target)["profile"] == "PV_SURPLUS"
            assert c.runtime.enabled(bound.target) is True
            assert c.confirmed_point(bound.target).current_a == 10
            assert p.pv_ongoing[bound.target]
            assert len(peer.permissions) == permissions
            assert len(peer.requests) == commands
            assert len(peer.authority_requests) == takeovers
    finally:
        await p.close()
        c.close()
        for remove in removers:
            remove()


async def test_restored_off_intent_never_enables(site):
    owner, c, bound, peer, p, hass = await prepared(site, enabled=False)
    count = len(peer.permissions)
    owner, c, p, remove = await recreate(owner, c, bound, peer, p, hass)
    try:
        assert owner.ready and not c.runtime.enabled(bound.target), (
            owner.status,
            owner.record,
            c.runtime.get(bound.target.station),
        )
        assert not owner.record["enabled_intent"]
        assert len(peer.permissions) == count and not peer.requests
    finally:
        await p.close()
        c.close()
        remove()


@pytest.mark.parametrize("case", ["local", "identity", "legacy", "corrupt", "missing"])
async def test_recovery_rejects_invalid_history_or_live_context(site, case):
    owner, c, bound, peer, p, hass = await prepared(site)
    await p.close()
    if case in ("legacy", "corrupt", "missing"):
        data = owner.saved()
        data["ownership"] = None if case in ("legacy", "missing") else {"version": 999}
        if case == "missing":
            data = {}
        await owner.store.async_save(data)
    count = len(peer.operations)
    restored, new, profile, remove = await recreate(
        owner,
        c,
        bound,
        peer,
        p,
        hass,
        local=case == "local",
        mismatch=case == "identity",
    )
    try:
        assert not restored.ready and not new.profile_permitted(bound.target)
        assert not profile.tasks and not profile.recoveries
        assert not any(
            op in ("permission", "authority_set", "profile")
            for op in peer.operations[count:]
        )
        if case == "local":
            new.runtime.observe_authority(
                new.runtime.get(bound.target.station).token,
                AuthorityObservation(
                    bound.target.station,
                    ControlAuthority.REMOTE,
                    datetime.now(UTC),
                    "test",
                ),
            )
            assert not restored.ready
    finally:
        await profile.close()
        new.close()
        remove()


async def test_unavailable_schedule_never_fabricates_confirmation_or_interrupts_charge(
    site,
):
    owner, c, bound, peer, p, hass = await prepared(site)
    peer.composite_response = None
    count = len(peer.requests), len(peer.permissions), len(peer.authority_requests)
    owner, c, p, remove = await recreate(
        owner, c, bound, peer, p, hass, wait_recovery=False
    )
    try:
        await asyncio.wait_for(p.recovery_parked.wait(), 2)
        assert owner.ready and c.runtime.enabled(bound.target)
        assert c.confirmed_point(bound.target) is None
        assert p.status[bound.target] == "recovery_waiting_electrical"
        assert count == (
            len(peer.requests),
            len(peer.permissions),
            len(peer.authority_requests),
        )
    finally:
        await p.close()
        c.close()
        remove()


async def test_online_local_revocation_is_persisted_and_remote_return_cannot_restore(
    site,
):
    owner, c, bound, peer, p, hass = await prepared(site)
    c.runtime.observe_authority(
        bound.token,
        AuthorityObservation(
            bound.target.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
        ),
    )
    assert not owner.ready and owner.record is None
    c.runtime.observe_authority(
        bound.token,
        AuthorityObservation(
            bound.target.station, ControlAuthority.REMOTE, datetime.now(UTC), "test"
        ),
    )
    assert not owner.ready
    await p.close()
    restored = ProfileOwnership(hass)
    await restored.load()
    assert restored.record is None


@pytest.mark.parametrize("change", ["generation", "authority", "voltage", "limit"])
async def test_schedule_readback_is_fenced_and_voltage_drift_is_semantic(site, change):
    from fractions import Fraction

    from test_voltage_command_fence import voltage

    from custom_components.wallbox_manager.core.capabilities import CurrentLimit
    from custom_components.wallbox_manager.core.models import PhaseMode

    owner, c, bound, peer, p, hass = await prepared(site)
    point = c.confirmed_point(bound.target)
    c._confirmed_points.clear()
    original = peer.composite_response

    def response(evse, duration):
        if change == "generation":
            c._edit(bound.target, {"target_w": 0})
        elif change == "authority":
            c.runtime.observe_authority(
                bound.token,
                AuthorityObservation(
                    bound.target.station,
                    ControlAuthority.LOCAL,
                    datetime.now(UTC),
                    "test",
                ),
            )
        elif change == "limit":
            c.capability_source.limits = lambda target: (
                CurrentLimit(PhaseMode.canonical(1), 0, 6, "test"),
            )
        else:
            voltage(c, bound.target, Fraction("229.6"))
        return original(evse, duration)

    peer.composite_response = response
    # The actual phase report, unlike historical selection, is required for adoption.
    await physical_report(peer, "Rxx")
    result = await c.reconcile_applied(bound.target, fence=lambda: True)
    assert (result is not None) is (change == "voltage")
    if result:
        assert point.same_setpoint(result)
        assert result.phase_voltages_v == (Fraction("229.6"),)


async def test_grid_profile_recovery_does_not_replay_matching_point(site):
    owner, (a, _), hass = site
    c, bound, peer, p, key = a
    await owner.activate(key)
    await p.set_value(bound.target, "power_kw", 2.3)
    p.wait = lambda _: asyncio.Event().wait()
    await p.permission(bound.target, True)
    schedule(peer, c.confirmed_point(bound.target))
    counts = len(peer.requests), len(peer.permissions), len(peer.authority_requests)
    owner, c, p, remove = await recreate(owner, c, bound, peer, p, hass)
    try:
        assert owner.ready and p.setting(bound.target)["profile"] == "NETZ"
        assert c.confirmed_point(bound.target).current_a == 10
        assert counts == (
            len(peer.requests),
            len(peer.permissions),
            len(peer.authority_requests),
        )
    finally:
        await p.close()
        c.close()
        remove()


async def test_persisted_off_reconciles_conflicting_hardware_on(site):
    owner, c, bound, peer, p, hass = await prepared(site, enabled=False)
    peer.enabled = True
    count = len(peer.permissions)
    owner, c, p, remove = await recreate(owner, c, bound, peer, p, hass)
    try:
        assert owner.ready and not c.runtime.enabled(bound.target)
        assert len(peer.permissions) == count + 1
        assert not peer.requests
    finally:
        await p.close()
        c.close()
        remove()


@pytest.mark.parametrize(
    "case", ["evse", "expired", "future", "variable", "watts", "phase", "missing_phase"]
)
async def test_ambiguous_or_invalid_schedule_cannot_be_adopted(site, case):
    from datetime import timedelta

    owner, c, bound, peer, p, hass = await prepared(site)
    c._confirmed_points.clear()
    original = peer.composite_response
    await physical_report(peer, "Rxx")

    def response(evse, duration):
        result = original(evse, duration)
        data = result.schedule
        if case == "evse":
            data["evse_id"] = 2
        elif case in ("expired", "future"):
            data["schedule_start"] = (
                datetime.now(UTC)
                + timedelta(seconds=-120 if case == "expired" else 120)
            ).isoformat()
        elif case == "variable":
            data["charging_schedule_period"].append(
                {"start_period": 1, "limit": 6, "number_phases": 1}
            )
        elif case == "watts":
            data["charging_rate_unit"] = "W"
        elif case == "phase":
            data["charging_schedule_period"][0]["number_phases"] = 3
        else:
            state = c.runtime.get(bound.target.station)
            c.runtime._publish(replace(state, physical_phases=()))
        return result

    peer.composite_response = response
    counts = len(peer.requests), len(peer.permissions)
    assert await c.reconcile_applied(bound.target, fence=lambda: True) is None
    assert c.confirmed_point(bound.target) is None
    assert counts == (len(peer.requests), len(peer.permissions))


async def test_local_observed_during_teardown_still_revokes_history(site):
    owner, c, bound, peer, p, hass = await prepared(site)
    await p.close()
    assert owner.record is not None and not owner.ready
    c.runtime.observe_authority(
        bound.token,
        AuthorityObservation(
            bound.target.station, ControlAuthority.LOCAL, datetime.now(UTC), "test"
        ),
    )
    assert owner.record is None and owner.status == "ownership_rejected_local"
