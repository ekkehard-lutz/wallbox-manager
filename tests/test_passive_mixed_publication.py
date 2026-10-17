"""Mixed 120-second HA/OCPP work counter, also runnable unchanged on beta.17.

WBM_MEASURE_ONLY=1 pytest -q -s tests/test_passive_mixed_publication.py
prints the baseline without applying the new performance assertions.
"""

import cProfile
import json
import logging
import os
import pstats
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)
from test_control_runtime import REFERENCE
from test_control_runtime import manual as manual
from test_ha_lifecycle import entry
from test_ocpp21_control import connected as connected

from custom_components.wallbox_manager import number, select, sensor, switch
from custom_components.wallbox_manager.battery import BatteryReserve
from custom_components.wallbox_manager.control.reference import ConfiguredReference
from custom_components.wallbox_manager.control_entity import ControlEntity
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.core.enabled import EnabledObservation
from custom_components.wallbox_manager.core.models import (
    PhaseMode,
    PhysicalPhaseObservation,
)
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
from custom_components.wallbox_manager.entity import async_setup_station_entities
from custom_components.wallbox_manager.ownership import ProfileOwnership, identity
from custom_components.wallbox_manager.profiles import GridProfiles
from custom_components.wallbox_manager.protocols.ocpp.common.metering import (
    METER_MAX_AGE,
)
from custom_components.wallbox_manager.protocols.ocpp.v21.control_runtime import (
    create_control_runtime,
)
from custom_components.wallbox_manager.protocols.ocpp.v21.phase_feedback import LIFETIME
from custom_components.wallbox_manager.session_entity import (
    SessionEntity,
    setup_session_entities,
)


@pytest.fixture
async def mixed(tmp_path, manual, monkeypatch):
    previous, bound, peer, _, _, server = manual
    previous.close()
    runtime, target = previous.runtime, bound.target
    runtime._publish(replace(runtime.get(target.station), protocol_version="2.1"))
    clock = [0]
    base = datetime.now(UTC) + timedelta(days=1)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return base + timedelta(seconds=clock[0])

    for module in (
        "runtime",
        "core.events",
        "control.runtime",
        "control.capabilities",
        "control_entity",
        "session_entity",
        "profiles",
        "pv_optimum",
        "pv_surplus",
        "pv_economy_control",
    ):
        monkeypatch.setattr(
            f"custom_components.wallbox_manager.{module}.datetime", Clock
        )
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    config = entry()
    hass.config_entries._entries[config.entry_id] = config
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
    control = create_control_runtime(runtime, server, ConfiguredReference(REFERENCE))
    owner = ProfileOwnership(hass)
    await owner.load()
    remove_owner = owner.register(config, control)
    owner.active_wallbox = identity(config.entry_id, target)
    options = {
        "min_soc_speicher": "number.reserve",
        "soc_speicher_aktuell": "sensor.soc",
        "leistung_pv": "sensor.pv",
        "leistung_verbraucher": "sensor.load",
    }
    for entity_id, value in (
        ("number.reserve", 10),
        ("sensor.soc", 80),
        ("sensor.pv", 4000),
        ("sensor.load", 2500),
    ):
        hass.states.async_set(entity_id, value)
    profile_entry = SimpleNamespace(entry_id=config.entry_id, options=options)
    profiles = GridProfiles(
        hass, profile_entry, control, BatteryReserve(hass, profile_entry)
    )
    await profiles.load()
    control.profiles = profiles
    config.runtime_data = SimpleNamespace(state=runtime, control=control)
    now = Clock.now(UTC)
    runtime.observe_authority(
        bound.token,
        AuthorityObservation(target.station, ControlAuthority.LOCAL, now, "physical"),
    )
    runtime.observe_enabled(
        bound.token, EnabledObservation(target, True, now, now + timedelta(seconds=15))
    )
    runtime.observe_physical_phase(
        bound.token,
        PhysicalPhaseObservation(
            target, PhaseMode.canonical(1), now, now + LIFETIME, "physical"
        ),
    )
    runtime.session_event(
        bound.token,
        SessionEvent(target, "mixed", SessionEventKind.STARTED, now, State.CHARGING),
        live=True,
    )

    def meter(voltage=230, power=1500):
        now = Clock.now(UTC)
        samples = [(Quantity(f"voltage_l{i}"), voltage) for i in (1, 2, 3)]
        samples += [
            (Quantity.POWER, power),
            (Quantity.CURRENT_L1, 6 if power else 0),
            (Quantity.ENERGY, 1000 + clock[0]),
        ]
        return tuple(
            Observation(
                Channel(target, q),
                value,
                now,
                now,
                now + METER_MAX_AGE,
                "meter",
            )
            for q, value in samples
        )

    runtime.observe(bound.token, meter())
    platforms = []
    for domain, module in (
        ("switch", switch),
        ("number", number),
        ("select", select),
        ("sensor", sensor),
    ):
        platform = EntityPlatform(
            hass=hass,
            logger=logging.getLogger(__name__),
            domain=domain,
            platform_name="wallbox_manager",
            platform=module,
            scan_interval=timedelta(seconds=30),
            entity_namespace=None,
        )
        platform.config_entry = config
        platforms.append(platform)

        def add(entities, platform=platform):
            entities = list(entities)
            for entity in entities:
                entity._attr_entity_registry_enabled_default = True
            hass.async_create_task(platform.async_add_entities(entities))

        if domain == "sensor":
            async_setup_station_entities(
                hass,
                config,
                add,
                lambda *args: [],
                observation_factory=lambda r, e, c: [sensor.ObservationSensor(r, e, c)],
            )
            setup_session_entities(hass, config, add)
        else:
            await module.async_setup_entry(hass, config, add)
    await hass.async_block_till_done()
    entities = [e for p in platforms for e in p.entities.values()]
    assert sum(isinstance(e, ControlEntity) for e in entities) >= 20
    assert any(isinstance(e, SessionEntity) for e in entities)
    # Drive duration at simulated seconds, not the machine's elapsed seconds.
    for entity in entities:
        if isinstance(entity, SessionEntity) and entity.key == "duration":
            entity._cancel_refresh()
    try:
        yield SimpleNamespace(
            hass=hass,
            runtime=runtime,
            control=control,
            owner=owner,
            target=target,
            peer=peer,
            clock=clock,
            Clock=Clock,
            entities=entities,
            meter=meter,
            profiles=profiles,
            config=config,
        )
    finally:
        for platform in platforms:
            await platform.async_reset()
        await config._async_process_on_unload(hass)
        await profiles.close()
        remove_owner()
        control.close()
        await hass.async_stop()


async def test_mixed_passive_publication_counts(mixed, monkeypatch):
    m = mixed
    published = Mock(wraps=m.runtime._publish)
    changed = Mock(wraps=m.owner.changed)
    inventory = Mock(wraps=m.owner.inventory)
    monkeypatch.setattr(m.runtime, "_publish", published)
    monkeypatch.setattr(m.owner, "changed", changed)
    monkeypatch.setattr(m.owner, "inventory", inventory)
    ids = [e.entity_id for e in m.entities]
    changes, reports = [], []

    @callback
    def on_changed(event):
        changes.append(event.data["entity_id"])

    @callback
    def on_reported(event):
        reports.append(event.data["entity_id"])

    unlisten = [
        async_track_state_change_event(m.hass, ids, on_changed),
        async_track_state_report_event(m.hass, ids, on_reported),
    ]
    meter_times = {round(i * 119 / 55): i for i in range(56)}
    phase_times = {round(i * 119 / 107) for i in range(108)}
    profiler = cProfile.Profile()
    before_operations = list(m.peer.operations)
    unchanged_inventory = unchanged_controls = 0
    control_ids = {
        e.entity_id
        for e in m.entities
        if isinstance(e, ControlEntity) and e.key != "charging_enabled"
    }
    profiler.enable()
    try:
        for second in range(120):
            m.clock[0] = second + 1
            now = m.Clock.now(UTC)
            token = m.runtime.get(m.target.station).token
            if second in (25, 27, 75, 77):
                authority = (
                    ControlAuthority.REMOTE
                    if second in (25, 75)
                    else ControlAuthority.LOCAL
                )
                m.runtime.observe_authority(
                    token,
                    AuthorityObservation(m.target.station, authority, now, "physical"),
                )
            if second == 90:
                m.runtime.disconnect(token)
            if second == 92:
                m.runtime.connect(
                    m.target.station, protocol="ocpp", protocol_version="2.1"
                )
            token = m.runtime.get(m.target.station).token
            if second == 93:
                token = m.runtime.boot(token, m.runtime.get(m.target.station).identity)
                m.runtime.observe_authority(
                    token,
                    AuthorityObservation(
                        m.target.station,
                        ControlAuthority.LOCAL,
                        now,
                        "reconnect readback",
                    ),
                )
            if second in (85, 86):
                # These reports repeat values with valid evidence in a settled
                # Local context; they must not rebuild inventory/configuration.
                before_inventory, before_reports = inventory.call_count, len(reports)
                before_changes = len(changes)
            if second % 5 == 0 and second <= 110:
                enabled = not 40 <= second < 55
                m.runtime.observe_enabled(
                    token,
                    EnabledObservation(
                        m.target, enabled, now, now + timedelta(seconds=15)
                    ),
                )
            if second in phase_times:
                mode = PhaseMode.canonical(3 if 45 <= second < 60 else 1)
                m.runtime.observe_physical_phase(
                    token,
                    PhysicalPhaseObservation(
                        m.target, mode, now, now + LIFETIME, "physical"
                    ),
                )
            if second in meter_times:
                index = meter_times[second]
                voltage = 232 if index % 8 == 0 else 230
                m.runtime.observe(
                    token, m.meter(voltage, 0 if 40 <= second < 55 else 1500)
                )
            for entity in m.entities:
                if isinstance(entity, SessionEntity) and entity.key == "duration":
                    entity._cancel_refresh()
                    entity._tick(now)
            await m.hass.async_block_till_done()
            if second in (85, 86):
                unchanged_inventory += inventory.call_count - before_inventory
                unchanged_controls += sum(
                    e in control_ids for e in reports[before_reports:]
                )
                unchanged_controls += sum(
                    e in control_ids for e in changes[before_changes:]
                )
    finally:
        profiler.disable()
        for remove in unlisten:
            remove()
    stats = pstats.Stats(profiler).stats

    def calls(suffix, function):
        return sum(
            v[1] for k, v in stats.items() if k[0].endswith(suffix) and k[2] == function
        )

    counts = dict(
        runtime_publications=published.call_count,
        ownership_callbacks=changed.call_count,
        ownership_inventory=inventory.call_count,
        control_view=calls("core/events.py", "control_view"),
        actively_charging=calls("core/events.py", "actively_charging"),
        resolve_capabilities=calls("core/electrical.py", "resolve_capabilities"),
        control_telemetry=calls("control_entity.py", "_telemetry"),
        expiry_refresh=calls("control_entity.py", "_refresh_expiry"),
        expiry_selectors=calls("control_entity.py", "expiry_view"),
        control_attributes=calls("control_entity.py", "extra_state_attributes"),
        ha_state_writes=len(reports) + len(changes),
        ha_state_changes=len(changes),
        control_entities=sum(isinstance(e, ControlEntity) for e in m.entities),
        submitted_meters=len(meter_times),
        submitted_phases=len(phase_times),
        submitted_permissions=23,
        unchanged_inventory=unchanged_inventory,
        unchanged_controls=unchanged_controls,
        ocpp_control_commands=len(m.peer.operations) - len(before_operations),
    )
    print(json.dumps(counts, sort_keys=True))
    assert m.peer.operations == before_operations
    assert m.runtime.sessions.get(m.target).active
    if not os.environ.get("WBM_MEASURE_ONLY"):
        assert unchanged_inventory == 0
        assert unchanged_controls == 0
        assert counts["ownership_callbacks"] < 20
        assert counts["control_view"] < 1000
        assert counts["control_attributes"] < 2000
