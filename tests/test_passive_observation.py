"""Phase 1 removes passive work without caching or advancing control decisions."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from homeassistant.core import callback
from homeassistant.core_config import DATA_CUSTOMIZE
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)
from test_control_runtime import manual as manual
from test_ha_control import controls as controls
from test_ha_diagnostics import diagnostics as diagnostics
from test_ocpp21_control import connected as connected
from test_profile_ownership import site as site
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.core.models import StationId
from custom_components.wallbox_manager.core.telemetry import (
    Channel,
    Observation,
    Quantity,
)
from custom_components.wallbox_manager.runtime import Runtime


async def test_only_configured_inputs_enter_profile_callback(grid, monkeypatch):
    p, target, _ = grid
    p.references.update(
        leistung_pv="sensor.input_pv", leistung_verbraucher="sensor.input_load"
    )
    p.battery.reserve, p.battery.soc = "number.reserve", "sensor.battery_soc"
    p.battery.record = {"entity": "number.previous_reserve"}
    observed = Mock(wraps=p.battery_changed)

    @callback
    def changed(event):
        observed(event)

    monkeypatch.setattr(p, "battery_changed", changed)
    reconcile = Mock()
    monkeypatch.setattr(p, "sessions_changed", reconcile)
    for name in ("pv_plan", "optimum_refresh", "common_soc_policy"):
        monkeypatch.setattr(
            p, name, Mock(side_effect=AssertionError("event ran policy"))
        )
    p.subscribe_inputs()
    p.hass.states.async_set("sensor.unrelated", 123)
    await p.hass.async_block_till_done()
    observed.assert_not_called()
    for entity, value, attrs in (
        ("sensor.input_pv", 2000, {"unit_of_measurement": "W"}),
        ("sensor.input_load", 500, {"unit_of_measurement": "W"}),
        ("number.reserve", 20, {}),
        ("sensor.battery_soc", 80, {"unit_of_measurement": "%"}),
        ("number.previous_reserve", 10, {}),
    ):
        p.hass.states.async_set(entity, value, attrs)
    await p.hass.async_block_till_done()
    assert observed.call_count == 5
    assert reconcile.call_count == 3
    assert p.power_history["leistung_pv"].samples[-1][1] == 2000
    assert p.power_history["leistung_verbraucher"].samples[-1][1] == 500
    assert target not in p.tasks
    p.battery.record = None


async def test_replaced_and_closed_input_subscriptions_are_removed(grid, monkeypatch):
    p, _, _ = grid
    calls = []

    @callback
    def changed(event):
        calls.append(event.data["entity_id"])

    monkeypatch.setattr(p, "battery_changed", changed)
    p.references["leistung_pv"] = "sensor.old_mapping"
    p.subscribe_inputs()
    p.references["leistung_pv"] = "sensor.new_mapping"
    p.subscribe_inputs()
    p.hass.states.async_set("sensor.old_mapping", 1)
    p.hass.states.async_set("sensor.new_mapping", 2)
    await p.hass.async_block_till_done()
    assert calls == ["sensor.new_mapping"]
    await p.close()
    p.hass.states.async_set("sensor.new_mapping", 3)
    await p.hass.async_block_till_done()
    assert calls == ["sensor.new_mapping"]


def test_runtime_keeps_new_provenance_and_rejects_duplicate_observations():
    runtime = Runtime()
    token = runtime.connect(StationId("observed"))
    published = Mock()
    runtime.subscribe(published)
    now = datetime.now(UTC)
    sample = Observation(
        Channel(token.station, Quantity.POWER),
        1000,
        now,
        now,
        now + timedelta(seconds=120),
        "meter",
    )
    runtime.observe(token, (sample,))
    published.reset_mock()
    runtime.observe(token, (sample,))
    published.assert_not_called()
    newer = replace(
        sample,
        observed_at=now + timedelta(seconds=1),
        received_at=now + timedelta(seconds=1),
    )
    runtime.observe(token, (newer,))
    published.assert_called_once()
    assert runtime.get(token.station).observation(sample.channel) == newer
    runtime.disconnect(token)
    assert published.call_args.args[0].connected is False


async def test_repeated_control_publications_report_without_state_changes(controls):
    hass, entities, (control, bound, *_), _, _ = controls
    ids = [entity.entity_id for entity in entities.values()]
    before = {
        entity_id: (
            state.last_changed,
            state.last_updated,
            state.last_reported,
        )
        for entity_id in ids
        if (state := hass.states.get(entity_id)) is not None
    }
    changes, reports = [], []

    @callback
    def changed(event):
        changes.append(event)

    @callback
    def reported(event):
        reports.append(event)

    unsubscribe_changes = async_track_state_change_event(hass, ids, changed)
    unsubscribe_reports = async_track_state_report_event(hass, ids, reported)
    for _ in range(3):
        control.publish(bound.target)
    await hass.async_block_till_done()
    assert not changes
    assert len(reports) == 3 * len(ids)
    for entity_id in ids:
        state = hass.states.get(entity_id)
        assert (state.last_changed, state.last_updated) == before[entity_id][:2]
        assert state.last_reported > before[entity_id][2]
    control.restore(bound.target, target_w=4600)
    await hass.async_block_till_done()
    power_id = entities["desired_charging_power"].entity_id
    assert hass.states.get(power_id).state == "4600.0"
    assert [event.data["entity_id"] for event in changes] == [power_id]
    changes.clear()
    control.publish(bound.target)
    await hass.async_block_till_done()
    assert not changes
    unsubscribe_changes()
    unsubscribe_reports()


@pytest.mark.parametrize("force_update", [False, True])
async def test_control_publications_preserve_customization_and_force_update(
    controls, force_update
):
    hass, entities, (control, bound, *_), _, _ = controls
    entity = entities["desired_charging_power"]
    entity._attr_force_update = force_update
    hass.data.setdefault(DATA_CUSTOMIZE, {})[entity.entity_id] = {
        "friendly_name": "Customized power",
        "review_attribute": "retained",
    }
    control.publish(bound.target)
    await hass.async_block_till_done()
    before = hass.states.get(entity.entity_id).last_reported
    changes = []

    @callback
    def changed(event):
        changes.append(event)

    unsubscribe = async_track_state_change_event(hass, entity.entity_id, changed)
    control.publish(bound.target)
    await hass.async_block_till_done()
    state = hass.states.get(entity.entity_id)
    assert state.attributes["friendly_name"] == "Customized power"
    assert state.attributes["review_attribute"] == "retained"
    assert state.last_reported > before
    assert bool(changes) is force_update
    unsubscribe()


@pytest.mark.parametrize("event", ["authority", "disconnect"])
async def test_live_invalidation_is_immediately_presented(controls, event):
    hass, entities, (control, bound, *_), _, _ = controls
    entity = entities["desired_charging_power"]
    if event == "authority":
        control.runtime.observe_authority(
            bound.token,
            AuthorityObservation(
                bound.target.station, ControlAuthority.LOCAL, datetime.now(UTC), "local"
            ),
        )
        reason = "no_authority"
    else:
        control.runtime.disconnect(bound.token)
        reason = "disconnected"
    attrs = hass.states.get(entity.entity_id).attributes
    assert not attrs["execution_ready"]
    assert attrs["execution_blocked_reason"] == reason


async def test_presentation_never_resolves_and_preserves_pending_confirmed_point(
    controls, monkeypatch
):
    _, entities, (control, bound, peer, *_), _, _ = controls
    control.restore(bound.target, allowed_current_1p=16)
    await control.change(bound.target, target_w=3680, allowed=True)
    confirmed = control.confirmed_point(bound.target)
    # A pending desired target does not become the confirmed point on rendering.
    control.pending_points[bound.target] = replace(
        confirmed, current_a=10, offered_power_w=2300
    )
    monkeypatch.setattr(
        control, "resolve", Mock(side_effect=AssertionError("presentation solved"))
    )
    before = list(peer.operations)
    for entity in entities.values():
        attrs = entity.extra_state_attributes
        assert attrs["applied_current_a"] == 16
        assert attrs["applied_phase_count"] == 1
        entity.async_write_ha_state()
    assert control.confirmed_point(bound.target) == confirmed
    assert peer.operations == before
    control.pending_points.pop(bound.target)


async def test_unchanged_ownership_does_not_rebroadcast_control(site, monkeypatch):
    owner, peers, hass = site
    control, bound, _, _, _ = peers[0]
    await owner.activate(peers[0][-1])
    published = Mock(wraps=control.publish)
    monkeypatch.setattr(control, "publish", published)
    owner.changed()
    published.assert_not_called()
    hass.bus.async_fire(
        "device_registry_updated", {"action": "update", "device_id": "other"}
    )
    await hass.async_block_till_done()
    published.assert_not_called()
    control.runtime.observe_authority(
        bound.token,
        AuthorityObservation(
            bound.target.station, ControlAuthority.LOCAL, datetime.now(UTC), "revoked"
        ),
    )
    assert not owner.ready
    assert published.call_count > 0


async def test_real_options_reload_replaces_input_subscription(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from homeassistant.config_entries import ConfigEntries
    from homeassistant.core import HomeAssistant
    from test_ha_lifecycle import entry

    from custom_components.wallbox_manager import async_setup_entry, async_unload_entry
    from custom_components.wallbox_manager.profiles import GridProfiles

    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    config = entry()
    hass.config_entries._entries[config.entry_id] = config
    calls = []
    original = GridProfiles.battery_changed

    @callback
    def changed(self, event):
        calls.append((self, event.data["entity_id"]))
        original(self, event)

    monkeypatch.setattr(GridProfiles, "battery_changed", changed)
    loaded = False
    try:
        for entity in ("sensor.before_reload", "sensor.after_reload"):
            hass.config_entries.async_update_entry(
                config, options={"leistung_pv": entity}
            )
            assert await async_setup_entry(hass, config)
            loaded = True
            current = config.runtime_data.profiles
            hass.states.async_set(entity, 1000, {"unit_of_measurement": "W"})
            await hass.async_block_till_done()
            assert calls[-1] == (current, entity)
            assert await async_unload_entry(hass, config)
            loaded = False
            count = len(calls)
            hass.states.async_set("sensor.before_reload", 2000)
            hass.states.async_set("sensor.after_reload", 2000)
            await hass.async_block_till_done()
            assert len(calls) == count
    finally:
        if loaded:
            await async_unload_entry(hass, config)
        await hass.async_stop()


async def test_station_projections_keep_metadata_without_unrelated_state_changes(
    diagnostics,
):
    from test_ha_observations import operational

    hass, _, runtime, platforms, _, _ = diagnostics
    token = runtime.connect(StationId("provenance"))
    now = datetime.now(UTC)
    sample = Observation(
        Channel(token.station, Quantity.POWER),
        1000,
        now,
        now,
        now + timedelta(seconds=120),
        "meter",
    )
    runtime.observe(token, (sample,))
    await hass.async_block_till_done()
    (meter,) = operational(platforms)
    entities = [
        entity for platform in platforms for entity in platform.entities.values()
    ]
    changes = []

    @callback
    def changed(event):
        changes.append(event)

    unsubscribe = async_track_state_change_event(
        hass, [entity.entity_id for entity in entities], changed
    )
    runtime.observe(token, (sample,))
    await hass.async_block_till_done()
    assert not changes
    # A new acquisition of the same number is meaningful provenance, not a no-op.
    newer = replace(
        sample,
        observed_at=now + timedelta(seconds=1),
        received_at=now + timedelta(seconds=1),
    )
    runtime.observe(token, (newer,))
    await hass.async_block_till_done()
    assert [event.data["entity_id"] for event in changes] == [meter.entity_id]
    assert (
        hass.states.get(meter.entity_id).attributes["observed_at"]
        == newer.observed_at.isoformat()
    )
    runtime.disconnect(token)
    assert not meter.available
    assert hass.states.get(meter.entity_id).state == "unavailable"
    unsubscribe()


async def test_unchanged_battery_reconciliation_does_not_publish(grid, monkeypatch):
    from unittest.mock import AsyncMock

    p, _, (control, *_) = grid
    monkeypatch.setattr(p.battery, "update", AsyncMock())
    published = Mock()
    monkeypatch.setattr(control, "publish", published)
    await p.reconcile_battery()
    published.assert_not_called()

    async def changed(_):
        p.battery.status = "restored"

    monkeypatch.setattr(p.battery, "update", changed)
    await p.reconcile_battery()
    assert published.call_count > 0


async def test_physical_invalidation_is_published_even_with_unchanged_snapshot(
    diagnostics,
):
    from test_ha_observations import operational

    from custom_components.wallbox_manager.core.models import ConnectorId, EvseId
    from custom_components.wallbox_manager.core.telemetry import State
    from custom_components.wallbox_manager.protocols.ocpp.common.metering import (
        state_observation,
    )

    hass, _, runtime, platforms, _, _ = diagnostics
    token = runtime.connect(StationId("paused"))
    target = ConnectorId(EvseId(token.station, "1"), "1")
    runtime.observe(
        token,
        state_observation(
            target,
            Quantity.CONNECTOR_STATE,
            State.OCCUPIED,
            datetime.now(UTC).isoformat(),
            "station",
        ),
    )
    await hass.async_block_till_done()
    meter = next(
        e
        for e in operational(platforms)
        if e.channel.quantity == Quantity.CONNECTOR_STATE
    )
    assert hass.states.get(meter.entity_id).attributes["state_fresh"] is True
    snapshot = runtime.get(token.station)
    runtime.observe_zero_point(token, target)
    assert runtime.get(token.station) == snapshot
    assert hass.states.get(meter.entity_id).attributes["state_fresh"] is False
    assert hass.states.get(meter.entity_id).state == "occupied"
