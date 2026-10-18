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


async def test_phase_last_observation_and_proof_expiry(ha, monkeypatch):
    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    key = ("station", ("Wallbox01", None, None), "physical_phases")
    at = datetime.now(UTC)
    state.observe(gen, key, 3, "phase", at, valid_until=at + timedelta(seconds=5))
    phase = next(e for e in entities(ha, entry) if e.key == key)
    assert phase.native_value == 3 and phase._expiry is None
    write = Mock(wraps=phase.async_write_ha_state)
    monkeypatch.setattr(phase, "async_write_ha_state", write)
    for i in range(2000):
        moment = at + timedelta(microseconds=i + 1)
        state.observe(
            gen, key, 3, "phase", moment, valid_until=moment + timedelta(seconds=5)
        )
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
            return at + timedelta(seconds=6)

    monkeypatch.setattr(store, "datetime", Later)
    assert state.value(key) is None  # Expired proof cannot support future control.
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
    assert write.call_count == 1
    assert ha.states.get(power.entity_id).state == "100.00000001"
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
    meter(100.00000001)
    assert power.available
    assert ha.states.get(power.entity_id).state == "100.00000001"
