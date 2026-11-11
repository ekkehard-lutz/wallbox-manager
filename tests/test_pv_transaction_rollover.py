"""Current scoped OCPP transactions own independent PV optimization budgets."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import eventually
from ocpp.exceptions import OCPPError
from ocpp.v21 import call
from test_active_control import ready
from test_grid_profile import GridStation
from test_protocol import peer
from test_pv_journal_recovery import entry_with_sources, journal_store
from test_pv_phase_lockout import PhaseStation, establish, periods, setup
from test_pv_runtime import install, power, release, tick
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from custom_components.wallbox_manager import async_setup_entry, async_unload_entry
from custom_components.wallbox_manager.pv_economics import Budget
from custom_components.wallbox_manager.session import event

A, B = "net-test", "next-transaction"


async def report(station, tx, *, kind="Started", seq=0, at=None, evse=1, connector=1):
    await station.call(
        call.TransactionEvent(
            event_type=kind,
            timestamp=(at or datetime.now(UTC)).isoformat(),
            trigger_reason={
                "Started": "CablePluggedIn",
                "Updated": "TxResumed",
                "Ended": "EVDeparted",
            }[kind],
            seq_no=seq,
            transaction_info={"transaction_id": tx, "charging_state": "EVConnected"},
            evse={"id": evse, "connector_id": connector},
        ),
        suppress=False,
    )


def scope(pv, transaction):
    return [*pv.state.binding, "remote", transaction]


@pytest.mark.parametrize("stopped,ended", [(False, False), (True, False), (True, True)])
async def test_new_transaction_rolls_over_import_and_stop(ha, stopped, ended):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await setup(entry, station)
        await establish(grid, pv, station, 1, 20)
        pv.values["grid_import_power"] = 1800
        pv.core.startup = 0
        await tick(pv)
        station.clock.now += 100 if stopped else 80
        await tick(pv)
        assert pv.budget.wh == (50 if stopped else 40)
        assert pv.budget.stopped is stopped
        assert pv.budget.scope == scope(pv, A)

        count = len(station.profiles)
        timer, task = pv.timer, grid.task
        if ended:
            await report(station, A, kind="Ended", seq=1)
        await report(station, B)
        assert grid.transaction() == B
        assert (
            len(station.profiles) == count and pv.timer is timer and grid.task is task
        )
        assert pv.budget.previous is None and pv.budget.low_since is None
        pv.values.update(grid_import_power=0, grid_export_power=2000)
        await power(station, 0)
        await tick(pv)
        assert pv.budget.saved() == Budget(scope=scope(pv, B)).saved()
        assert len(station.profiles) == count + 1 and station.amps > 0
        assert (await pv.store.async_load())["economics"] == pv.budget.saved()

        # An exact duplicate, a newer update for B, and a late Ended for A
        # must not retire the newly bound budget.
        pv.budget.wh = 12
        await report(station, B)
        await report(station, B, kind="Updated", seq=1)
        await report(station, A, kind="Ended", seq=2)
        await tick(pv)
        assert pv.budget.scope == scope(pv, B) and pv.budget.wh == 12


@pytest.mark.parametrize("stopped", [False, True])
async def test_same_transaction_resume_preserves_import_and_stop(ha, stopped):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await setup(entry, station)
        await tick(pv)
        pv.budget.wh = 50 if stopped else 40
        pv.budget.exhaust(50)
        expected = pv.budget.saved()
        generation = pv.state.generation
    await eventually(lambda: not pv.state.connected)
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        await report(station, A, kind="Updated", seq=1)
        assert not station.profiles and grid.transaction() == A
        assert not event(
            pv.state, generation, pv.state.binding, B, "Started", datetime.now(UTC), 0
        )
        await power(station, 0)
        await tick(pv)
        assert pv.budget.saved() == expected
        if stopped:
            assert not any(amps > 0 for _, amps in periods(station))


async def test_invalid_transaction_observations_do_not_roll_over(ha):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await setup(entry, station)
        await tick(pv)
        pv.budget.wh = 40
        expected = pv.budget.saved()
        count = len(station.profiles)
        await report(station, B, at=pv.state.epoch - timedelta(microseconds=1))
        for evse, connector in ((2, 1), (1, 2)):
            with pytest.raises(OCPPError):
                await report(station, B, evse=evse, connector=connector)
        async with connect(
            f"ws://127.0.0.1:{grid.listener.port}/OtherStation",
            subprotocols=["ocpp2.1"],
        ) as wrong:
            with pytest.raises(ConnectionClosed):
                await wrong.recv()
        assert grid.transaction() == A and len(station.profiles) == count
        await tick(pv)
        assert pv.budget.saved() == expected


async def test_valid_reloaded_budget_rolls_to_observed_new_transaction(ha):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await setup(entry, station)
        await tick(pv)
        pv.budget.wh = 50
        assert pv.budget.exhaust(50)
        await pv.persist()
        assert await async_unload_entry(ha, entry)
    await async_setup_entry(ha, entry)
    grid, pv = entry.runtime_data.grid, entry.runtime_data.pv
    assert pv.budget.scope == scope(pv, A) and pv.budget.stopped
    async with peer(entry, station_type=GridStation) as station:
        station.values["ControlAuthority"] = "OCPP"
        await ready(entry, station)
        await report(station, B, kind="Updated", seq=3)
        await power(station, 0)
        await grid.action()
        assert not station.profiles
        await tick(pv)
        assert pv.budget.saved() == Budget(scope=scope(pv, B)).saved()
        assert (await pv.store.async_load())["economics"] == pv.budget.saved()
        assert station.amps > 0


@pytest.mark.parametrize("history", ["corrupt", "quarantined", "unreconciled"])
async def test_unknown_journal_cannot_roll_over_to_valid_transaction(ha, history):
    entry = entry_with_sources(ha)
    store = journal_store(ha, entry)
    if history == "unreconciled":
        await store.async_save(
            {"version": 1, "economics": Budget.restore(None).saved()}
        )
    else:
        path = Path(store.path)
        if history == "quarantined":
            path = path.with_name(f"{path.name}.corrupt.test")

        def seed():
            path.parent.mkdir(exist_ok=True)
            path.write_text("{", encoding="utf-8")

        await ha.async_add_executor_job(seed)
    await ha.install(entry)
    async with peer(entry, station_type=GridStation) as station:
        grid, pv = await release(entry, station)
        await tick(pv)
        expected = pv.budget.saved()
        assert expected["scope"] == ["unreconciled"] and expected["stopped"]
        count = len(station.profiles)
        await report(station, B)
        await power(station, 0)
        await tick(pv)
        assert grid.transaction() == B and pv.budget.saved() == expected
        assert len(station.profiles) == count
        assert pv.input_problem == "economic_scope_unreconciled"


async def test_new_transaction_persists_budget_before_positive_proposal(
    ha, monkeypatch
):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await setup(entry, station)
        await tick(pv)
        await report(station, B)
        station.amps = 0  # The new transaction has no retained applied offer.
        pv.values["grid_export_power"] = 2000
        await power(station, 0)
        count = len(station.profiles)
        save = pv.store.async_save

        async def fail_new_scope(record):
            if record["economics"]["scope"] == scope(pv, B):
                raise OSError("new transaction journal write failed")
            await save(record)

        with monkeypatch.context() as patch:
            patch.setattr(pv.store, "async_save", fail_new_scope)
            await tick(pv)
            assert len(station.profiles) == count
            assert pv.input_problem == "journal_write_failed"
            assert (await pv.store.async_load())["economics"]["scope"] == scope(pv, A)
        await tick(pv)
        assert grid.transaction() == B and len(station.profiles) == count + 1
        assert (await pv.store.async_load())["economics"]["scope"] == scope(pv, B)
