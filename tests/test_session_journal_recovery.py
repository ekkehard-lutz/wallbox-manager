"""Passive session recovery against HA's real Store and synthetic peers."""

# ruff: noqa: ASYNC240 -- synchronous access to small local fixture files
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import config
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.storage import Store
from ocpp.exceptions import InternalError
from test_active_control import ready
from test_grid_profile import GridStation
from test_protocol import peer
from test_pv_runtime import sources, tick
from test_pv_transaction_rollover import report

from custom_components.wallbox_manager import async_unload_entry, session
from custom_components.wallbox_manager.ocpp import Listener
from custom_components.wallbox_manager.store import State

SCOPE = ("Wallbox01", "1", "1")
EMPTY = {"schema": 2, "session": None, "closed_transactions": []}


def journal(ha, entry):
    return Store(ha, 1, f"wallbox_manager.{entry.entry_id}.passive_session")


def archives(path):
    return sorted(path.parent.glob(f"{path.name}.corrupt.*"))


def seed_record(*, completed=False):
    state = State(SCOPE)
    generation = state.reset(connected=True)
    at = datetime.now(UTC)
    assert session.event(state, generation, SCOPE, "saved", "Started", at, 0)
    if completed:
        assert session.event(state, generation, SCOPE, "saved", "Ended", at, 1)
    return session.document(state)["session"]


async def test_first_setup_without_journal_needs_no_recovery(ha, caplog):
    entry = await ha.install()
    runtime = entry.runtime_data
    path = Path(runtime.session_store.path)
    assert session.document(runtime.state) == EMPTY
    assert runtime.settings.get("charge_release") is False
    assert not path.exists() and not archives(path)
    assert not caplog.records


@pytest.mark.parametrize("historical_generation", [False, True])
async def test_valid_journal_takes_precedence_over_old_quarantine(
    ha, caplog, historical_generation
):
    entry = config()
    store = journal(ha, entry)
    path = Path(store.path)
    record = seed_record()
    if historical_generation:
        record["generation"] = [1, 0]
    saved = EMPTY | {"session": record, "closed_transactions": ["z", "a"]}
    await store.async_save(saved)
    original = path.read_bytes()
    artifact = path.with_name(f"{path.name}.corrupt.previous")
    artifact.write_bytes(b"prior artifact")
    state = State(SCOPE)
    await session.load(ha, entry.entry_id, state)
    assert state.session == record | {"generation": None}
    assert state.closed_transactions == {"z", "a"}
    assert session.view(state, datetime.now(UTC))["active"] is None
    assert "generation" not in session.document(state)["session"]
    assert path.read_bytes() == original
    assert archives(path) == [artifact]
    assert "Recovered passive session journal" not in caplog.text


@pytest.mark.parametrize("closed", [None, ["preserved", "", " ", True, 8, [], {}]])
@pytest.mark.parametrize("missing_sequence", [False, True])
async def test_partial_recovery_keeps_only_trustworthy_history(
    ha, caplog, closed, missing_sequence
):
    entry = config()
    store = journal(ha, entry)
    path = Path(store.path)
    record = seed_record(completed=True)
    for key in ("start_meter", "current_meter", "end_meter", "energy"):
        record.pop(key)
    if missing_sequence:
        record.pop("sequence")
    saved = {"schema": 2, "session": record}
    if closed is not None:
        saved["closed_transactions"] = closed
    await store.async_save(saved)
    original = path.read_bytes()
    state = State(SCOPE)
    await session.load(ha, entry.entry_id, state)
    expected = {"saved", "preserved"} if closed is not None else {"saved"}
    assert state.closed_transactions == expected
    if missing_sequence:
        assert state.session is None
    else:
        assert state.session["session_id"] == record["session_id"]
        view = session.view(state, datetime.now(UTC))
        assert view["active"] is False
        assert all(view[key] is None for key in ("start_meter", "end_meter", "energy"))
    assert "missing or invalid closed transaction history" in caplog.text
    assert "replay protection may be incomplete" in caplog.text
    assert str(entry.entry_id) in caplog.text
    [artifact] = archives(path)
    assert artifact.read_bytes() == original
    normalized = session.document(state)
    assert await store.async_load() == normalized

    caplog.clear()
    restarted = State(SCOPE)
    await session.load(ha, entry.entry_id, restarted)
    assert session.document(restarted) == normalized
    assert not caplog.records
    assert archives(path) == [artifact]


async def test_missing_record_preserves_closed_ids_and_original_wrapper(ha):
    entry = config()
    store = journal(ha, entry)
    mismatched_minor = Store(ha, 1, store.key, minor_version=2)
    await mismatched_minor.async_save({"schema": 2, "closed_transactions": ["keep"]})
    path = Path(store.path)
    original = path.read_bytes()
    state = State(SCOPE)
    await session.load(ha, entry.entry_id, state)
    assert await store.async_load() == EMPTY | {"closed_transactions": ["keep"]}
    [artifact] = archives(path)
    assert artifact.read_bytes() == original


async def test_historical_meter_generation_cannot_supply_a_new_end_reading(ha):
    entry = config()
    store = journal(ha, entry)
    record = seed_record()
    future = datetime.now(UTC) + timedelta(seconds=1)
    record.update(
        current_meter=12,
        energy_generation=[1, 0],
        energy_at=future.isoformat(),
    )
    await store.async_save(EMPTY | {"session": record})
    state = State(SCOPE)
    await session.load(ha, entry.entry_id, state)
    generation = state.reset(connected=True)
    assert list(generation) == record["energy_generation"]
    assert session.event(state, generation, SCOPE, "saved", "Ended", future, 1)
    assert state.session["end_meter"] is None
    assert state.session["current_meter"] == 12


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": ["Wallbox01", "1", ["invalid"]]},
        {"scope": ["AnotherStation", "1", "1"]},
        {"external_transaction_id": True},
        {"session_id": None},
        {"sequence": -1},
        {"sequence": True},
        {"updated_at": "2026-11-01T10:00:00"},
        {"started_at": "9999-11-01T10:00:00+00:00"},
        {"ended_at": "2020-11-01T10:00:00+00:00"},
        {"end_meter": 12.3},
        {"external_transaction_id": "retired"},
    ],
)
async def test_inconsistent_session_does_not_create_active_history(ha, changes):
    entry = config()
    store = journal(ha, entry)
    await store.async_save(
        EMPTY | {"session": seed_record() | changes, "closed_transactions": ["retired"]}
    )
    state = State(SCOPE)
    await session.load(ha, entry.entry_id, state)
    assert session.document(state) == EMPTY | {"closed_transactions": ["retired"]}


@pytest.mark.parametrize(
    "raw",
    [
        b'{"version":1,"data":',
        b"{}",
        b"[]",
        b'{"version":1,"data":null}',
        b'{"version":2,"data":{"schema":2}}',
    ],
)
async def test_damaged_journal_is_preserved_and_reset_before_listener(
    ha, caplog, monkeypatch, raw
):
    entry = config()
    store = journal(ha, entry)
    path = Path(store.path)
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(raw)
    start = Listener.start

    async def start_after_recovery(listener):
        [artifact] = archives(path)
        assert artifact.read_bytes() == raw
        assert await store.async_load() == EMPTY
        await start(listener)

    monkeypatch.setattr(Listener, "start", start_after_recovery)
    await ha.install(entry)
    [artifact] = archives(path)
    assert artifact.read_bytes() == raw
    assert str(artifact) in caplog.text
    assert await store.async_load() == EMPTY
    assert entry.runtime_data.server.port
    assert entry.runtime_data.settings.get("charge_release") is False


async def test_already_quarantined_bytes_are_left_in_place(ha, caplog):
    entry = config()
    store = journal(ha, entry)
    path = Path(store.path)
    path.parent.mkdir(exist_ok=True)
    raw = b'{"version":1,"data":'
    path.write_bytes(raw)
    # Exercise the pinned HA Store's actual quarantine path first.
    assert await store.async_load() is None
    [artifact] = archives(path)
    caplog.clear()
    await ha.install(entry)
    assert "missing after quarantine" in caplog.text
    assert str(artifact) in caplog.text
    assert await store.async_load() == EMPTY
    assert archives(path) == [artifact]
    assert artifact.read_bytes() == raw
    await async_unload_entry(ha, entry)
    caplog.clear()
    await ha.install(entry)
    assert "Recovered passive session journal" not in caplog.text
    assert archives(path) == [artifact]


@pytest.mark.parametrize("action", ["read", "archive", "write and verify"])
async def test_failed_recovery_preserves_source_and_prevents_startup(
    ha, monkeypatch, caplog, action
):
    entry = config()
    store = journal(ha, entry)
    path = Path(store.path)
    saved = {"schema": 2, "session": seed_record()}
    await store.async_save(saved)
    original = path.read_bytes()

    def deny(*_args, **_kwargs):
        raise PermissionError("injected filesystem failure")

    with monkeypatch.context() as scoped:
        if action == "read":
            read_bytes = Path.read_bytes

            def read(file):
                return deny() if file == path else read_bytes(file)

            scoped.setattr(Path, "read_bytes", read)
        elif action == "archive":
            scoped.setattr(session.os, "link", deny)
        else:
            # Home Assistant catches this write failure and returns normally.
            replace = session.os.replace

            def fail_recovery_save(src, dest):
                return deny() if Path(dest) == path else replace(src, dest)

            scoped.setattr(session.os, "replace", fail_recovery_save)
        with pytest.raises(ConfigEntryError, match=f"Cannot {action}"):
            await ha.install(entry)

    assert not hasattr(entry, "runtime_data")
    assert path.read_bytes() == original
    assert not any(record.levelno == logging.WARNING for record in caplog.records)
    assert "Recovery did not complete" in caplog.text
    if action == "write and verify":
        [artifact] = archives(path)
        assert artifact.read_bytes() == original
        await ha.install(entry)
        assert archives(path) == [artifact]
        assert (await store.async_load())["session"] == saved["session"]
    else:
        assert not archives(path)


@pytest.mark.parametrize("profile", ["NETZ", "PV_SURPLUS"])
async def test_recovery_preserves_transaction_and_control_fences(ha, profile):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    ha.config_entries.async_update_entry(entry, options=sources(ha))
    await ha.install(entry)
    await entry.runtime_data.settings.set("charging_profile", profile)
    await async_unload_entry(ha, entry)
    store = journal(ha, entry)
    record = seed_record()
    record.pop("energy")
    await store.async_save(
        EMPTY | {"session": record, "closed_transactions": ["retired"]}
    )
    await ha.install(entry)
    runtime = entry.runtime_data
    state = runtime.state
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        assert runtime.grid.transaction() is None
        assert session.view(state, datetime.now(UTC))["active"] is None
        assert state.closed_transactions == {"retired"}
        await report(station, "retired", kind="Updated", seq=4)
        await report(station, "saved", kind="Updated", seq=0)
        await report(
            station,
            "saved",
            kind="Updated",
            seq=1,
            at=state.epoch - timedelta(milliseconds=1),
        )
        with pytest.raises(InternalError):
            await report(station, "saved", kind="Updated", seq=1, connector=2)
        assert runtime.grid.transaction() is None
        await report(station, "saved", kind="Updated", seq=1)
        assert state.session["session_id"] == record["session_id"]
        assert runtime.grid.transaction() == "saved"
        # An ID absent from the retained history can establish a new transaction.
        await report(station, "new")
        assert runtime.grid.transaction() == "new"
        assert state.closed_transactions == {"retired", "saved"}
        await tick(runtime.pv)
        assert runtime.settings.get("charge_release") is False
        assert not station.writes
        assert all(
            data["charging_schedule"][0]["charging_schedule_period"][0]["limit"] == 0
            for _, data in station.profiles
        )
    await async_unload_entry(ha, entry)
    await ha.install(entry)
    assert entry.runtime_data.settings.get("charge_release") is False
    assert entry.runtime_data.state.closed_transactions == {"retired", "saved"}
    assert entry.runtime_data.grid.transaction() is None
