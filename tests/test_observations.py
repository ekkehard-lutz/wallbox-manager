"""Targeted HA publication, held measurements, generations and passive sessions."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from conftest import config, entities
from homeassistant.helpers.storage import Store

from custom_components.wallbox_manager import session
from custom_components.wallbox_manager.meter import samples


def test_units_phases_invalid_values_and_boundary_energy():
    now = datetime.now(UTC).isoformat()
    groups = [
        {
            "timestamp": now,
            "sampled_value": [
                {
                    "measurand": "Voltage",
                    "phase": "L1-N",
                    "value": 230000,
                    "unit_of_measure": {"unit": "mV"},
                },
                {
                    "measurand": "Current.Import",
                    "phase": "L2",
                    "value": 0,
                    "unit_of_measure": {"unit": "A"},
                },
                {
                    "measurand": "Power.Active.Import",
                    "value": 1.5,
                    "unit_of_measure": {"unit": "kW", "multiplier": 0},
                },
                {"value": 123456, "unit_of_measure": {"unit": "Wh"}},
            ],
        }
    ]
    values = {r: (v, u) for r, v, _, u in samples(groups)}
    assert values == {
        "voltage_l1": (230, "V"),
        "current_l2": (0, "A"),
        "power": (1500, "W"),
        "energy": (123.456, "kWh"),
    }
    group = [
        {
            "timestamp": now,
            "sampled_value": [
                {
                    "value": 10,
                    "context": "Transaction.Begin",
                    "unit_of_measure": {"unit": "Wh"},
                }
            ],
        }
    ]
    assert list(samples(group)) == []
    assert list(samples(group, session=True))[0][1] == 0.01
    groups[0]["sampled_value"][0]["value"] = -1
    assert list(samples(groups))[0][1] is None
    groups[0]["timestamp"] = "2026-01-01T00:00:00"
    assert list(samples(groups)) == []


async def test_narrow_writes_provenance_and_no_measurement_timeout(ha, monkeypatch):
    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    scope = ("Wallbox01", "1", None)
    power = ("observation", scope, "power")
    voltage = ("observation", scope, "voltage_l1")
    state.observe(gen, power, 1500, "meter", unit="W")
    state.observe(gen, voltage, 230, "meter", unit="V")
    await ha.async_block_till_done()
    all_entities = entities(ha, entry)
    writes = {e.unique_id: Mock(wraps=e.async_write_ha_state) for e in all_entities}
    # Replace registered callbacks to count actual targeted state writes.
    for e in all_entities:
        monkeypatch.setattr(e, "async_write_ha_state", writes[e.unique_id])
    first = state.observations[power]
    state.observe(gen, power, 1500, "meter", unit="W")
    await ha.async_block_till_done()
    reading = next(e for e in all_entities if e.key == power)
    assert writes[reading.unique_id].call_count == 0
    assert all(
        w.call_count == 0 for uid, w in writes.items() if uid != reading.unique_id
    )
    assert state.observations[power].observed_at > first.observed_at
    from custom_components.wallbox_manager import store

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(days=3)

    monkeypatch.setattr(store, "datetime", Later)
    assert state.value(power) == 1500
    assert reading.available
    assert reading.extra_state_attributes["valid_until"] is None
    monkeypatch.undo()
    assert not state.observe(
        gen, ("observation", ("Other", "1", None), "power"), 1, "bad"
    )
    assert not state.observe(
        gen, ("observation", ("Wallbox01", "2", None), "power"), 1, "bad"
    )
    next_gen = state.reset(connected=True)
    assert state.value(power) is None
    assert not state.observe(gen, power, 999, "late")
    assert not state.observe(next_gen, power, 999, "old-source", first.observed_at)
    state.observe(next_gen, power, None, "unknown", status="unknown")
    assert not reading.available


async def test_two_instances_are_isolated(ha):
    one, two = await ha.install(config("A")), await ha.install(config("B"))
    assert one.runtime_data.server.port != two.runtime_data.server.port
    a, b = one.runtime_data.state, two.runtime_data.state
    ga, gb = a.reset(connected=True), b.reset(connected=True)
    key = ("observation", ("A", "1", None), "energy")
    a.observe(ga, key, 42, "meter")
    assert not b.observe(gb, key, 88, "foreign")
    await one.runtime_data.settings.set("soll_power", 4.2)
    assert two.runtime_data.settings.get("soll_power") == 11
    assert not b.observations


async def test_passive_session_energy_is_not_lifetime_and_legacy_store_untouched(ha):
    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    at = datetime.now(UTC)
    assert session.event(
        state, gen, state.binding, "real-transaction", "Started", at, 0
    )
    session.meter(state, "energy", 100, at, boundary=True)
    later = datetime.now(UTC)
    session.meter(state, "energy", 100.8, later)
    session.meter(state, "power", 1500, later)
    assert session.view(state, later)["energy"] == pytest.approx(0.8)
    assert session.view(state, later)["power"] == 1500
    assert not any(k[2] == "energy" for k in state.observations)
    assert not session.event(
        state, gen, state.binding, "real-transaction", "Updated", at, 0
    )
    assert session.event(
        state, gen, state.binding, "real-transaction", "Ended", later, 1
    )
    assert session.view(state, later)["active"] is False
    assert session.view(state, later)["power"] == 0
    assert not session.event(
        state, gen, state.binding, "real-transaction", "Started", later, 2
    )
    await ha.async_block_till_done()
    reading = next(
        e for e in entities(ha, entry) if e.family == "session" and e.role == "duration"
    )
    assert reading._refresh is None
    legacy = Store(ha, 1, f"wallbox_manager.{entry.entry_id}.sessions")
    assert await legacy.async_load() is None
    state.reset(connected=True)
    assert session.view(state, later)["energy"] == pytest.approx(0.8)


@pytest.mark.parametrize("elapsed", [5, 15, 300, 18000])
async def test_all_observations_hold_without_expiry_callbacks(ha, monkeypatch, elapsed):
    from custom_components.wallbox_manager import entity, store
    from custom_components.wallbox_manager.ocpp import Adapter

    entry = await ha.install()
    state = entry.runtime_data.state
    generation = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, generation)
    scope = ("Wallbox01", "1", None)
    values = {
        ("observation", scope, "voltage_l1"): 230.1,
        ("observation", scope, "current_l1"): 6.123,
        ("observation", scope, "power"): 1409.9,
    }
    for key, value in values.items():
        state.observe(generation, key, value, "meter")
    at = datetime.now(UTC)
    adapter.variable(
        generation,
        {"name": "WallboxController"},
        {"name": "ChargingEnabled"},
        "true",
        at,
        "NotifyEvent",
    )
    adapter.variable(
        generation,
        {"name": "Connector", "evse": {"id": 1, "connector_id": 1}},
        {"name": "PhaseRotation"},
        "RST",
        at,
        "NotifyEvent",
    )
    permission = ("control", state.binding, "charging_enabled")
    phase = ("station", ("Wallbox01", None, None), "physical_phases")
    values.update({permission: True, phase: 3})
    readings = [e for e in entities(ha, entry) if e.key in values]
    writes = [Mock(wraps=e.async_write_ha_state) for e in readings]
    for e, write in zip(readings, writes, strict=True):
        monkeypatch.setattr(e, "async_write_ha_state", write)
        assert not hasattr(e, "_expiry")
        assert not hasattr(state.observations[e.key], "valid_until")
    assert not hasattr(entity, "async_track_point_in_utc_time")

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return at + timedelta(seconds=elapsed)

    monkeypatch.setattr(store, "datetime", Later)
    assert all(state.value(key) == value for key, value in values.items())
    assert all(e.available for e in readings)
    assert not any(write.called for write in writes)
    # A single explicit invalid phase report retains the same canonical value
    # even after prolonged silence; a second distinct report removes it.
    later = Later.now(UTC)
    state.observe(generation, phase, None, "NotifyEvent", later)
    assert state.value(phase) == state.last_observed_value(phase) == 3
    state.observe(
        generation, phase, None, "NotifyEvent", later + timedelta(microseconds=1)
    )
    assert state.value(phase) is None
    state.observe(
        generation, phase, 1, "NotifyEvent", later + timedelta(microseconds=2)
    )
    assert state.value(phase) == 1

    from custom_components.wallbox_manager import async_unload_entry

    await async_unload_entry(ha, entry)
    assert not state.listeners
    assert state.add_sensor is None
    del entry.runtime_data


async def test_ended_transaction_cannot_reopen_after_another_session(ha):
    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    assert session.event(
        state, gen, state.binding, "old", "Started", datetime.now(UTC), 0
    )
    assert session.event(
        state, gen, state.binding, "old", "Ended", datetime.now(UTC), 1
    )
    assert session.event(
        state, gen, state.binding, "new", "Started", datetime.now(UTC), 0
    )
    assert not session.event(
        state, gen, state.binding, "old", "Updated", datetime.now(UTC), 2
    )
    assert state.session["external_transaction_id"] == "new"


async def test_delayed_and_superseded_transactions_cannot_replace_current(ha):
    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    first = datetime.now(UTC)
    session.event(state, gen, state.binding, "old", "Started", first, 0)
    later = datetime.now(UTC)
    session.event(state, gen, state.binding, "current", "Started", later, 0)
    assert not session.event(state, gen, state.binding, "delayed", "Started", first, 0)
    assert not session.event(
        state, gen, state.binding, "old", "Updated", datetime.now(UTC), 10
    )
    assert state.session["external_transaction_id"] == "current"


async def test_session_energy_streams_and_final_endpoint_are_not_invented(ha):
    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    start = datetime.now(UTC)
    session.event(state, gen, state.binding, "real", "Started", start, 0)
    session.meter(state, "energy", 0, start, boundary=True)
    power = ("observation", ("Wallbox01", "1", None), "power")
    fresh = datetime.now(UTC)
    state.observe(gen, power, 800, "meter", fresh)
    session.meter(state, "power", 800, fresh, channel=power)
    session.meter(state, "power", 100, start)
    assert session.view(state, fresh)["power"] == 800
    session.meter(
        state,
        "energy",
        12345,
        datetime.now(UTC),
        channel=("observation", ("Wallbox01", "1", None), "energy"),
    )
    assert session.view(state, start)["energy"] == 0
    session.meter(state, "energy", 1.2, datetime.now(UTC), boundary=True)
    assert session.view(state, start)["energy"] == 1.2
    end = datetime.now(UTC)
    session.event(state, gen, state.binding, "real", "Ended", end, 1)
    assert session.view(state, end)["energy"] is None
    assert session.view(state, end)["end_meter"] is None
    session.meter(state, "energy", 1.3, start, boundary=True)
    assert session.view(state, end)["energy"] is None
    session.meter(state, "energy", 1.4, end, boundary=True)
    assert session.view(state, end)["energy"] == 1.4
    # A reset at the final endpoint must remain unknown, even after Ended cleared
    # the public provisional meter value.
    start = datetime.now(UTC)
    session.event(state, gen, state.binding, "reset", "Started", start, 0)
    session.meter(state, "energy", 0, start, boundary=True)
    session.meter(state, "energy", 2, datetime.now(UTC), boundary=True)
    end = datetime.now(UTC)
    session.event(state, gen, state.binding, "reset", "Ended", end, 1)
    session.meter(state, "energy", 1, end, boundary=True)
    assert session.view(state, end)["energy"] is None


async def test_superseded_parent_session_is_unavailable_and_stops_timer(ha):
    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    scope = ("Wallbox01", "1", None)
    session.event(state, gen, scope, "parent", "Started", datetime.now(UTC), 0)
    await ha.async_block_till_done()
    duration = next(e for e in entities(ha, entry) if e.role == "duration")
    assert duration._refresh is not None
    session.event(state, gen, state.binding, "next", "Started", datetime.now(UTC), 0)
    await ha.async_block_till_done()
    assert not duration.available
    assert ha.states.get(duration.entity_id).state == "unavailable"
    assert duration._refresh is None


async def test_observation_metadata_and_all_local_setting_roles(ha):
    from custom_components.wallbox_manager.sensor import Reading
    from custom_components.wallbox_manager.settings import NUMBERS, SELECTS, TIMES

    entry = await ha.install()
    scope = ("Wallbox01", "1", None)
    for role, unit, device_class, state_class in (
        ("energy", "kWh", "energy", "total_increasing"),
        ("power", "W", "power", "measurement"),
        ("power_l1", "W", "power", "measurement"),
        ("current_l1", "A", "current", "measurement"),
        ("voltage_l1", "V", "voltage", "measurement"),
    ):
        reading = Reading(entry, "observation", scope, role)
        assert (
            reading.native_unit_of_measurement,
            reading.device_class,
            reading.state_class,
        ) == (unit, device_class, state_class)
    from homeassistant.helpers import entity_registry as er

    roles = {e.role for e in entities(ha, entry)}
    for item in er.async_entries_for_config_entry(er.async_get(ha), entry.entry_id):
        if ":control:" in item.unique_id:
            roles.add(json.loads(item.unique_id.split(":control:", 1)[1])[3])
    assert set(NUMBERS) | set(SELECTS) | set(TIMES) <= roles
    assert "active_wallbox" not in roles
    assert (
        next(e for e in entities(ha, entry) if e.role == "discovery").device_class
        == "enum"
    )


async def test_restart_keeps_session_identity_but_invalidates_live_proof(ha):
    from custom_components.wallbox_manager import async_unload_entry

    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    gen = state.reset(connected=True, boot=True)
    session.event(
        state, gen, state.binding, "real-persisted", "Started", datetime.now(UTC), 0
    )
    previous_id = state.session["session_id"]
    await async_unload_entry(ha, entry)
    assert not state.listeners
    await ha.install(entry)
    restored = entry.runtime_data.state
    same_counter = restored.reset(connected=True)
    same_counter = restored.reset(connected=True, boot=True)
    assert same_counter == gen
    assert restored.session["session_id"] == previous_id
    assert session.view(restored, datetime.now(UTC))["active"] is None
    assert session.view(restored, datetime.now(UTC))["duration"] is None
    assert session.event(
        restored,
        same_counter,
        restored.binding,
        "real-persisted",
        "Updated",
        datetime.now(UTC),
        1,
    )
    assert session.view(restored, datetime.now(UTC))["active"] is True


async def test_audited_numeric_channels_omissions_invalid_samples_and_exact_energy(ha):
    from custom_components.wallbox_manager.ocpp import Adapter

    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, gen)
    scope = ("Wallbox01", "1", None)
    power = ("observation", scope, "power")
    voltage = ("observation", scope, "voltage_l1")
    energy = ("observation", scope, "energy")

    def meters(samples):
        adapter.meters(
            1, [{"timestamp": datetime.now(UTC).isoformat(), "sampled_value": samples}]
        )

    meters(
        [
            {
                "measurand": "Power.Active.Import",
                "value": 100,
                "unit_of_measure": {"unit": "W"},
            },
            {
                "measurand": "Voltage",
                "phase": "L1",
                "value": 230,
                "unit_of_measure": {"unit": "V"},
            },
            {"value": 123.45678901, "unit_of_measure": {"unit": "kWh"}},
        ]
    )
    previous_power, previous_energy = (
        state.observations[power],
        state.observations[energy],
    )
    meters(
        [
            {
                "measurand": "Voltage",
                "phase": "L1",
                "value": -1,
                "unit_of_measure": {"unit": "V"},
            }
        ]
    )
    assert state.value(voltage) is None  # No global debounce of numeric invalidity.
    assert state.observations[voltage].invalid_count == 0
    assert state.observations[power] is previous_power
    assert state.observations[energy] is previous_energy
    meters([])  # Omission never erases a previously observed channel.
    assert state.observations[power] is previous_power
    meters([{"value": 123.45678902, "unit_of_measure": {"unit": "kWh"}}])
    assert state.value(energy) == 123.45678902
    assert state.value(power) == 100


async def test_audited_status_authority_capabilities_and_permission_are_not_debounced(
    ha,
):
    from custom_components.wallbox_manager.ocpp import Adapter

    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, gen)
    status = ("observation", state.binding, "connector_state")
    for raw, expected in [
        ("Available", "available"),
        ("Unavailable", "unavailable"),
        ("Faulted", "faulted"),
    ]:
        adapter.status(1, 1, raw, datetime.now(UTC).isoformat())
        assert state.value(status) == expected
    component = {"name": "WallboxController"}
    authority = ("station", ("Wallbox01", None, None), "control_authority")
    for raw, expected in [("OCPP", "remote"), ("Local", "local"), ("", "unknown")]:
        adapter.variable(
            gen,
            component,
            {"name": "ControlAuthority"},
            raw,
            datetime.now(UTC),
            "NotifyEvent",
        )
        assert state.value(authority) == expected
    capability = ("capability", ("Wallbox01", "1", None), "minimum_current")
    state.observe(gen, capability, 6, "inventory", status="verified")
    state.observe(gen, capability, None, "inventory", status="degraded")
    assert state.value(capability) is None
    assert state.observations[capability].status == "degraded"
    permission = ("control", state.binding, "charging_enabled")
    switch = next(e for e in entities(ha, entry) if e.key == permission)
    for raw, expected in [("true", True), ("", None), ("true", True), ("false", False)]:
        adapter.variable(
            gen,
            component,
            {"name": "ChargingEnabled"},
            raw,
            datetime.now(UTC),
            "NotifyEvent",
        )
        assert state.value(permission) is expected
        assert state.observations[permission].invalid_count == 0
        assert switch.is_on is expected
    state.reset(connected=False)
    assert not switch.available


async def test_unsuccessful_permission_readback_preserves_confirmation(ha, monkeypatch):
    from unittest.mock import AsyncMock

    from ocpp.v21 import call_result

    from custom_components.wallbox_manager.ocpp import Adapter

    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, gen)
    endpoint = {
        "component": {"name": "WallboxController"},
        "variable": {"name": "ChargingEnabled"},
    }
    permission = ("control", state.binding, "charging_enabled")
    adapter.variable(
        gen,
        endpoint["component"],
        endpoint["variable"],
        "true",
        datetime.now(UTC),
        "GetVariables",
    )
    confirmed = state.observations[permission]
    monkeypatch.setattr(adapter, "request", AsyncMock(side_effect=TimeoutError))
    with pytest.raises(TimeoutError):
        await adapter.read(gen, endpoint)
    assert (
        state.observations[permission] is confirmed
    )  # No failed read invents a new observation.
    monkeypatch.setattr(
        adapter,
        "request",
        AsyncMock(
            return_value=call_result.GetVariables(
                get_variable_result=[{**endpoint, "attribute_status": "Rejected"}]
            )
        ),
    )
    await adapter.read(gen, endpoint)
    assert state.value(permission) is True
    assert state.observations[permission] is confirmed
    assert state.observations[permission].invalid_count == 0


@pytest.mark.parametrize(
    "measurand,unit,raw,expected",
    [
        ("Voltage", "V", 231.43782, 231.4),
        ("Voltage", "mV", 231437.82, 231.4),
        ("Voltage", "kV", 0.23143782, 231.4),
        ("Current.Import", "A", 8.236728, 8.237),
        ("Current.Import", "mA", 8236.728, 8.237),
        ("Power.Active.Import", "W", 1234.56, 1234.6),
        ("Power.Active.Import", "kW", 1.23456, 1234.6),
        ("Power.Active.Import", "MW", 0.00123456, 1234.6),
    ],
)
def test_electrical_resolution_all_phases_and_supported_units(
    measurand, unit, raw, expected
):
    prefix, canonical = {
        "Voltage": ("voltage", "V"),
        "Current.Import": ("current", "A"),
        "Power.Active.Import": ("power", "W"),
    }[measurand]
    phases = ["L1", "L2", "L3"]
    if measurand == "Voltage":
        phases += ["L1-N", "L2-N", "L3-N"]
    if measurand == "Power.Active.Import":
        phases += [None]
    for phase in phases:
        value = list(
            samples(
                [
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "sampled_value": [
                            {
                                "measurand": measurand,
                                "phase": phase,
                                "value": raw,
                                "unit_of_measure": {"unit": unit},
                            }
                        ],
                    }
                ]
            )
        )
        role = prefix + "_" + phase[:2].lower() if phase else prefix
        assert len(value) == 1
        assert (value[0][0], value[0][1], value[0][3]) == (role, expected, canonical)


@pytest.mark.parametrize(
    "measurand,raw,unit,multiplier,expected",
    [
        ("Voltage", 231.45, "V", 0, 231.5),
        ("Voltage", 231.449999, "V", 0, 231.4),
        ("Voltage", 231.35, "V", 0, 231.4),
        ("Voltage", 23145, "V", -2, 231.5),
        ("Voltage", 0.23145, "V", 3, 231.5),
        ("Voltage", 0.05, "V", 0, 0.1),
        ("Current.Import", 8.2365, "A", 0, 8.237),
        ("Current.Import", 8.23649999, "A", 0, 8.236),
        ("Current.Import", 0.0005, "A", 0, 0.001),
        ("Current.Import", 82365, "A", -4, 8.237),
        ("Power.Active.Import", 1906.347, "W", 0, 1906.3),
        ("Power.Active.Import", 1906.35, "W", 0, 1906.4),
        ("Power.Active.Import", 1.23465, "kW", 0, 1234.7),
        ("Power.Active.Import", 123465, "W", -2, 1234.7),
        ("Power.Active.Import", 0, "W", 0, 0),
        ("Voltage", 1000000000000000049999999999999, "mV", -12, 1000000000000000.0),
        ("Voltage", 1000000000000000050000000000000, "mV", -12, 1000000000000000.1),
    ],
)
def test_decimal_half_up_boundaries_after_conversion(
    measurand, raw, unit, multiplier, expected
):
    values = list(
        samples(
            [
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "sampled_value": [
                        {
                            "measurand": measurand,
                            "phase": "L1",
                            "value": raw,
                            "unit_of_measure": {"unit": unit, "multiplier": multiplier},
                        }
                    ],
                }
            ]
        )
    )
    assert values[0][1] == expected


def test_raw_conflicts_are_checked_before_quantization():
    at = datetime.now(UTC)

    def voltage(raw, unit="V"):
        return {
            "measurand": "Voltage",
            "phase": "L1",
            "value": raw,
            "unit_of_measure": {"unit": unit},
        }

    def parse(values):
        return list(samples([{"timestamp": at.isoformat(), "sampled_value": values}]))

    assert parse([voltage(231.41), voltage(231.42)])[0][1] is None
    assert parse([voltage(231.41), voltage(231.42), voltage(231.41)])[0][1] is None
    assert parse([voltage(231.41), voltage(231.41)])[0][1] == 231.4
    assert parse([voltage(231.41), voltage(231410, "mV")])[0][1] == 231.4
    assert parse([voltage(-1), voltage(231.41)])[0][1] is None
    groups = [
        {"timestamp": at.isoformat(), "sampled_value": [voltage(231.41)]},
        {"timestamp": at.isoformat(), "sampled_value": [voltage(231.42)]},
    ]
    assert list(samples(groups))[0][1] is None
    groups[1]["timestamp"] = (at + timedelta(microseconds=1)).isoformat()
    assert [value for _, value, _, _ in samples(groups)] == [231.4, 231.4]


@pytest.mark.parametrize(
    "raw",
    [-1, float("inf"), float("-inf"), float("nan"), None, True, "231.45", {}, 1e19],
)
def test_resolution_preserves_invalid_numeric_semantics(raw):
    result = list(
        samples(
            [
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "sampled_value": [
                        {
                            "measurand": "Voltage",
                            "phase": "L1",
                            "value": raw,
                            "unit_of_measure": {"unit": "V"},
                        }
                    ],
                }
            ]
        )
    )
    assert result[0][1] is None


def test_resolution_preserves_missing_unsupported_and_energy_samples():
    at = datetime.now(UTC).isoformat()
    voltage = {
        "measurand": "Voltage",
        "phase": "L1",
        "value": 231.45,
        "unit_of_measure": {"unit": "V"},
    }
    for changes in [
        {"unit_of_measure": {"unit": "unsupported"}},
        {"unit_of_measure": {"unit": "V", "multiplier": 13}},
        {"unit_of_measure": {"unit": "V", "multiplier": True}},
        {"format": "SignedData"},
    ]:
        assert (
            list(
                samples([{"timestamp": at, "sampled_value": [{**voltage, **changes}]}])
            )[0][1]
            is None
        )
    assert list(samples([{"timestamp": at, "sampled_value": []}])) == []
    assert list(samples([{"timestamp": "invalid", "sampled_value": [voltage]}])) == []
    for changes in [
        {"measurand": "unsupported"},
        {"phase": "L4"},
        {"context": "unsupported"},
        {"location": "Inlet"},
    ]:
        assert (
            list(
                samples([{"timestamp": at, "sampled_value": [{**voltage, **changes}]}])
            )
            == []
        )
    missing = {k: v for k, v in voltage.items() if k != "value"}
    assert list(samples([{"timestamp": at, "sampled_value": [missing]}]))[0][1] is None
    for unit, raw in [
        ("kWh", 123.45678901),
        ("Wh", 123456.78901),
        ("MWh", 0.12345678901),
    ]:
        group = [
            {
                "timestamp": at,
                "sampled_value": [{"value": raw, "unit_of_measure": {"unit": unit}}],
            }
        ]
        assert list(samples(group))[0][1] == 123.45678901
        group[0]["sampled_value"][0]["context"] = "Transaction.Begin"
        assert list(samples(group)) == []
        assert list(samples(group, session=True))[0][1] == 123.45678901


async def test_session_and_ordinary_power_share_normalization_and_safety_is_unchanged(
    ha,
):
    from custom_components.wallbox_manager.ocpp import Adapter

    entry = await ha.install()
    state = entry.runtime_data.state
    generation = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, generation)
    at = datetime.now(UTC).isoformat()
    sample = {
        "measurand": "Power.Active.Import",
        "value": 1.906347,
        "unit_of_measure": {"unit": "kW"},
    }
    adapter.transaction(
        "Started",
        at,
        "CablePluggedIn",
        0,
        {"transaction_id": "actual-resolution"},
        evse={"id": 1, "connector_id": 1},
        meter_value=[{"timestamp": at, "sampled_value": [sample]}],
    )
    transaction = ("transaction", state.binding, "power")
    assert state.value(transaction) == 1906.3
    adapter.meters(
        1, [{"timestamp": datetime.now(UTC).isoformat(), "sampled_value": [sample]}]
    )
    normal = ("observation", ("Wallbox01", "1", None), "power")
    assert (
        state.value(normal) == session.view(state, datetime.now(UTC))["power"] == 1906.3
    )
    capability = ("capability", ("Wallbox01", "1", None), "minimum_current")
    state.observe(generation, capability, 6.123456789, "inventory", status="verified")
    assert state.value(capability) == 6.123456789
    controller = {"name": "WallboxController"}
    adapter.variable(
        generation,
        controller,
        {"name": "ChargingEnabled"},
        "false",
        datetime.now(UTC),
        "GetVariables",
    )
    assert state.value(("control", state.binding, "charging_enabled")) is False
    await entry.runtime_data.settings.set("allowed_current_1p", "19/3")
    assert entry.runtime_data.settings.get("allowed_current_1p") == float(19 / 3)


async def test_failed_actual_reads_leave_initial_and_confirmed_fields_untouched(ha):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from custom_components.wallbox_manager.ocpp import Adapter

    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, gen)
    for name, component, key, valid, expected in [
        (
            "ChargingEnabled",
            {"name": "WallboxController"},
            ("control", state.binding, "charging_enabled"),
            "true",
            True,
        ),
        (
            "ControlAuthority",
            {"name": "WallboxController"},
            ("station", ("Wallbox01", None, None), "control_authority"),
            "OCPP",
            "remote",
        ),
        (
            "PhaseRotation",
            {"name": "Connector", "evse": {"id": 1, "connector_id": 1}},
            ("station", ("Wallbox01", None, None), "physical_phases"),
            "RST",
            3,
        ),
    ]:
        endpoint = {"component": component, "variable": {"name": name}}
        row = {
            **endpoint,
            "attribute_type": "Actual",
            "attribute_status": "Accepted",
            "attribute_value": valid,
        }
        failures = [
            None,
            {},
            [],
            [None],
            [row, row],
            [{**row, "attribute_status": "Rejected"}],
            [{**row, "attribute_status": "UnknownVariable"}],
            [{**row, "attribute_value": ""}],
            [{**row, "attribute_value": None}],
            [{**row, "attribute_value": {}}],
            [{**row, "attribute_value": "unsupported"}],
            [{k: v for k, v in row.items() if k != "attribute_value"}],
            [{**row, "attribute_type": "Target"}],
            [{**row, "component": {"name": "Other"}}],
            [{**row, "variable": {"name": "Other"}}],
        ]
        for initialized in (False, True):
            if initialized:
                adapter.request = AsyncMock(
                    return_value=SimpleNamespace(get_variable_result=[row])
                )
                await adapter.read(gen, endpoint)
                assert state.value(key) == expected
            previous = state.observations.get(key)
            for result in failures:
                adapter.request = AsyncMock(
                    return_value=SimpleNamespace(get_variable_result=result)
                )
                await adapter.read(gen, endpoint)
                assert state.observations.get(key) is previous
                assert state.value(key) == (expected if initialized else None)
            adapter.request = AsyncMock(side_effect=TimeoutError)
            with pytest.raises(TimeoutError):
                await adapter.read(gen, endpoint)
            assert state.observations.get(key) is previous
            if not initialized:
                assert key not in state.observations and key[2] in state.missing


async def test_identical_permission_readbacks_do_not_publish_or_change_attributes(
    ha, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from homeassistant.core import callback
    from homeassistant.helpers.event import async_track_state_report_event

    from custom_components.wallbox_manager.ocpp import Adapter

    entry = await ha.install()
    state = entry.runtime_data.state
    gen = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, gen)
    endpoint = {
        "component": {"name": "WallboxController"},
        "variable": {"name": "ChargingEnabled"},
    }
    row = {**endpoint, "attribute_status": "Accepted", "attribute_value": "true"}
    adapter.request = AsyncMock(return_value=SimpleNamespace(get_variable_result=[row]))
    await adapter.read(gen, endpoint)
    permission = next(e for e in entities(ha, entry) if e.role == "charging_enabled")
    first = state.observations[permission.key]
    published = ha.states.get(permission.entity_id)
    attrs = permission.extra_state_attributes
    writes = Mock(wraps=permission.async_write_ha_state)
    monkeypatch.setattr(permission, "async_write_ha_state", writes)
    changes, reports = [], []

    @callback
    def changed(event):
        if event.data["entity_id"] == permission.entity_id:
            changes.append(event)

    @callback
    def reported(event):
        reports.append(event)

    stop = ha.bus.async_listen("state_changed", changed)
    stop_reports = async_track_state_report_event(ha, [permission.entity_id], reported)
    for _ in range(2000):
        await adapter.read(gen, endpoint)
    await ha.async_block_till_done()
    assert state.observations[permission.key].observed_at > first.observed_at
    assert state.observations[permission.key].received_at > first.received_at
    assert permission.extra_state_attributes == attrs
    assert ha.states.get(permission.entity_id) is published
    assert not writes.called and not changes and not reports
    assert attrs["valid_until"] is attrs["enabled_valid_until"] is None
    assert not {"observed_at", "received_at", "enabled_observed_at"} & attrs.keys()
    # Readback changes and explicit unknown events remain immediately visible.
    for value, expected in [("false", False), ("true", True)]:
        row["attribute_value"] = value
        await adapter.read(gen, endpoint)
        assert permission.is_on is expected
        assert ha.states.get(permission.entity_id).state == (
            "on" if expected else "off"
        )
    adapter.variable(
        gen,
        endpoint["component"],
        endpoint["variable"],
        "",
        datetime.now(UTC),
        "NotifyEvent",
    )
    assert permission.is_on is None
    assert ha.states.get(permission.entity_id).state == "unknown"
    stop()
    stop_reports()
