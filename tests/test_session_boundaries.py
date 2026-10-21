"""SDM630 boundaries, conservative display and persisted session semantics."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from custom_components.wallbox_manager import session
from custom_components.wallbox_manager.ocpp import Adapter, Listener
from custom_components.wallbox_manager.sensor import Reading
from custom_components.wallbox_manager.store import State


def setup():
    state = State(("Wallbox01", "1", "1"))
    generation = state.reset(connected=True)
    state.epoch -= timedelta(seconds=60)
    listener = Listener(state, "127.0.0.1", 0)
    adapter = Adapter(Mock(), listener, generation)
    return state, adapter


def energy(at, value):
    return [
        {
            "timestamp": at.isoformat(),
            "sampled_value": [{"value": value, "unit_of_measure": {"unit": "Wh"}}],
        }
    ]


def transaction(adapter, kind, at, seq, value=None, measured=None, tx="a"):
    adapter.transaction(
        kind,
        at.isoformat(),
        "MeterValuePeriodic",
        seq,
        {"transaction_id": tx},
        evse={"id": 1, "connector_id": 1},
        meter_value=energy(measured or at, value) if value is not None else None,
    )


def test_available_registration_projects_idle_without_observation():
    state, adapter = setup()
    adapter.boot(
        {"vendor_name": "wallbox-stationary", "model": "wallbox-stationary"}, "PowerUp"
    )
    at = datetime.now(UTC)
    adapter.status(1, 1, "Available", at.isoformat())
    entry = Mock(entry_id="entry")
    entry.runtime_data.state = state
    reading = Reading(entry, "observation", state.binding, "charging_state")
    assert reading.available
    assert reading.native_value == "idle"
    assert reading.key not in state.observations


def test_start_between_polls_uses_original_acquisition_time():
    state, adapter = setup()
    at = datetime.now(UTC) - timedelta(seconds=10)
    measured = at - timedelta(seconds=5.5)
    transaction(adapter, "Started", at, 0, 100000, measured)
    assert state.session["start_meter"] == 100
    assert state.session["end_meter"] is None
    assert state.session["started_at"] == at.isoformat()
    assert state.session["energy_at"] == measured.isoformat()


def test_live_counter_is_not_final_counter():
    state, adapter = setup()
    at = datetime.now(UTC) - timedelta(seconds=10)
    transaction(adapter, "Started", at, 0, 100000)
    transaction(adapter, "Updated", at + timedelta(seconds=5), 1, 100001)
    assert state.session["end_meter"] is None
    assert state.session["current_meter"] == 100.001
    assert state.session["energy"] == pytest.approx(0.001)


@pytest.mark.parametrize("age", [0, 5.5, 6, 15])
def test_end_between_polls_and_completed_values_survive_reconnect(age):
    state, adapter = setup()
    start = datetime.now(UTC) - timedelta(seconds=30)
    end = start + timedelta(seconds=25)
    transaction(adapter, "Started", start, 0, 100000, start - timedelta(seconds=5))
    transaction(adapter, "Ended", end, 1, 100123, end - timedelta(seconds=age))
    expected = session.view(state, end)
    assert expected["start_meter"] == 100
    assert expected["end_meter"] == 100.123
    assert expected["energy"] == pytest.approx(0.123)
    assert state.session["end_meter_at"] == (end - timedelta(seconds=age)).isoformat()
    state.reset(connected=False)
    state.reset(connected=True)
    adapter.generation = state.generation
    adapter.boot(
        {"vendor_name": "wallbox-stationary", "model": "wallbox-stationary"}, "PowerUp"
    )
    assert session.view(state, end) == expected


@pytest.mark.parametrize("age", [15.001, 30, -0.1])
def test_ineligible_boundary_remains_unknown(age):
    state, adapter = setup()
    start = datetime.now(UTC) - timedelta(seconds=30)
    end = start + timedelta(seconds=20)
    transaction(adapter, "Started", start, 0, 100000, start - timedelta(seconds=age))
    assert state.session["start_meter"] is None
    transaction(adapter, "Ended", end, 1, 100100, end - timedelta(seconds=age))
    assert state.session["end_meter"] is None
    assert state.session["energy"] is None


@pytest.mark.parametrize("bad", [-1, float("nan"), 99999])
def test_invalid_or_backwards_energy_never_becomes_final(bad):
    state, adapter = setup()
    start = datetime.now(UTC) - timedelta(seconds=20)
    transaction(adapter, "Started", start, 0, 100000)
    transaction(adapter, "Updated", start + timedelta(seconds=5), 1, bad)
    assert state.session["current_meter"] == 100
    assert state.session["energy"] is None
    transaction(adapter, "Ended", start + timedelta(seconds=10), 2, 100200)
    assert state.session["end_meter"] is None
    assert state.session["energy"] is None


def test_missing_start_is_not_reconstructed_and_replay_cannot_replace_session():
    state, adapter = setup()
    start = datetime.now(UTC) - timedelta(seconds=20)
    transaction(adapter, "Started", start, 0)
    transaction(adapter, "Updated", start + timedelta(seconds=5), 1, 100100)
    transaction(adapter, "Ended", start + timedelta(seconds=10), 2, 100200)
    assert state.session["start_meter"] is None
    assert state.session["end_meter"] == 100.2
    assert state.session["energy"] is None
    old_id = state.session["session_id"]
    transaction(adapter, "Started", start + timedelta(seconds=15), 0, 100300, tx="b")
    assert state.session["session_id"] != old_id
    assert state.session["end_meter"] is None
    assert state.session["energy"] == 0
    transaction(adapter, "Ended", start + timedelta(seconds=16), 3, 999999, tx="a")
    assert state.session["external_transaction_id"] == "b"
    assert state.session["current_meter"] == 100.3


@pytest.mark.parametrize(
    "condition",
    [
        "unregistered",
        "active",
        "unresolved",
        "unknown",
        "conflict",
        "disconnected",
        "real",
    ],
)
def test_idle_is_conservative_and_real_observations_win(condition):
    state, adapter = setup()
    state.registered_generation = state.generation
    at = datetime.now(UTC) - timedelta(seconds=2)
    adapter.status(1, 1, "Available", at.isoformat())
    if condition == "unregistered":
        state.registered_generation = None
    elif condition in ("active", "unresolved"):
        transaction(adapter, "Started", at + timedelta(seconds=1), 0)
        if condition == "unresolved":
            state.session["generation"] = None
    elif condition == "unknown":
        adapter.status(1, 1, "Unknown", (at + timedelta(seconds=1)).isoformat())
    elif condition == "conflict":
        adapter.status(1, 1, "Occupied", at.isoformat())
    elif condition == "disconnected":
        state.reset(connected=False)
    elif condition == "real":
        state.observe(
            state.generation,
            ("observation", state.binding, "charging_state"),
            "charging",
            "test",
            at,
        )
    assert state.charging_value(state.binding) != "idle"
    if condition == "real":
        assert state.charging_value(state.binding) == "charging"


def supported(state):
    state.registered_generation = state.generation
    state.identity = {
        "manufacturer": "wallbox-stationary",
        "model": "wallbox-stationary",
    }


def test_shared_energy_requires_identity_overlap_and_new_generation_proof():
    state, adapter = setup()
    supported(state)
    start = datetime.now(UTC) - timedelta(seconds=30)
    adapter.meters(1, energy(start, 100000))
    transaction(adapter, "Started", start, 0, 100000)
    adapter.meters(1, energy(start + timedelta(seconds=5), 100001))
    assert state.session["energy"] == pytest.approx(0.001)
    assert state.session["current_meter"] == 100.001
    assert state.session["end_meter"] is None
    # Older replay cannot change the live value.
    adapter.meters(1, energy(start, 999999))
    assert state.session["current_meter"] == 100.001
    generation = state.reset(connected=True)
    state.epoch = start + timedelta(seconds=10)
    adapter.generation = generation
    supported(state)
    transaction(adapter, "Updated", start + timedelta(seconds=15), 1)
    adapter.meters(1, energy(start + timedelta(seconds=16), 100010))
    assert state.session["current_meter"] == 100.001
    transaction(
        adapter,
        "Updated",
        start + timedelta(seconds=17),
        2,
        100010,
        start + timedelta(seconds=16),
    )
    adapter.meters(1, energy(start + timedelta(seconds=20), 100011))
    assert state.session["energy"] == pytest.approx(0.011)
    adapter.meters(1, energy(start + timedelta(seconds=21), -1))
    assert state.session["energy"] is None


@pytest.mark.parametrize(
    "proof", ["no_identity", "no_overlap", "other_model", "different_value"]
)
def test_arbitrary_energy_channels_are_not_linked(proof):
    state, adapter = setup()
    supported(state)
    start = datetime.now(UTC) - timedelta(seconds=20)
    if proof == "no_identity":
        state.registered_generation = None
    elif proof == "other_model":
        state.identity["model"] = "another"
    adapter.meters(1, energy(start, 200000 if proof == "different_value" else 100000))
    transaction(
        adapter,
        "Started",
        start,
        0,
        100000,
        start - timedelta(seconds=1) if proof == "no_overlap" else start,
    )
    adapter.meters(1, energy(start + timedelta(seconds=5), 200100))
    assert state.session["energy"] == 0
    assert state.session["end_meter"] is None


def test_latest_boundary_sample_is_selected_and_epoch_is_respected():
    state, adapter = setup()
    start = datetime.now(UTC) - timedelta(seconds=10)
    adapter.transaction(
        "Started",
        start.isoformat(),
        "CablePluggedIn",
        0,
        {"transaction_id": "a"},
        evse={"id": 1, "connector_id": 1},
        meter_value=energy(start - timedelta(seconds=10), 100000)
        + energy(start - timedelta(seconds=5), 100001),
    )
    assert state.session["start_meter"] == 100.001
    state, adapter = setup()
    state.epoch = start - timedelta(seconds=1)
    transaction(adapter, "Started", start, 0, 100000, start - timedelta(seconds=5))
    assert state.session["start_meter"] is None


async def test_completed_session_survives_reload_and_new_state_from_disk(ha):
    from conftest import entities

    from custom_components.wallbox_manager import async_unload_entry

    entry = await ha.install()
    state = entry.runtime_data.state
    state.reset(connected=True)
    state.epoch -= timedelta(seconds=60)
    adapter = Adapter(Mock(), entry.runtime_data.server, state.generation)
    start = datetime.now(UTC) - timedelta(seconds=20)
    transaction(adapter, "Started", start, 0, 100000, start - timedelta(seconds=5))
    transaction(
        adapter,
        "Ended",
        start + timedelta(seconds=15),
        1,
        100123,
        start + timedelta(seconds=10),
    )
    expected = session.view(state, datetime.now(UTC))
    await async_unload_entry(ha, entry)
    await ha.install(entry)
    restored = entry.runtime_data.state
    assert session.view(restored, datetime.now(UTC)) == expected
    # A fresh State loads the actual store, as after an HA process restart.
    from homeassistant.core import HomeAssistant

    restarted_ha = HomeAssistant(ha.config.config_dir)
    fresh = State(state.binding)
    await session.load(restarted_ha, entry.entry_id, fresh)
    await restarted_ha.async_stop()
    assert session.view(fresh, datetime.now(UTC)) == expected
    assert fresh.session["start_meter_at"] == state.session["start_meter_at"]
    assert fresh.session["end_meter_at"] == state.session["end_meter_at"]
    reading = next(e for e in entities(ha, entry) if e.role == "end_meter")
    assert reading.available and reading.native_value == 100.123
    assert reading.extra_state_attributes["boundary_approximation"] is True


@pytest.mark.parametrize("completed", [False, True])
async def test_schema_one_migration_keeps_id_and_history_without_inventing_data(
    ha, completed
):
    from homeassistant.helpers.storage import Store

    state, adapter = setup()
    start = datetime.now(UTC) - timedelta(seconds=20)
    transaction(adapter, "Started", start, 0, 100000)
    transaction(adapter, "Updated", start + timedelta(seconds=5), 1, 100100)
    if completed:
        transaction(adapter, "Ended", start + timedelta(seconds=10), 2, 100200)
    old = dict(state.session)
    old.pop("current_meter")
    if not completed:
        old["end_meter"] = 100.1  # beta.5's provisional endpoint
    old["start_meter"] = None  # Historical absence must survive migration.
    old["energy"] = None
    doc = {"session": old, "closed_transactions": ["historical"]}
    await Store(ha, 1, "wallbox_manager.migration.passive_session").async_save(doc)
    legacy = Store(ha, 1, "wallbox_manager.migration.sessions")
    await legacy.async_save({"records": [{"session_id": "untouched"}]})
    fresh = State(state.binding)
    await session.load(ha, "migration", fresh)
    assert fresh.session["session_id"] == old["session_id"]
    assert fresh.session["start_meter"] is None
    assert fresh.session["energy"] is None
    assert fresh.session["current_meter"] == old["end_meter"]
    assert fresh.session["end_meter"] == (old["end_meter"] if completed else None)
    assert "historical" in fresh.closed_transactions
    assert await legacy.async_load() == {"records": [{"session_id": "untouched"}]}
    assert session.document(fresh)["schema"] == 2


def test_projection_publishes_connector_and_session_changes():
    state, adapter = setup()
    supported(state)
    key = ("observation", state.binding, "charging_state")
    changed = Mock()
    state.watch(key, changed)
    at = datetime.now(UTC) - timedelta(seconds=3)
    adapter.status(1, 1, "Available", at.isoformat())
    assert changed.called and state.charging_value(state.binding) == "idle"
    changed.reset_mock()
    transaction(adapter, "Started", at + timedelta(seconds=1), 0)
    assert changed.called and state.charging_value(state.binding) is None
    changed.reset_mock()
    transaction(adapter, "Ended", at + timedelta(seconds=2), 1)
    assert changed.called and state.charging_value(state.binding) == "idle"


def test_transaction_first_overlap_links_only_matching_connector_and_evse():
    state, adapter = setup()
    supported(state)
    start = datetime.now(UTC) - timedelta(seconds=15)
    transaction(adapter, "Started", start, 0, 100000)
    adapter.meters(1, energy(start, 100000))
    adapter.meters(1, energy(start + timedelta(seconds=5), 100001))
    assert state.session["energy"] == pytest.approx(0.001)
    with pytest.raises(ValueError, match="Unexpected station connector"):
        adapter.meters(2, energy(start + timedelta(seconds=6), 200000))
    with pytest.raises(ValueError, match="Unexpected station connector"):
        adapter.transaction(
            "Updated",
            (start + timedelta(seconds=7)).isoformat(),
            "MeterValuePeriodic",
            1,
            {"transaction_id": "a"},
            evse={"id": 1, "connector_id": 2},
            meter_value=energy(start + timedelta(seconds=7), 300000),
        )
    assert state.session["energy"] == pytest.approx(0.001)
    assert state.session["current_meter"] == 100.001


def test_projection_attributes_use_current_generation_and_discovery_agrees():
    state, adapter = setup()
    key = ("observation", state.binding, "charging_state")
    state.observe(state.generation, key, "charging", "old")
    adapter.generation = state.reset(connected=True)
    adapter.boot(
        {"vendor_name": "wallbox-stationary", "model": "wallbox-stationary"}, "PowerUp"
    )
    adapter.status(1, 1, "Available", datetime.now(UTC).isoformat())
    entry = Mock(entry_id="entry")
    entry.runtime_data.state = state
    reading = Reading(entry, "observation", state.binding, "charging_state")
    attrs = reading.extra_state_attributes
    assert attrs["source"] == "wbm:registered_available"
    assert attrs["connection_generation"] == state.generation[0]
    assert attrs["boot_generation"] == state.generation[1]
    assert "charging_state" not in adapter.listener.initialization["missing"]
    state.reset(connected=False)
    assert not reading.available and reading.native_value is None
    assert "charging_state" in adapter.listener.initialization["missing"]
