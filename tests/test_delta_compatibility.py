"""Delta-report compatibility through the existing adapter, store and sessions."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from conftest import entities, eventually
from ocpp.exceptions import OCPPError
from ocpp.v21 import call
from test_protocol import READ_ONLY, boot, peer

from custom_components.wallbox_manager import session
from custom_components.wallbox_manager.ocpp import Adapter


def phase_event(value="", trigger="Delta", **overrides):
    return {
        "event_id": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "trigger": trigger,
        "actual_value": value,
        "event_notification_type": "HardWiredNotification",
        "component": {"name": "Connector", "evse": {"id": 1, "connector_id": 1}},
        "variable": {"name": "PhaseRotation"},
        **overrides,
    }


@pytest.mark.parametrize(
    ("overrides", "count"),
    [
        ({}, 2),
        ({"trigger": "Periodic"}, 1),
        ({"trigger": "Alerting"}, 1),
        ({"trigger": None}, 1),
        ({"actual_value": "unknown"}, 1),
        ({"actual_value": None}, 1),
        ({"event_notification_type": "CustomMonitor"}, 0),
        ({"variable": {"name": "PhaseRotation", "instance": "other"}}, 1),
        (
            {
                "component": {
                    "name": "Connector",
                    "instance": "other",
                    "evse": {"id": 1, "connector_id": 1},
                }
            },
            1,
        ),
        ({"component": {"name": "Connector", "evse": {"id": 1}}}, 0),
        (
            {
                "component": {
                    "name": "Connector",
                    "evse": {"id": 1, "connector_id": 1},
                    "custom_data": {"vendor_id": "example"},
                },
                "variable": {
                    "name": "PhaseRotation",
                    "custom_data": {"vendor_id": "example"},
                },
            },
            2,
        ),
    ],
)
async def test_only_exact_delta_unknown_is_authoritative(ha, overrides, count):
    entry = await ha.install()
    state = entry.runtime_data.state
    generation = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, generation)
    key = ("station", ("Wallbox01", None, None), "physical_phases")
    state.observe(generation, key, 3, "initial")
    event = phase_event(**overrides)
    if overrides.get("actual_value", "") is None:
        event.pop("actual_value")  # Missing is distinct from explicitly empty.
    adapter.notify_event([event])
    assert state.observations[key].invalid_count == count
    assert state.value(key) == (None if count == 2 else 3)


@pytest.mark.parametrize("recovery", ["Rxx", "RST"])
@pytest.mark.parametrize("boot_generation", [False, True])
async def test_delta_phase_unknown_replay_generation_and_recovery(
    ha, recovery, boot_generation
):
    entry = await ha.install()
    state = entry.runtime_data.state
    generation = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, generation)
    key = ("station", ("Wallbox01", None, None), "physical_phases")
    phase = next(e for e in entities(ha, entry) if e.key == key)
    state.observe(generation, key, 3, "initial")
    event = phase_event()
    adapter.notify_event([event])
    unknown = state.observations[key]
    assert not phase.available
    assert unknown.invalid_count == 2
    adapter.notify_event([event])
    assert state.observations[key] is unknown
    # An equal-time valid report cannot turn an invalid transition into recovery.
    adapter.notify_event([{**event, "actual_value": recovery}])
    assert state.observations[key] is unknown
    adapter.notify_event([phase_event(value=recovery)])
    assert phase.available
    assert state.value(key) == {"Rxx": 1, "RST": 3}[recovery]
    valid = state.observations[key]
    adapter.notify_event([event])  # Older unknown cannot withdraw newer evidence.
    assert state.observations[key] is valid
    newer = state.reset(connected=True, boot=boot_generation)
    adapter.notify_event([phase_event()])  # Previous adapter/generation is fenced.
    assert state.observations[key] is valid
    adapter.generation = newer
    adapter.notify_event([event])  # Previous source epoch is also fenced.
    assert state.observations[key] is valid
    adapter.notify_event([phase_event()])
    assert state.value(key) is None
    assert state.observations[key].invalid_count == 2
    adapter.notify_event([phase_event(value=recovery)])
    assert phase.available and state.observations[key].invalid_count == 0


async def test_same_time_conflict_keeps_legacy_single_invalid_semantics(ha):
    entry = await ha.install()
    state = entry.runtime_data.state
    generation = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, generation)
    key = ("station", ("Wallbox01", None, None), "physical_phases")
    event = phase_event(value="RST")
    adapter.notify_event([event])
    adapter.notify_event([{**event, "actual_value": ""}])
    assert state.value(key) == 3
    assert state.observations[key].invalid_count == 1
    # A later periodic unknown is still the second distinct invalid report.
    adapter.notify_event([phase_event(trigger="Periodic")])
    assert state.value(key) is None


async def test_phase_delta_empty_value_passes_real_ocpp21_schema(ha):
    entry = await ha.install()
    state = entry.runtime_data.state
    async with peer(entry) as station:
        await boot(station)
        key = ("station", ("Wallbox01", None, None), "physical_phases")
        await eventually(lambda: state.value(key) == 1)
        event = phase_event()
        await station.call(
            call.NotifyEvent(
                generated_at=event["timestamp"], seq_no=0, event_data=[event]
            )
        )
        assert state.value(key) is None
        assert state.observations[key].invalid_count == 2
        missing = {k: v for k, v in event.items() if k != "actual_value"}
        with pytest.raises(OCPPError):
            await station.call(
                call.NotifyEvent(
                    generated_at=event["timestamp"], seq_no=1, event_data=[missing]
                )
            )
        assert set(station.operations) <= READ_ONLY


async def test_idle_initialization_is_contextual_without_synthetic_observations(ha):
    entry = await ha.install()
    state, listener = entry.runtime_data.state, entry.runtime_data.server
    generation = state.reset(connected=True)
    listener._initialization = "missing"
    adapter = Adapter(Mock(), listener, generation)
    # Supply all other supported fields through their real adapter paths.
    for name, value, component in (
        ("ControlAuthority", "Local", {"name": "WallboxController"}),
        ("ChargingEnabled", "false", {"name": "WallboxController"}),
        ("PhaseRotation", "Rxx", phase_event()["component"]),
    ):
        adapter.variable(
            generation,
            component,
            {"name": name},
            value,
            datetime.now(UTC),
            "GetVariables",
        )
    adapter.meters(
        1,
        [
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "sampled_value": [
                    {
                        "value": value,
                        "measurand": measurand,
                        "phase": phase,
                        "unit_of_measure": {"unit": unit},
                    }
                    for measurand, unit, value in (
                        ("Voltage", "V", 230),
                        ("Current.Import", "A", 0),
                    )
                    for phase in ("L1", "L2", "L3")
                ]
                + [
                    {
                        "value": 0,
                        "measurand": "Power.Active.Import",
                        "unit_of_measure": {"unit": "W"},
                    },
                    {"value": 1000, "unit_of_measure": {"unit": "Wh"}},
                ],
            }
        ],
    )
    adapter.status(1, 1, "Available", datetime.now(UTC).isoformat())
    assert listener.initialization["status"] == "ready"
    assert listener.initialization["missing"] == []
    assert "charging_state" in state.missing  # Evidence remains genuinely absent.
    assert state.value(("observation", state.binding, "charging_state")) is None
    assert state.session is None
    published = Mock()
    state.watch(("station", ("Wallbox01", None, None), "discovery"), published)
    adapter.status(1, 1, "Occupied", datetime.now(UTC).isoformat())
    assert listener.initialization["missing"] == ["charging_state"]
    published.assert_called_once()
    adapter.status(1, 1, "Available", datetime.now(UTC).isoformat())
    assert listener.initialization["status"] == "ready"
    # An active current-generation transaction must not receive the idle exception.
    adapter.transaction(
        "Started",
        datetime.now(UTC).isoformat(),
        "EVDetected",
        0,
        {"transaction_id": "real"},
        evse={"id": 1, "connector_id": 1},
    )
    assert listener.initialization["missing"] == ["charging_state"]
    count = published.call_count
    adapter.transaction(
        "Updated",
        datetime.now(UTC).isoformat(),
        "MeterValuePeriodic",
        1,
        {"transaction_id": "real"},
        evse={"id": 1, "connector_id": 1},
    )
    assert published.call_count == count  # No repeated readiness publication.
    adapter.transaction(
        "Ended",
        datetime.now(UTC).isoformat(),
        "EVDeparted",
        2,
        {"transaction_id": "real"},
        evse={"id": 1, "connector_id": 1},
    )
    assert listener.initialization["status"] == "ready"
    state.reset(connected=True, boot=True)
    assert "charging_state" in listener.initialization["missing"]
    assert "connector_state" in listener.initialization["missing"]


async def test_changed_transaction_energy_needs_no_periodic_refresh(ha):
    entry = await ha.install()
    state = entry.runtime_data.state
    generation = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, generation)

    def transaction(kind, sequence, energy=None, reason="MeterValuePeriodic"):
        at = datetime.now(UTC).isoformat()
        groups = (
            None
            if energy is None
            else [
                {
                    "timestamp": at,
                    "sampled_value": [
                        {
                            "value": energy,
                            "measurand": "Energy.Active.Import.Register",
                            "unit_of_measure": {"unit": "Wh"},
                            "context": "Transaction.Begin"
                            if kind == "Started"
                            else "Transaction.End"
                            if kind == "Ended"
                            else "Sample.Periodic",
                        }
                    ],
                }
            ]
        )
        adapter.transaction(
            kind,
            at,
            reason,
            sequence,
            {"transaction_id": "delta-session", "charging_state": "EVConnected"},
            evse={"id": 1, "connector_id": 1},
            meter_value=groups,
        )

    transaction("Started", 0, 1000, "CablePluggedIn")
    identity = state.session["session_id"]
    transaction("Updated", 1, 1001)
    assert session.view(state, datetime.now(UTC))["energy"] == pytest.approx(0.001)
    transaction("Updated", 2)  # Omitted unchanged energy retains the last value.
    assert state.session["end_meter"] == 1.001
    assert not state.session["energy_invalid"]
    transaction("Updated", 3, 1001)  # A repeated value is harmless.
    # Lifetime energy must not enter the transaction-scoped stream.
    adapter.meters(
        1,
        [
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "sampled_value": [
                    {"value": 999999, "unit_of_measure": {"unit": "Wh"}},
                ],
            }
        ],
    )
    assert state.session["end_meter"] == 1.001
    assert state.value(("observation", ("Wallbox01", "1", None), "energy")) == 999.999
    # Reconnect/TxResumed retains the same session and its energy baseline.
    previous_generation = generation
    generation = state.reset(connected=True)
    adapter.generation = generation
    transaction("Updated", 4, reason="TxResumed")
    assert state.session["session_id"] == identity
    assert state.session["generation"] == list(generation)
    assert state.session["start_meter"] == 1
    transaction("Updated", 5, 1001.00001)
    assert state.session["end_meter"] == 1.00100001
    assert session.view(state, datetime.now(UTC))["energy"] == pytest.approx(0.00100001)
    before = deepcopy(state.session)
    transaction("Updated", 5, 2000)  # Replayed sequence cannot change session energy.
    assert state.session == before
    assert not session.event(
        state,
        previous_generation,
        state.binding,
        "delta-session",
        "Updated",
        datetime.now(UTC),
        100,
    )
    transaction("Ended", 6, 1002, "EVDeparted")
    assert state.session["session_id"] == identity
    assert state.session["sequence"] == 6
    assert state.session["energy"] == pytest.approx(0.002)
    assert session.view(state, datetime.now(UTC))["active"] is False
    transaction("Updated", 7, 5000)
    assert state.session["end_meter"] == 1.002


async def test_permission_delta_events_coexist_with_existing_read_only_poll(ha):
    entry = await ha.install()
    state, listener = entry.runtime_data.state, entry.runtime_data.server
    key = ("control", state.binding, "charging_enabled")
    async with peer(entry) as station:
        await boot(station)
        await eventually(lambda: listener.adapter.poll is not None)
        assert state.value(key) is True
        event = phase_event(
            value="false",
            component={"name": "WallboxController"},
            variable={"name": "ChargingEnabled"},
        )
        await station.call(
            call.NotifyEvent(
                generated_at=event["timestamp"],
                seq_no=0,
                event_data=[event],
            )
        )
        assert state.value(key) is False
        # No replacement subscriber/task: the original five-second poll is still live.
        poll = listener.adapter.poll
        await asyncio.sleep(5.1)
        await eventually(lambda: station.reads.count("ChargingEnabled") >= 2)
        assert state.value(key) is True
        assert listener.adapter.poll is poll and not poll.done()
        assert set(station.operations) <= READ_ONLY


async def test_transaction_energy_cannot_replace_a_lifetime_session_stream(ha):
    entry = await ha.install()
    state = entry.runtime_data.state
    generation = state.reset(connected=True)
    adapter = Adapter(Mock(), entry.runtime_data.server, generation)
    session.event(
        state, generation, state.binding, "real", "Started", datetime.now(UTC), 0
    )
    at = datetime.now(UTC).isoformat()
    adapter.meters(
        1,
        [
            {
                "timestamp": at,
                "sampled_value": [{"value": 100000, "unit_of_measure": {"unit": "Wh"}}],
            }
        ],
    )
    assert state.session["end_meter"] == 100
    stream = deepcopy(state.session["energy_channel"])
    at = datetime.now(UTC).isoformat()
    adapter.transaction(
        "Updated",
        at,
        "MeterValuePeriodic",
        1,
        {"transaction_id": "real"},
        evse={"id": 1, "connector_id": 1},
        meter_value=[
            {
                "timestamp": at,
                "sampled_value": [{"value": 1000, "unit_of_measure": {"unit": "Wh"}}],
            }
        ],
    )
    assert state.session["energy_channel"] == stream
    assert state.session["end_meter"] == 100
    assert state.session["start_meter"] is None
    assert state.session["energy"] is None
    assert not state.session["energy_invalid"]
