"""Deterministic actual HA write/event counts; no inferred live CPU claim."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from conftest import config, entities
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_report_event


async def test_passive_workloads(ha, monkeypatch):
    one, two = await ha.install(), await ha.install(config("Other"))
    state = one.runtime_data.state
    generation = state.reset(connected=True)
    scope = ("Wallbox01", "1", None)
    quantities = (
        "voltage_l1",
        "voltage_l2",
        "voltage_l3",
        "current_l1",
        "current_l2",
        "current_l3",
        "power",
        "power_l1",
        "power_l2",
        "power_l3",
        "energy",
    )
    from custom_components.wallbox_manager.ocpp import READ_ONLY, Adapter

    commands = []
    original_send = Adapter._send

    async def record_send(adapter, message):
        wire = json.loads(message)
        if wire[0] == 2 and wire[2] not in READ_ONLY:
            commands.append(wire[2])
        await original_send(adapter, message)

    monkeypatch.setattr(Adapter, "_send", record_send)
    results = {}
    changes, reports = [], []

    @callback
    def changed(event):
        new = event.data.get("new_state")
        if new and new.attributes.get("wallbox_manager_entry") == one.entry_id:
            changes.append(event.data["entity_id"])

    remove = ha.bus.async_listen("state_changed", changed)

    async def measure(name, action):
        changes.clear()
        reports.clear()
        accepted, values = state.accepted, state.changed
        await action()
        await ha.async_block_till_done()
        unrelated = [
            entity
            for entity in changes + reports
            if entity.startswith(("number.", "select.", "text.", "button."))
        ]
        results[name] = {
            "accepted": state.accepted - accepted,
            "value_changes": state.changed - values,
            "ha_writes": len(changes) + len(reports),
            "ha_changes": len(changes),
            "state_reported": len(reports),
            "estimated_recorder_rows": len(changes),
            "unrelated_writes": len(unrelated),
            "control_commands": len(commands),
        }

    async def initial():
        for q in quantities:
            state.observe(
                generation,
                ("observation", scope, q),
                0
                if q.startswith("current")
                else 230
                if q.startswith("voltage")
                else 100,
                "initial",
            )

        for family, scope_, role, value in (
            ("observation", state.binding, "connector_state", "occupied"),
            ("observation", state.binding, "charging_state", "connected"),
            ("station", ("Wallbox01", None, None), "control_authority", "local"),
            ("station", ("Wallbox01", None, None), "physical_phases", 1),
            ("control", state.binding, "charging_enabled", True),
            ("capability", scope, "minimum_current", 6),
            ("capability", scope, "current_step", 1),
            ("capability", scope, "supported_phases", "1,3"),
            ("capability", scope, "phase_switching", True),
            ("capability", state.binding, "maximum_asymmetry_current", 16),
            ("capability", state.binding, "maximum_current", 16),
            ("capability", state.binding, "zero_current", True),
            ("capability", state.binding, "enable_disable", True),
            ("station", ("Wallbox01", None, None), "revision", 1),
            ("station", ("Wallbox01", None, None), "discovery", "verified"),
        ):
            state.observe(generation, (family, scope_, role), value, "initial")
        assert not state.missing

    await measure("initial_sync", initial)
    ids = [
        s.entity_id
        for s in ha.states.async_all()
        if s.attributes.get("wallbox_manager_entry") == one.entry_id
    ]

    @callback
    def reported(event):
        reports.append(event.data["entity_id"])

    stop_reports = async_track_state_report_event(ha, ids, reported)
    # All initialized fields are now settled. Missing optional bootstrap fields
    # are diagnostic, but normal reports do not change that list repeatedly.
    base = datetime.now(UTC)

    async def repeats():
        for i in range(2000):
            state.observe(
                generation,
                ("observation", scope, "power"),
                100,
                "initial",
                base + timedelta(microseconds=i + 1),
            )

    await measure("same_value_reports", repeats)
    assert results["same_value_reports"]["accepted"] == 2000
    assert results["same_value_reports"]["value_changes"] == 0
    assert results["same_value_reports"]["ha_writes"] == 0
    assert not changes and not reports
    latest = state.observations[("observation", scope, "power")]
    assert latest.observed_at == base + timedelta(microseconds=2000)
    assert latest.received_at >= base

    async def changing():
        for i in range(100):
            state.observe(
                generation,
                ("observation", scope, "power"),
                200 + i,
                "meter",
                base + timedelta(microseconds=3000 + i),
            )

    await measure("changed_values", changing)
    assert results["changed_values"]["ha_writes"] == 100

    adapter = Adapter(Mock(), one.runtime_data.server, generation)

    def meter_voltage(raw, index):
        adapter.meters(
            1,
            [
                {
                    "timestamp": (base + timedelta(microseconds=index)).isoformat(),
                    "sampled_value": [
                        {
                            "measurand": "Voltage",
                            "phase": "L1",
                            "value": raw,
                            "unit_of_measure": {"unit": "V"},
                        }
                    ],
                }
            ],
        )

    meter_voltage(230, 5000)
    await ha.async_block_till_done()  # Settle source/unit provenance outside workload.

    async def fluctuations():
        for i in range(2000):
            meter_voltage(230 + (i + 1) * 0.00000001, 6000 + i)

    await measure("normalized_voltage_reports", fluctuations)
    assert results["normalized_voltage_reports"]["ha_writes"] == 0
    assert results["normalized_voltage_reports"]["accepted"] == 2000
    assert state.observations[("observation", scope, "voltage_l1")].observed_at == (
        base + timedelta(microseconds=7999)
    )

    async def boundary():
        meter_voltage(230.05, 8000)

    await measure("normalized_voltage_boundary", boundary)
    assert results["normalized_voltage_boundary"]["ha_changes"] == 1

    def meter_energy(raw, index):
        adapter.meters(
            1,
            [
                {
                    "timestamp": (base + timedelta(microseconds=index)).isoformat(),
                    "sampled_value": [
                        {"value": raw, "unit_of_measure": {"unit": "kWh"}}
                    ],
                }
            ],
        )

    meter_energy(100, 8001)
    await ha.async_block_till_done()

    async def energy():
        for i in range(1000):
            meter_energy(100 + (i + 1) * 0.00000001, 9000 + i)

    await measure("exact_energy_changes", energy)
    assert results["exact_energy_changes"]["ha_changes"] == 1000

    # Alternative native HA semantics still execute a full HA write per report.
    reading = next(
        e for e in entities(ha, one) if e.key == ("observation", scope, "power")
    )

    async def native_reports():
        for _ in range(2000):
            reading.async_write_ha_state()

    await measure("native_unchanged_writes", native_reports)
    assert results["native_unchanged_writes"]["ha_changes"] == 0
    assert results["native_unchanged_writes"]["state_reported"] == 2000
    results["same_value_reports"]["timestamp_attributes"] = [
        key
        for key in ("observed_at", "received_at")
        if key in ha.states.get(reading.entity_id).attributes
    ]

    async def silent():
        # No periodic scanner or metering timer exists; time passage alone does
        # not change ordinary source values or schedule any policy work.
        assert state.value(("observation", scope, "voltage_l1")) == 230.1

    await measure("change_only_idle", silent)
    assert results["change_only_idle"]["ha_writes"] == 0

    async def isolated():
        second = two.runtime_data.state
        gen = second.reset(connected=True)
        for i in range(100):
            second.observe(
                gen, ("observation", ("Other", "1", None), "power"), i, "other"
            )

    await measure("other_instance", isolated)
    assert results["other_instance"]["ha_writes"] == 0

    async def reconnect():
        nonlocal generation
        state.reset(connected=False)
        generation = state.reset(connected=True)
        assert state.value(("observation", scope, "power")) is None
        await initial()

    await measure("disconnect_reconnect", reconnect)
    assert results["disconnect_reconnect"]["accepted"] == 26
    assert all(
        row["unrelated_writes"] == 0
        for name, row in results.items()
        if name
        not in (
            "initial_sync",
            "disconnect_reconnect",
        )
    )
    remove()
    stop_reports()
    print("PASSIVE_BENCHMARK " + json.dumps(results, sort_keys=True))
