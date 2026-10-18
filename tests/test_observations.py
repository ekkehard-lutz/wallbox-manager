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


async def test_permission_expiry_is_one_timer_and_not_measurement_expiry(ha):
    import asyncio

    entry = await ha.install()
    state = entry.runtime_data.state
    generation = state.reset(connected=True)
    power = ("observation", ("Wallbox01", "1", None), "power")
    state.observe(generation, power, 1234, "meter")
    permission = ("control", state.binding, "charging_enabled")
    now = datetime.now(UTC)
    state.observe(
        generation,
        permission,
        True,
        "query",
        now,
        valid_until=now + timedelta(seconds=10),
    )
    entity = next(e for e in entities(ha, entry) if e.role == "charging_enabled")
    old = entity._expiry
    now = datetime.now(UTC)
    state.observe(
        generation,
        permission,
        True,
        "query",
        now,
        valid_until=now + timedelta(milliseconds=30),
    )
    assert entity._expiry is not old
    await asyncio.sleep(0.05)
    await ha.async_block_till_done()
    assert entity._expiry is None
    assert ha.states.get(entity.entity_id).state == "unknown"
    assert state.value(power) == 1234
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


async def test_unsuccessful_permission_readback_never_refreshes_confirmation(
    ha, monkeypatch
):
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
    )  # Original deadline, not renewed.
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
    assert state.value(permission) is None  # Explicit unsuccessful read revokes now.
    assert state.observations[permission].invalid_count == 0
