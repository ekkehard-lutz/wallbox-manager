"""Fresh physical proof and deadlines survive selective HA publication."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from homeassistant.helpers import device_registry as dr
from test_control_runtime import manual as manual
from test_ha_control import controls as controls
from test_ocpp21_control import connected as connected
from test_passive_mixed_publication import mixed as mixed

from custom_components.wallbox_manager.core.enabled import EnabledObservation
from custom_components.wallbox_manager.core.models import (
    PhaseMode,
    PhysicalPhaseObservation,
)
from custom_components.wallbox_manager.runtime import Runtime
from custom_components.wallbox_manager.select import ActiveWallbox
from custom_components.wallbox_manager.station_config import ensure_subentry


async def test_repeated_phase_and_permission_preserve_proof_without_config_writes(
    controls, monkeypatch
):
    hass, entities, (control, bound, peer, *_), _, _ = controls
    runtime, target = control.runtime, bound.target
    now = datetime.now(UTC)
    phase = PhysicalPhaseObservation(
        target, PhaseMode.canonical(1), now, now + timedelta(seconds=30), "physical"
    )
    runtime.observe_physical_phase(bound.token, phase)
    runtime.observe_enabled(
        bound.token, EnabledObservation(target, True, now, now + timedelta(seconds=15))
    )
    writes = {key: Mock(wraps=e.async_write_ha_state) for key, e in entities.items()}
    for key, entity in entities.items():
        monkeypatch.setattr(entity, "async_write_ha_state", writes[key])
    before = list(peer.operations)
    for _ in range(50):
        now = datetime.now(UTC)
        phase = PhysicalPhaseObservation(
            target, PhaseMode.canonical(1), now, now + timedelta(seconds=30), "physical"
        )
        runtime.observe_physical_phase(bound.token, phase)
        enabled = EnabledObservation(target, True, now, now + timedelta(seconds=15))
        runtime.observe_enabled(bound.token, enabled)
    assert runtime.get(target.station).physical_phases == (phase,)
    assert runtime.enabled_observation(target).observed_at == enabled.observed_at
    assert all(
        w.call_count == 0 for key, w in writes.items() if key != "charging_enabled"
    )
    # The hardware entity keeps actual acquisition metadata for both streams.
    attrs = hass.states.get(entities["charging_enabled"].entity_id).attributes
    assert attrs["enabled_observed_at"] == enabled.observed_at.isoformat()
    assert attrs["physical_phase_valid_until"] == phase.valid_until.isoformat()
    assert peer.operations == before
    now = datetime.now(UTC)
    runtime.observe_physical_phase(
        bound.token,
        PhysicalPhaseObservation(
            target, PhaseMode.canonical(3), now, now + timedelta(seconds=30), "physical"
        ),
    )
    assert all(w.call_count > 0 for w in writes.values())


async def test_shortened_renewed_permission_deadline_still_expires(controls):
    hass, entities, (control, bound, *_), _, _ = controls
    target, runtime = bound.target, control.runtime
    now = datetime.now(UTC)
    runtime.observe_enabled(
        bound.token, EnabledObservation(target, True, now, now + timedelta(seconds=15))
    )
    now = datetime.now(UTC)
    deadline = now + timedelta(milliseconds=50)
    runtime.observe_enabled(
        bound.token, EnabledObservation(target, True, now, deadline)
    )
    assert all(e._expiry_deadline == deadline for e in entities.values())
    await asyncio.sleep(0.07)
    await hass.async_block_till_done()
    assert runtime.enabled(target) is None
    assert hass.states.get(entities["charging_enabled"].entity_id).state == "unknown"
    assert all(
        hass.states.get(e.entity_id).attributes["actual_enabled"] is None
        for e in entities.values()
    )


async def test_same_phase_readback_is_immediately_available_to_command_guard(
    controls, monkeypatch
):
    _, entities, (control, bound, peer, *_), _, _ = controls
    runtime, target = control.runtime, bound.target
    now = datetime.now(UTC)
    runtime.observe_physical_phase(
        bound.token,
        PhysicalPhaseObservation(
            target, PhaseMode.canonical(1), now, now + timedelta(seconds=5), "physical"
        ),
    )
    writes = Mock(wraps=entities["desired_charging_power"].async_write_ha_state)
    monkeypatch.setattr(
        entities["desired_charging_power"], "async_write_ha_state", writes
    )
    before = runtime.get(target.station).physical_phases[0]
    operations = list(peer.operations)
    await bound.read_physical_mode(is_current=lambda: runtime.current(bound.token))
    after = runtime.get(target.station).physical_phases[0]
    assert after.observed_at > before.observed_at
    assert after.mode == before.mode
    assert after.source == "ocpp2.1:GetVariables:Connector.PhaseRotation"
    assert peer.operations == operations + ["phase_get"]
    writes.assert_not_called()


async def test_inventory_updates_only_for_relevant_registry_and_config_changes(
    mixed, monkeypatch
):
    m = mixed
    entity = next(e for e in m.entities if isinstance(e, ActiveWallbox))
    writes = Mock(wraps=entity.async_write_ha_state)
    inventory = Mock(wraps=m.owner.inventory)
    monkeypatch.setattr(entity, "async_write_ha_state", writes)
    monkeypatch.setattr(m.owner, "inventory", inventory)
    m.owner.changed()
    inventory.assert_not_called()
    registry = dr.async_get(m.hass)
    foreign = registry.async_get_or_create(
        config_entry_id=m.config.entry_id,
        identifiers={("other", "unrelated")},
        name="Unrelated",
    )
    await m.hass.async_block_till_done()
    registry.async_remove_device(foreign.id)
    await m.hass.async_block_till_done()
    inventory.assert_not_called()
    device = registry.async_get_device(
        identifiers={
            ("wallbox_manager", f"{m.config.entry_id}:{m.target.station.value}")
        }
    )
    registry.async_update_device(device.id, model="Metadata not in ownership inventory")
    await m.hass.async_block_till_done()
    inventory.assert_not_called()
    registry.async_update_device(device.id, name_by_user="Garage")
    await m.hass.async_block_till_done()
    assert writes.call_count == 1
    attrs = m.hass.states.get(entity.entity_id).attributes
    assert attrs["wallboxes"][m.owner.active_wallbox]["display_name"] == "Garage"
    before = writes.call_count
    ensure_subentry(m.hass, m.config, "offline", "2", "1")
    await m.hass.async_block_till_done()
    attrs = m.hass.states.get(entity.entity_id).attributes
    assert len(attrs["wallboxes"]) == 2
    assert writes.call_count > before
    # Explicit unchanged ownership notifications do not rewrite a static select.
    before = writes.call_count
    m.owner.publish(controls=False)
    assert writes.call_count == before


def test_shared_snapshot_selector_is_evaluated_once_per_publication():
    from custom_components.wallbox_manager.core.models import StationId

    runtime = Runtime()
    token = runtime.connect(StationId("shared"))

    class Selector:
        __hash__ = None
        calls = 0

        def __call__(self, snapshot):
            self.calls += 1
            return snapshot.connected

    selector = Selector()
    listeners = [Mock() for _ in range(20)]
    for listener in listeners:
        runtime.subscribe(listener, select=selector)
    runtime.disconnect(token)
    assert selector.calls == 2
    assert all(listener.call_count == 1 for listener in listeners)
