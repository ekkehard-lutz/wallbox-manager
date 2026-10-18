"""Recorder boundaries, retained evidence and passive settings/diagnostics."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from conftest import entities
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_report_event

from custom_components.wallbox_manager import session, store
from custom_components.wallbox_manager.config_flow import Options


async def test_retained_observation_reconnect_and_exact_energy(ha):
    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    key = ("observation", ("Wallbox01", "1", None), "energy")
    state.observe(gen, key, 123.456789012, "meter", unit="kWh")
    await ha.async_block_till_done()
    reading = next(e for e in entities(ha, entry) if e.key == key)
    old = state.observations[key]
    state.reset(connected=False)
    assert state.observations[key] is old
    assert state.value(key) is None
    assert ha.states.get(reading.entity_id).state == "unavailable"
    assert all(
        e.available
        for e in entities(ha, entry)
        if e.family == "control" and e.role != "charging_enabled"
    )
    new = state.reset(connected=True)
    assert not reading.available
    assert state.observations[key] is old
    assert not state.observe(gen, key, 9, "late")
    assert not state.observe(new, key, old.value, "meter", old.observed_at)
    assert not reading.available
    assert state.observe(new, key, old.value, "meter", unit="kWh")
    assert ha.states.get(reading.entity_id).state == str(old.value)
    assert state.observe(new, key, 123.456789013, "meter", unit="kWh")
    assert reading.native_value == 123.456789013
    assert not state.observe(new, key, 1, "late", old.observed_at)
    assert "observed_at" not in reading.extra_state_attributes
    assert "received_at" not in reading.extra_state_attributes


async def test_phase_last_observation_has_no_expiry(ha, monkeypatch):
    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    key = ("station", ("Wallbox01", None, None), "physical_phases")
    at = datetime.now(UTC)
    state.observe(gen, key, 3, "phase", at)
    phase = next(e for e in entities(ha, entry) if e.key == key)
    assert phase.native_value == 3 and not hasattr(phase, "_expiry")
    write = Mock(wraps=phase.async_write_ha_state)
    monkeypatch.setattr(phase, "async_write_ha_state", write)
    for i in range(2000):
        moment = at + timedelta(microseconds=i + 1)
        state.observe(gen, key, 3, "phase", moment)
    assert write.call_count == 0
    print(
        "PHASE_BENCHMARK "
        + json.dumps(
            {
                "identical_reports": 2000,
                "ha_writes": write.call_count,
                "expiry_timers": 0,
            }
        )
    )

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return at + timedelta(hours=5)

    monkeypatch.setattr(store, "datetime", Later)
    assert state.value(key) == 3
    assert phase.native_value == 3 and phase.available
    assert phase.extra_state_attributes["valid_until"] is None
    assert write.call_count == 0
    state.reset(connected=False)
    assert not phase.available
    assert state.observations[key].value == 3


async def test_duration_minutes_final_duration_and_cleanup(ha, monkeypatch):
    from homeassistant.util import dt as dt_util

    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    start = datetime.now(UTC)
    session.event(state, gen, state.binding, "actual", "Started", start, 0)
    await ha.async_block_till_done()
    duration = next(e for e in entities(ha, entry) if e.role == "duration")
    changes, reports = [], []

    @callback
    def changed(event):
        if event.data["entity_id"] == duration.entity_id:
            changes.append(event)

    @callback
    def reported(event):
        reports.append(event)

    stop = ha.bus.async_listen("state_changed", changed)
    stop_reports = async_track_state_report_event(ha, [duration.entity_id], reported)
    # Exercise the actual HA interval callback and its configured cadence;
    # time_changed no longer drives interval timers in modern HA.
    from custom_components.wallbox_manager import sensor

    class Clock(datetime):
        moment = start

        @classmethod
        def now(cls, tz=None):
            return cls.moment

    monkeypatch.setattr(sensor, "datetime", Clock)
    track = duration._refresh.__self__
    for second in range(1, 3601):
        if second % track.seconds:
            continue
        now = Clock.moment = start + timedelta(seconds=second)
        monkeypatch.setattr(dt_util, "utcnow", lambda now=now: now)
        handle = track._timer_handle
        action, args = handle._callback, handle._args
        handle.cancel()
        action(*args)
        await ha.async_block_till_done()
    print(
        "DURATION_BENCHMARK "
        + json.dumps(
            {
                "interval_seconds": track.seconds,
                "ha_writes": len(changes) + len(reports),
                "state_changed": len(changes),
                "state_reported": len(reports),
                "estimated_recorder_rows": len(changes),
            }
        )
    )
    assert len(changes) == 60
    assert len(changes) + len(reports) == 60
    assert duration._refresh is not None
    state.reset(connected=False)
    assert duration._refresh is None and not duration.available
    gen = state.reset(connected=True)
    assert duration._refresh is None and not duration.available
    # Reconfirm transaction, then obtain the authentic final duration.
    end = start + timedelta(seconds=3661)
    # Source future fence is handled by OCPP; direct lifecycle uses event time.
    session.event(state, gen, state.binding, "actual", "Updated", end, 1)
    assert duration.available and duration._refresh is not None
    session.event(state, gen, state.binding, "actual", "Ended", end, 2)
    assert duration.native_value == 3661 and duration._refresh is None
    state.reset(connected=False)
    assert duration.available and duration.native_value == 3661
    stop()
    stop_reports()


async def test_options_selectors_precedence_validation_and_no_effects(ha):
    entry = await ha.install()
    settings = entry.runtime_data.settings
    ha.config_entries.async_update_entry(
        entry,
        options={
            "diagnostic_level": 1,
            "leistung_pv": "sensor.pv",
            "foreign": {"keep": True},
            "pv_stop_delay": 123,
        },
    )
    await ha.async_block_till_done()
    flow = Options()
    flow.hass = ha
    flow.handler = entry.entry_id
    form = await flow.async_step_init()
    schema = form["data_schema"].schema
    sections = {key.schema: value for key, value in schema.items()}
    general = sections["general"].schema.schema
    fields = {key.schema: value for key, value in general.items()}
    assert fields["leistung_pv"].config["domain"] == ["sensor"]
    assert fields["min_soc_speicher"].config["domain"] == ["number", "input_number"]
    assert fields["soc_speicher_aktuell"].selector_type == "entity"
    result = await flow.async_step_init(
        {"general": {"diagnostic_level": "3"}, "regulation": {"pv_start_delay": 20}}
    )
    assert result["data"]["leistung_pv"] == "sensor.pv"
    assert result["data"]["foreign"] == {"keep": True}
    assert result["data"]["pv_stop_delay"] == 123
    ha.config_entries.async_update_entry(entry, options=result["data"])
    await ha.async_block_till_done()
    assert settings.get("pv_start_delay") == 20
    assert entry.runtime_data.server.adapter is None
    assert entry.runtime_data.state.generation == (0, 0)
    invalid = await flow.async_step_init({"regulation": {"regulation_interval": 0}})
    assert invalid["errors"] == {"base": "invalid_settings"}
    cleared = await flow.async_step_init({"general": {"leistung_pv": ""}})
    assert "leistung_pv" not in cleared["data"]


@pytest.mark.parametrize("level", [0, 1, 2, 3])
async def test_diagnostic_levels_bounded_and_fail_safe(ha, caplog, monkeypatch, level):
    entry = await ha.install()
    listener = entry.runtime_data.server
    ha.config_entries.async_update_entry(entry, options={"diagnostic_level": level})
    caplog.clear()
    listener.diagnostic(0, "error", reason="test")
    for _ in range(10):
        listener.diagnostic(1, "connection_accepted", reason="transport_accepted")
        listener.diagnostic(2, "transition_charging_state", value="charging")
        listener.diagnostic(3, "read_completed", operation="GetVariables")
    records = [
        json.loads(r.message.split("WBMGR subsystem=ocpp ")[1])
        for r in caplog.records
        if "WBMGR subsystem=ocpp " in r.message
    ]
    assert len(records) == level + 1
    assert all(r["station"] == "Wallbox01" and "generation" in r for r in records)
    monkeypatch.setattr(listener, "options", Mock(side_effect=RuntimeError))
    listener.diagnostic(0, "must_not_escape")


async def test_meter_session_projection_publishes_real_changes_only(ha, monkeypatch):
    from custom_components.wallbox_manager.ocpp import Adapter

    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    session.event(state, gen, state.binding, "actual", "Started", datetime.now(UTC), 0)
    adapter = Adapter(Mock(), entry.runtime_data.server, gen)

    def meter(value):
        adapter.meters(
            1,
            [
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "sampled_value": [
                        {
                            "measurand": "Power.Active.Import",
                            "value": value,
                            "unit_of_measure": {"unit": "W"},
                        }
                    ],
                }
            ],
        )

    meter(100)
    await ha.async_block_till_done()
    power = next(
        e for e in entities(ha, entry) if e.family == "session" and e.role == "power"
    )
    write = Mock(wraps=power.async_write_ha_state)
    monkeypatch.setattr(power, "async_write_ha_state", write)
    for _ in range(2000):
        meter(100)
    assert write.call_count == 0
    meter(100.00000001)
    assert write.call_count == 0  # Raw noise remains within the normalized bin.
    meter(100.05)
    assert write.call_count == 1
    assert ha.states.get(power.entity_id).state == "100.1"
    assert state.session["energy"] is None
    state.reset(connected=False)
    assert not power.available
    adapter.generation = state.reset(connected=True)
    session.event(
        state,
        state.generation,
        state.binding,
        "actual",
        "Updated",
        datetime.now(UTC),
        1,
    )
    assert not power.available  # Transaction confirmation is not a power report.
    meter(100.05)
    assert power.available
    assert ha.states.get(power.entity_id).state == "100.1"


@pytest.mark.parametrize("recovery", [3, 1])
async def test_phase_single_invalid_preserves_canonical_value(
    ha, monkeypatch, recovery
):
    from dataclasses import replace

    from custom_components.wallbox_manager.ocpp import Adapter

    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, gen)
    key = ("station", ("Wallbox01", None, None), "physical_phases")
    component = {"name": "Connector", "evse": {"id": 1, "connector_id": 1}}
    start = datetime.now(UTC)

    def report(raw, index, source="NotifyEvent"):
        adapter.variable(
            gen,
            component,
            {"name": "PhaseRotation"},
            raw,
            start + timedelta(microseconds=index),
            source,
        )

    report("RST", 0)
    phase = next(e for e in entities(ha, entry) if e.key == key)
    original = state.observations[key]
    published = ha.states.get(phase.entity_id)
    write = Mock(wraps=phase.async_write_ha_state)
    monkeypatch.setattr(phase, "async_write_ha_state", write)
    changes, reports = [], []

    @callback
    def changed(event):
        if event.data["entity_id"] == phase.entity_id:
            changes.append(event)

    @callback
    def reported(event):
        reports.append(event)

    stop = ha.bus.async_listen("state_changed", changed)
    stop_reports = async_track_state_report_event(ha, [phase.entity_id], reported)
    report("", 1, "GetVariables")  # Invalid source does not replace valid provenance.
    invalid = state.observations[key]
    assert invalid == replace(
        original, reported_at=start + timedelta(microseconds=1), invalid_count=1
    )
    assert phase.available and phase.native_value == 3
    assert state.last_observed_value(key) == 3
    assert state.value(key) == 3
    assert ha.states.get(phase.entity_id) is published
    assert not hasattr(phase, "_expiry")
    report("RST" if recovery == 3 else "Rxx", 2)
    assert state.value(key) == recovery
    assert state.last_observed_value(key) == recovery
    assert state.observations[key].invalid_count == 0
    assert state.observations[key].observed_at == start + timedelta(microseconds=2)
    await ha.async_block_till_done()
    assert write.call_count == (0 if recovery == 3 else 1)
    assert len(changes) == (0 if recovery == 3 else 1)
    assert not reports
    assert "observed_at" not in phase.extra_state_attributes
    assert "received_at" not in phase.extra_state_attributes
    stop()
    stop_reports()


async def test_phase_two_distinct_invalid_reports_only_publish_availability(ha, caplog):
    from custom_components.wallbox_manager.ocpp import Adapter

    entry = await ha.install()
    ha.config_entries.async_update_entry(entry, options={"diagnostic_level": 2})
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, gen)
    key = ("station", ("Wallbox01", None, None), "physical_phases")
    component = {"name": "Connector", "evse": {"id": 1, "connector_id": 1}}
    start = datetime.now(UTC)

    def report(raw, i):
        adapter.variable(
            gen,
            component,
            {"name": "PhaseRotation"},
            raw,
            start + timedelta(microseconds=i),
            "NotifyEvent",
        )

    report("RST", 0)
    phase = next(e for e in entities(ha, entry) if e.key == key)
    changes, reports = [], []

    @callback
    def changed(event):
        if event.data["entity_id"] == phase.entity_id:
            changes.append(event.data["new_state"].state)

    @callback
    def reported(event):
        reports.append(event)

    stop = ha.bus.async_listen("state_changed", changed)
    stop_reports = async_track_state_report_event(ha, [phase.entity_id], reported)
    report("", 1)
    assert phase.available and state.value(key) == 3
    report("unrecognized", 2)
    assert not phase.available and phase.native_value is None
    assert state.last_observed_value(key) is None
    assert state.observations[key].value == 3
    assert state.observations[key].observed_at == start
    for i in range(3, 2003):
        report("", i)
    assert state.observations[key].invalid_count == 2
    report("RST", 2003)
    assert phase.available and state.value(key) == 3
    await ha.async_block_till_done()
    assert changes == ["unavailable", "3"]
    assert not reports
    logs = [
        json.loads(record.message.split("WBMGR subsystem=ocpp ")[1])
        for record in caplog.records
        if "WBMGR subsystem=ocpp " in record.message
    ]
    assert [
        record["value"]
        for record in logs
        if record["event"] == "transition_physical_phases"
    ] == [3, None, 3]
    stop()
    stop_reports()


def test_phase_report_watermark_duplicates_conflicts_and_ordering():
    state = store.State(("Wallbox01", "1", "1"))
    gen = state.reset(connected=True)
    key = ("station", ("Wallbox01", None, None), "physical_phases")
    start = datetime.now(UTC)

    def observe(value, index):
        at = start + timedelta(microseconds=index)
        return state.observe(gen, key, value, "phase", at)

    assert observe(3, 0)
    assert observe(None, 2)
    first_invalid = state.observations[key]
    assert not observe(None, 2)
    assert not observe(3, 2)  # Equal-time recovery cannot clear the invalid report.
    assert not observe(None, 1)
    assert not observe(1, 1)
    assert state.observations[key] is first_invalid
    assert state.last_observed_value(key) == state.value(key) == 3
    assert observe(None, 3)
    assert state.last_observed_value(key) is None
    assert not observe(3, 2)
    assert not observe(None, 3)
    assert observe(1, 4)
    assert state.value(key) == 1
    assert observe(
        3, 4
    )  # Conflicting values at one timestamp count as one invalid report.
    assert state.last_observed_value(key) == state.value(key) == 1
    assert state.observations[key].invalid_count == 1
    assert not observe(3, 4)
    assert not observe(1, 4)
    assert observe(1, 5)
    assert state.value(key) == 1
    assert observe(3, 6)  # Genuine changes are immediate in either direction.
    assert state.value(key) == 3


@pytest.mark.parametrize("boot", [False, True])
async def test_phase_initial_invalid_and_generation_fencing(ha, boot):
    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    key = ("station", ("Wallbox01", None, None), "physical_phases")
    phase = next(e for e in entities(ha, entry) if e.key == key)
    assert not phase.available and phase.native_value is None
    assert state.observe(gen, key, None, "invalid")
    assert state.value(key) is None and state.last_observed_value(key) is None
    assert state.observe(gen, key, 3, "phase")
    valid = state.observations[key]
    assert state.observe(gen, key, None, "invalid")
    assert phase.available and state.value(key) == 3
    if not boot:
        state.reset(connected=False)
        assert not phase.available and phase.native_value is None
        assert state.observations[key].value == 3
    new = state.reset(connected=True, boot=boot)
    assert not phase.available and state.value(key) is None
    assert not state.observe(gen, key, 3, "late")
    assert not state.observe(gen, key, None, "late")
    assert not state.observe(new, key, 3, "old-source", valid.observed_at)
    assert state.observe(new, key, None, "new-invalid")
    assert not phase.available  # No valid phase sample in this generation.
    assert state.observations[key].invalid_count == 1
    assert state.observations[key].generation == valid.generation
    assert state.observations[key].observed_at == valid.observed_at
    assert state.observations[key].received_at == valid.received_at
    assert state.observations[key].reported_generation == new
    assert state.observe(new, key, 3, "new-valid")
    assert phase.available and state.value(key) == 3
    assert state.observations[key].invalid_count == 0


async def test_phase_invalid_sequences_are_entry_local(ha):
    from conftest import config

    one, two = await ha.install(), await ha.install(config("Other"))
    a, b = one.runtime_data.state, two.runtime_data.state
    ga, gb = a.reset(connected=True), b.reset(connected=True)
    ka = ("station", ("Wallbox01", None, None), "physical_phases")
    kb = ("station", ("Other", None, None), "physical_phases")
    a.observe(ga, ka, 3, "phase")
    b.observe(gb, kb, 1, "phase")
    original = b.observations[kb]
    a.observe(ga, ka, None, "invalid")
    a.observe(ga, ka, None, "invalid")
    assert a.last_observed_value(ka) is None and a.value(ka) is None
    assert b.observations[kb] is original
    assert b.last_observed_value(kb) == b.value(kb) == 1
    assert not b.observe(gb, ka, None, "foreign")
