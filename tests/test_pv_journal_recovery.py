"""PV startup recovery through Home Assistant Store and a simulated OCPP peer."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import config
from homeassistant.helpers.storage import Store
from ocpp.v21 import call
from test_grid_profile import GridStation
from test_protocol import peer
from test_pv_core import legacy_economics
from test_pv_runtime import power, release, sources, tick

from custom_components.wallbox_manager import async_setup_entry, async_unload_entry
from custom_components.wallbox_manager.pv_economics import Budget

BINDING = ["Wallbox01", "1", "1"]
SCOPE = [*BINDING, "remote", "net-test"]


def stored_budget(**fields):
    return Budget(scope=SCOPE, **fields).saved()


def journal_store(ha, entry, suffix="pv"):
    return Store(
        ha, 1, f"wallbox_manager.{entry.entry_id}.{suffix}", atomic_writes=True
    )


def entry_with_sources(ha):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    ha.config_entries.async_update_entry(entry, options=sources(ha, battery=False))
    return entry


def assert_unreconciled(budget):
    assert budget.scope == ["unreconciled"]
    assert budget.stopped and not budget.reset
    assert budget.previous is None and budget.low_since is None
    assert not budget.bind(SCOPE)


@pytest.mark.parametrize("record", [None, {}, [], False, "", "invalid", 0, 1])
def test_restore_rejects_non_records_and_empty_records(record):
    assert_unreconciled(Budget.restore(record))


@pytest.mark.parametrize(
    "key,value",
    [
        ("version", 2),
        ("version", True),
        ("version", 1.0),
        ("scope", []),
        ("scope", ["net-test"]),
        ("scope", [*BINDING, "local", "net-test"]),
        ("scope", [*BINDING, "remote", ""]),
        ("scope", [*BINDING, "remote", 1]),
        ("scope", "net-test"),
        ("wh", float("nan")),
        ("wh", float("inf")),
        ("wh", -1),
        ("wh", False),
        ("wh", 10**400),
        ("stopped", 0),
        ("stopped", "false"),
        ("reset", None),
        ("cause", []),
    ],
)
def test_restore_rejects_invalid_fields(key, value):
    assert_unreconciled(Budget.restore(stored_budget() | {key: value}))


@pytest.mark.parametrize("key", ["version", "scope", "wh", "stopped", "reset", "cause"])
def test_restore_requires_saved_fields(key):
    record = stored_budget()
    del record[key]
    assert_unreconciled(Budget.restore(record))


@pytest.mark.parametrize(
    "fields",
    [
        {"wh": 51},
        {"stopped": True},
        {"reset": True},
        {"cause": "import_budget"},
    ],
)
def test_unbound_record_cannot_claim_existing_economic_history(fields):
    assert_unreconciled(Budget.restore(Budget(**fields).saved()))


@pytest.mark.parametrize("wh,stopped", [(0, False), (7, False), (0, True), (51, True)])
def test_restore_preserves_valid_saved_budget_but_excludes_downtime(wh, stopped):
    budget = Budget(
        scope=SCOPE,
        wh=wh,
        stopped=stopped,
        reset=stopped,
        cause="import_budget" if stopped else None,
        previous=(20, 500, 250, True),
        low_since=10,
    )
    restored = Budget.restore(budget.saved())
    assert restored.saved() == budget.saved() | {"reset": False}
    assert restored.previous is None and restored.low_since is None
    assert restored.bind(SCOPE)
    assert restored.wh == wh and restored.stopped is stopped


@pytest.mark.parametrize("cause", [None, "operator_stop", "netz_scope_exit"])
def test_valid_unbound_budget_can_bind_first_scope(cause):
    for budget in (Budget(cause=cause), Budget.restore(Budget(cause=cause).saved())):
        assert budget.bind(SCOPE) and budget.wh == 0 and not budget.stopped


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1},
        {"version": 1, "economics": None},
        {"version": 1, "economics": {}},
        {"version": 1, "economics": []},
        {"version": 1, "economics": False},
        {"version": 1, "economics": 0},
        {"version": 1, "economics": stored_budget(), "station_identity": []},
        {"version": 99, "economics": stored_budget()},
        {},
        [],
        False,
        None,
    ],
)
async def test_current_store_invalid_payload_never_uses_legacy_fallback(ha, payload):
    entry = entry_with_sources(ha)
    await journal_store(ha, entry).async_save(payload)
    await journal_store(ha, entry, "pv_economy").async_save(
        {"version": 1, "records": []}
    )
    await ha.install(entry)
    assert_unreconciled(entry.runtime_data.pv.budget)


@pytest.mark.parametrize(
    "raw",
    ["{", "{}", "null", "[]", '{"version":1,"key":"pv"}', '{"version":7,"data":{}}'],
)
async def test_invalid_store_wrapper_or_quarantine_is_not_absence(ha, raw):
    entry = entry_with_sources(ha)
    path = Path(journal_store(ha, entry).path)

    def seed():
        path.parent.mkdir(exist_ok=True)
        path.write_text(raw, encoding="utf-8")

    await ha.async_add_executor_job(seed)
    await ha.install(entry)
    assert_unreconciled(entry.runtime_data.pv.budget)
    if raw == "{":
        assert not await ha.async_add_executor_job(path.exists)
        assert (
            len(
                await ha.async_add_executor_job(
                    lambda: list(path.parent.glob(f"{path.name}.corrupt.*"))
                )
            )
            == 1
        )
        # HA already removed the original path on the first read.
        await async_unload_entry(ha, entry)
        await async_setup_entry(ha, entry)
        assert_unreconciled(entry.runtime_data.pv.budget)


async def test_unreadable_store_is_not_first_installation(ha, monkeypatch):
    entry = entry_with_sources(ha)
    key = journal_store(ha, entry).key
    original = Store.async_load

    async def inaccessible(store):
        if store.key == key:
            raise PermissionError("simulated journal access failure")
        return await original(store)

    monkeypatch.setattr(Store, "async_load", inaccessible)
    await ha.install(entry)
    assert_unreconciled(entry.runtime_data.pv.budget)


@pytest.mark.parametrize(
    "serial,tx,problem",
    [
        ("serial-1", "net-test", None),
        ("other", "net-test", "economic_station_identity_mismatch"),
        ("serial-1", "other", "economic_scope_unreconciled"),
    ],
)
async def test_loaded_budget_requires_matching_station_and_transaction(
    ha, serial, tx, problem
):
    entry = entry_with_sources(ha)
    saved = stored_budget(wh=7) | {"scope": [*SCOPE[:-1], tx]}
    await journal_store(ha, entry).async_save(
        {
            "version": 1,
            "economics": saved,
            "station_identity": {"serial_number": serial},
        }
    )
    await ha.install(entry)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        count = len(station.profiles)
        await tick(pv)
        assert pv.input_problem == problem and pv.budget.saved() == saved
        assert len(station.profiles) == count + (problem is None)
        assert (station.amps > 0) is (problem is None)


@pytest.mark.parametrize("legacy_empty", [False, True])
async def test_first_installation_and_empty_legacy_can_charge(ha, legacy_empty):
    entry = entry_with_sources(ha)
    if legacy_empty:
        await journal_store(ha, entry, "pv_economy").async_save(
            {"version": 1, "records": []}
        )
    await ha.install(entry)
    assert entry.runtime_data.pv.budget.saved() == Budget().saved()
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        await tick(pv)
        assert station.amps > 0
        assert pv.budget.scope == SCOPE and pv.input_problem is None


@pytest.mark.parametrize(
    "kind", ["valid", "null", "invalid", "invalid_version", "day_only"]
)
async def test_startup_imports_legacy_history_without_restoring_release(ha, kind):
    entry = entry_with_sources(ha)
    old = legacy_economics()
    old["records"][0]["identity"]["entry_id"] = entry.entry_id
    old["records"][0]["identity"]["transaction"] = SCOPE[-1]
    if kind != "day_only":
        economy = {
            "valid": old,
            "null": None,
            "invalid": False,
            "invalid_version": {"version": True, "records": []},
        }[kind]
        await journal_store(ha, entry, "pv_economy").async_save(economy)
    await journal_store(ha, entry, "pv_day").async_save(
        {
            "version": 2,
            "days": {json.dumps(BINDING): {"date": "2026-10-25", "state": "FINISHED"}},
        }
    )
    await ha.install(entry)
    pv = entry.runtime_data.pv
    assert not pv.settings.get("charge_release")
    assert pv.planner.date == "2026-10-25" and pv.planner.phase == "FINISHED"
    if kind == "valid":
        assert pv.budget.scope == SCOPE and pv.budget.wh == 51 and pv.budget.stopped
        assert pv.budget.cause == "import_budget"
        assert pv.station_identity["serial_number"] == "serial-1"
    elif kind == "day_only":
        assert pv.budget.saved() == Budget().saved()
    else:
        assert_unreconciled(pv.budget)
        assert pv.budget.cause == "legacy_recovery_unknown"


async def test_damaged_current_journal_blocks_eligible_pv_until_explicit_stop(ha):
    entry = entry_with_sources(ha)
    await journal_store(ha, entry).async_save(
        {"version": 1, "economics": {}, "ended_transaction": "unreconciled"}
    )
    await ha.install(entry)
    async with peer(entry, station_type=GridStation) as station:
        grid, pv = await release(entry, station)
        count = len(station.profiles)
        await tick(pv)
        assert not station.amps and len(station.profiles) == count
        assert pv.input_problem == "economic_scope_unreconciled"
        assert_unreconciled(pv.budget)
        await grid.stop_release()
        assert not pv.settings.get("charge_release")
        assert pv.budget.saved() == Budget(cause="operator_stop").saved()
        assert (await pv.store.async_load())["economics"] == pv.budget.saved()
        await grid.action()
        await tick(pv)
        assert station.amps > 0 and pv.budget.scope == SCOPE


async def test_exhausted_current_journal_survives_restart_then_ends_with_transaction(
    ha,
):
    entry = entry_with_sources(ha)
    saved = stored_budget(wh=51, stopped=True, reset=True, cause="import_budget")
    await journal_store(ha, entry).async_save({"version": 1, "economics": saved})
    await ha.install(entry)
    await async_unload_entry(ha, entry)
    await async_setup_entry(ha, entry)
    pv = entry.runtime_data.pv
    assert not pv.settings.get("charge_release")
    assert pv.budget.saved() == saved | {"reset": False}
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        await tick(pv)
        assert not station.amps and pv.budget.wh == 51 and pv.budget.stopped
        for event, trigger, seq, tx in (
            ("Ended", "EVDeparted", 1, SCOPE[-1]),
            ("Started", "CablePluggedIn", 0, "next"),
        ):
            await station.call(
                call.TransactionEvent(
                    event_type=event,
                    timestamp=datetime.now(UTC).isoformat(),
                    trigger_reason=trigger,
                    seq_no=seq,
                    transaction_info={"transaction_id": tx},
                    evse={"id": 1, "connector_id": 1},
                )
            )
        await power(station, 0)
        await tick(pv)
        assert pv.budget.scope == [*SCOPE[:-1], "next"]
        assert not pv.budget.stopped and pv.budget.wh == 0
        assert station.amps > 0
