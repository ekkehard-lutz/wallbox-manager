"""Persisted ownership is reconciled with a fresh runtime and live OCPP peer."""

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
    phase_event=True,
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
    if phase_event:
        await physical_report(peer, getattr(peer, "phase_read_value", "Rxx"))
    new_control = create_control_runtime(
        runtime,
        SimpleNamespace(sessions={bound.target.station: SimpleNamespace(adapter=live)}),
    )
    await new_control.adapter(bound.target).read_enabled()
    new_owner = owner if reload else ProfileOwnership(hass)
    await new_owner.load()
    remove = new_owner.register(entry, new_control)
    battery = BatteryReserve(hass, entry)
    await battery.load(
        preserve=bool(new_owner.record and new_owner.record["enabled_intent"])
    )
    new_profile = GridProfiles(hass, entry, new_control, battery)
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


@pytest.mark.parametrize("diagnostics", [False, True])
@pytest.mark.parametrize("power,phases,current", [(2.3, 1, 10), (5.52, 3, 8)])
async def test_grid_profile_recovery_does_not_replay_matching_point(
    site, power, phases, current, diagnostics, caplog
):
    owner, (a, _), hass = site
    c, bound, peer, p, key = a
    p.entry.options["pv_diagnostic_logging"] = diagnostics
    caplog.set_level(logging.INFO)
    await owner.activate(key)
    await p.set_value(bound.target, "power_kw", power)
    peer.phase_read_value = "RST" if phases == 3 else "Rxx"
    await physical_report(peer, peer.phase_read_value)
    p.wait = lambda _: asyncio.Event().wait()
    await p.permission(bound.target, True)
    schedule(peer, c.confirmed_point(bound.target))
    counts = len(peer.requests), len(peer.permissions), len(peer.authority_requests)
    owner, c, p, remove = await recreate(
        owner, c, bound, peer, p, hass, phase_event=False
    )
    try:
        assert owner.ready and p.setting(bound.target)["profile"] == "NETZ"
        assert c.confirmed_point(bound.target).current_a == current
        attrs = c.attributes(bound.target)
        assert attrs["applied_phase_count"] == phases
        assert attrs["applied_current_a"] == current
        assert attrs["electrical_recovery_status"] == "point_adopted"
        from custom_components.wallbox_manager.control_entity import ControlEntity

        entity = ControlEntity(c, c.entry_id, bound.target, "charging_profile")
        assert entity.extra_state_attributes["applied_phase_count"] == phases
        assert entity.extra_state_attributes["applied_current_a"] == current
        assert counts == (
            len(peer.requests),
            len(peer.permissions),
            len(peer.authority_requests),
        )
        lines = [
            json.loads(r.message.removeprefix("WBMGR subsystem=recovery "))
            for r in caplog.records
            if r.message.startswith("WBMGR subsystem=recovery ")
        ]
        assert bool(lines) is diagnostics
        assert not any(r.message.startswith("PVCTRL") for r in caplog.records)
        if diagnostics:
            assert any(
                r.get("profile") == "NETZ" and r["stage"] == "start" for r in lines
            )
            assert any(
                r.get("value") == peer.phase_read_value
                and r.get("status") == "Accepted"
                for r in lines
            )
            assert any(
                r["stage"] == "composite_schedule" and r.get("status") == "Accepted"
                for r in lines
            )
            adopted = next(r for r in lines if r.get("result") == "point_adopted")
            assert adopted["phases"] == phases and adopted["current_a"] == current
            assert adopted["voltages_v"] == [230] * phases
            assert adopted["charging_command_sent"] is False
        edit_ready = asyncio.Event()
        p.debounce_wait = lambda _: edit_ready.wait()
        await p.set_value(bound.target, "power_kw", power + 0.7)
        edit_task = p.debounce_tasks[bound.target]
        edit_ready.set()
        await edit_task
        assert len(peer.requests) == counts[0] + 1
        assert c.attributes(bound.target)["applied_current_a"] != current
        assert len(peer.permissions) == counts[1]
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


async def test_live_reserve_survives_owned_reload_and_restores_on_permission_off(site):
    from datetime import timedelta

    from custom_components.wallbox_manager.core.telemetry import (
        Channel,
        Observation,
        Quantity,
        State,
    )

    owner, c, bound, peer, p, hass = await prepared(site)
    target = bound.target
    options = {
        "min_soc_speicher": "number.reserve",
        "soc_speicher_aktuell": "sensor.soc",
    }
    p.entry.options.update(options)
    p.battery = BatteryReserve(hass, p.entry)
    p.setting(target)["min_soc"] = 40
    hass.states.async_set("number.reserve", "20", {"step": 1})
    hass.states.async_set("sensor.soc", "65", {"unit_of_measurement": "%"})
    writes = []

    async def write(call):
        writes.append(call.data["value"])
        hass.states.async_set("number.reserve", str(call.data["value"]), {"step": 1})

    hass.services.async_register("number", "set_value", write)

    def flow(control, token):
        now = datetime.now(UTC)
        control.runtime.observe(
            token,
            (
                Observation(
                    Channel(target, Quantity.CHARGING_STATE),
                    State.CHARGING,
                    now,
                    now,
                    None,
                    "test",
                ),
                Observation(
                    Channel(target, Quantity.POWER),
                    2300,
                    now,
                    now,
                    now + timedelta(seconds=60),
                    "test",
                ),
            ),
        )

    flow(c, bound.token)
    await p.reconcile_battery()
    assert writes == [40]
    owner, c, p, remove = await recreate(owner, c, bound, peer, p, hass)
    try:
        await p.reconcile_battery()
        assert writes == [40]  # Still awaiting live flow; no restore on load.
        flow(c, c.runtime.get(target.station).token)
        await p.reconcile_battery()
        assert writes == [40]
        assert p.battery.record["original"] == 20
        await p.permission(target, False)
        assert writes == [40, 20]
    finally:
        await p.close()
        c.close()
        remove()


async def reconnect(
    owner,
    control,
    bound,
    peer,
    profile,
    *,
    boot=True,
    local=False,
    unplugged=False,
    voltage=True,
):
    """Same live HA objects; fresh protocol generation after station outage."""
    from ocpp.v21 import call

    target = bound.target
    runtime = control.runtime
    old = runtime.get(target.station)
    session = runtime.sessions.get(target)
    transaction_id = session.external_transaction_id if session else "vehicle"
    runtime.disconnect(old.token)
    assert not owner.ready
    assert owner.record is not None
    assert not control.profile_permitted(target)
    token = runtime.connect(target.station, protocol="ocpp", protocol_version="2.1")
    if boot:
        token = runtime.boot(token, old.identity)
    live = bound.adapter
    live.token = token
    assert not owner.ready  # Fresh authority and permission are still absent.
    rows = inventory()
    for row in rows:
        if row["variable"]["name"] == "ControlAuthority":
            row["variable_attribute"][0]["value"] = "Local" if local else "OCPP"
        if row["variable"]["name"] == "ChargingEnabled":
            row["variable_attribute"][0]["value"] = str(peer.enabled).lower()
    runtime.discover(
        token,
        discovery=old.discovery,
        charging_schedule=old.charging_schedule,
        connectors=(target,),
        electrical=live.electrical_inventory(token, rows),
    )
    live.inventory_completed(token, rows, datetime.now(UTC))
    if live.enabled_poll_task:
        live.enabled_poll_task.cancel()
    if voltage:
        measured(runtime, token, target)
    await peer.call(
        call.StatusNotification(
            timestamp=datetime.now(UTC).isoformat(),
            evse_id=1,
            connector_id=1,
            connector_status="Available" if unplugged else "Occupied",
        )
    )
    await peer.call(
        call.TransactionEvent(
            event_type="Ended" if unplugged else "Updated",
            timestamp=(
                max(datetime.now(UTC), session.updated_at) + timedelta(microseconds=1)
            ).isoformat(),
            trigger_reason="EVDeparted" if unplugged else "ChargingStateChanged",
            seq_no=(session.sequence or 0) + 1,
            transaction_info={
                "transaction_id": transaction_id,
                "charging_state": "Idle" if unplugged else "Charging",
            },
            evse={"id": 1, "connector_id": 1},
        )
    )
    await control.adapter(target).read_enabled()
    return token


@pytest.mark.parametrize("boot", [False, True])
@pytest.mark.parametrize("phases,power", [(1, 2.3), (3, 5.52)])
async def test_station_reconnect_reuses_proof_and_adopts_without_commands(
    site, boot, phases, power
):
    owner, (a, _), _ = site
    c, bound, peer, p, key = a
    await owner.activate(key)
    await p.set_value(bound.target, "power_kw", power)
    peer.phase_read_value = "RST" if phases == 3 else "Rxx"
    await physical_report(peer, peer.phase_read_value)
    p.wait = lambda _: asyncio.Event().wait()
    await p.permission(bound.target, True)
    schedule(peer, c.confirmed_point(bound.target))
    counts = len(peer.requests), len(peer.permissions), len(peer.authority_requests)
    for _ in range(2):
        await reconnect(owner, c, bound, peer, p, boot=boot)
        await asyncio.wait_for(asyncio.shield(p.recoveries[bound.target]), 2)
        assert owner.ready and owner.record["enabled_intent"]
        assert p.setting(bound.target)["profile"] == "NETZ"
        attrs = c.attributes(bound.target)
        assert attrs["applied_phase_count"] == phases
        assert attrs["electrical_recovery_status"] == "point_adopted"
        assert counts == (
            len(peer.requests),
            len(peer.permissions),
            len(peer.authority_requests),
        )


async def test_station_reconnect_local_invalidates_proof_permanently(site):
    owner, c, bound, peer, p, _ = await prepared(site)
    token = await reconnect(owner, c, bound, peer, p, local=True)
    assert owner.record is None and not owner.ready
    assert owner.status == "ownership_rejected_local"
    c.runtime.observe_authority(
        token,
        AuthorityObservation(
            bound.target.station,
            ControlAuthority.REMOTE,
            datetime.now(UTC),
            "test",
        ),
    )
    assert not owner.ready and not c.profile_permitted(bound.target)


async def test_station_reconnect_unplugged_updates_physical_state(site):
    from custom_components.wallbox_manager.core.telemetry import (
        Channel,
        Quantity,
        State,
    )

    owner, c, bound, peer, p, _ = await prepared(site)
    parked = asyncio.Event()

    async def wait(_):
        parked.set()
        await asyncio.Event().wait()

    p.wait = wait
    counts = len(peer.requests), len(peer.permissions)
    await reconnect(owner, c, bound, peer, p, unplugged=True)
    await asyncio.wait_for(parked.wait(), 2)
    assert c.confirmed_point(bound.target) is None
    assert counts == (len(peer.requests), len(peer.permissions))
    assert owner.ready and owner.record["enabled_intent"]
    state = c.runtime.get(bound.target.station)
    for quantity, expected in (
        (Quantity.CONNECTOR_STATE, State.AVAILABLE),
        (Quantity.CHARGING_STATE, State.IDLE),
    ):
        observation = state.observation(Channel(bound.target, quantity))
        assert observation.value == expected
        assert c.runtime.physical_state_fresh(observation)
    assert not c.runtime.sessions.get(bound.target).active


async def test_station_reconnect_adopts_changed_point_then_reconciles_profile(site):
    from fractions import Fraction

    from test_ocpp21_control import point

    owner, (a, _), _ = site
    c, bound, peer, p, key = a
    await owner.activate(key)
    await p.set_value(bound.target, "power_kw", 2.3)
    p.wait = lambda _: asyncio.Event().wait()
    await p.permission(bound.target, True)
    schedule(peer, point(current=Fraction(6)))
    count = len(peer.requests), len(peer.permissions)
    await reconnect(owner, c, bound, peer, p)
    await asyncio.wait_for(asyncio.shield(p.recoveries[bound.target]), 2)
    assert c.recovery_status[bound.target] == "point_adopted"
    assert c.confirmed_point(bound.target).current_a == 10
    assert len(peer.requests) == count[0] + 1
    assert len(peer.permissions) == count[1]


async def test_station_reconnect_voltage_missing_waits_existing_retry(site):
    owner, c, bound, peer, p, _ = await prepared(site)
    waiting, retry = asyncio.Event(), asyncio.Event()

    async def wait(seconds):
        assert seconds == 60
        waiting.set()
        await retry.wait()

    p.wait = wait
    counts = len(peer.requests), len(peer.permissions)
    token = await reconnect(owner, c, bound, peer, p, voltage=False)
    await asyncio.wait_for(waiting.wait(), 2)
    assert c.confirmed_point(bound.target) is None
    assert c.recovery_status[bound.target] == "voltage_unavailable"
    measured(c.runtime, token, bound.target)
    retry.set()
    # Park the regulation task launched after adoption independently of retry.
    p.wait = lambda _: asyncio.Event().wait()
    await asyncio.wait_for(asyncio.shield(p.recoveries[bound.target]), 2)
    assert c.recovery_status[bound.target] == "point_adopted"
    assert counts == (len(peer.requests), len(peer.permissions))


@pytest.mark.parametrize("diagnostics", [False, True])
async def test_reconnect_diagnostics_describe_transitions_without_idle_noise(
    site, diagnostics, caplog
):
    owner, c, bound, peer, p, _ = await prepared(site)
    p.entry.options["pv_diagnostic_logging"] = diagnostics
    caplog.set_level(logging.INFO)
    await reconnect(owner, c, bound, peer, p)
    await asyncio.wait_for(asyncio.shield(p.recoveries[bound.target]), 2)

    def records():
        return [
            json.loads(r.message.removeprefix("WBMGR subsystem=reconnect "))
            for r in caplog.records
            if r.message.startswith("WBMGR subsystem=reconnect ")
        ]

    lines = records()
    assert bool(lines) is diagnostics
    if diagnostics:
        assert any(not r["connected"] and r["prior_ownership_retained"] for r in lines)
        assert any(r["connected"] and r["authority"] == "unknown" for r in lines)
        assert any(
            r["authority"] == "remote"
            and r["ownership"] == "restored_ownership"
            and r["enabled_intent"]
            and r["enabled_actual"]
            for r in lines
        )
        assert any(any(o["fresh"] for o in r["physical_states"]) for r in lines)
    owner.changed()
    measured(c.runtime, c.runtime.get(bound.target.station).token, bound.target)
    assert records() == lines
    token = c.runtime.get(bound.target.station).token
    c.runtime.observe_authority(
        token,
        AuthorityObservation(
            bound.target.station,
            ControlAuthority.LOCAL,
            datetime.now(UTC),
            "test",
        ),
    )
    if diagnostics:
        assert records()[-1]["ownership"] == "ownership_rejected_local"
        assert records()[-1]["prior_ownership_retained"] is False


async def test_remote_reconnect_without_prior_takeover_never_grants_ownership(site):
    owner, (a, _), _ = site
    c, bound, _, p, _ = a
    runtime = c.runtime
    runtime.disconnect(bound.token)
    token = runtime.connect(bound.target.station)
    runtime.observe_authority(
        token,
        AuthorityObservation(
            bound.target.station,
            ControlAuthority.REMOTE,
            datetime.now(UTC),
            "test",
        ),
    )
    assert owner.record is None and not owner.ready
    assert not c.profile_permitted(bound.target)
    assert not p.recoveries


async def test_reconnect_reconciles_retained_on_intent_when_station_reset_to_off(site):
    owner, c, bound, peer, p, _ = await prepared(site)
    peer.enabled = False
    counts = len(peer.permissions), len(peer.authority_requests)
    await reconnect(owner, c, bound, peer, p)
    await asyncio.wait_for(asyncio.shield(p.recoveries[bound.target]), 2)
    assert owner.ready and owner.record["enabled_intent"]
    assert c.runtime.enabled(bound.target) is True
    assert p.setting(bound.target)["profile"] == "PV_SURPLUS"
    assert len(peer.permissions) == counts[0] + 1
    assert len(peer.authority_requests) == counts[1]


async def test_successful_ocpp_off_updates_canonical_sensor_without_cp_event(site):
    from ocpp.v21 import call

    from custom_components.wallbox_manager.core.telemetry import Channel, Quantity
    from custom_components.wallbox_manager.sensor import StateObservationSensor

    owner, c, bound, peer, p, _ = await prepared(site)
    await peer.call(
        call.StatusNotification(
            timestamp=datetime.now(UTC).isoformat(),
            evse_id=1,
            connector_id=1,
            connector_status="Occupied",
        )
    )
    await peer.call(
        call.TransactionEvent(
            event_type="Updated",
            timestamp=datetime.now(UTC).isoformat(),
            trigger_reason="ChargingStateChanged",
            seq_no=2,
            transaction_info={"transaction_id": "active", "charging_state": "Charging"},
            evse={"id": 1, "connector_id": 1},
        )
    )
    sensors = [
        StateObservationSensor(c.runtime, c.entry_id, Channel(bound.target, q))
        for q in (Quantity.CONNECTOR_STATE, Quantity.CHARGING_STATE)
    ]
    assert [e.native_value for e in sensors] == ["occupied", "charging"]
    remove = c.runtime.subscribe(
        lambda snapshot: [setattr(e, "snapshot", snapshot) for e in sensors]
    )
    try:
        result = await p.permission(bound.target, False)
        assert result.status.value == "applied"
        assert [e.native_value for e in sensors] == ["occupied", "connected"]
        assert not sensors[0].state_fresh and sensors[1].state_fresh
    finally:
        remove()
