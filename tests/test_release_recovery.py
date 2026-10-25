"""Disabled manager release reconciles retained offers through existing OCPP fences."""

import asyncio
from datetime import UTC, datetime

import pytest
from conftest import eventually
from ocpp.v21 import call
from test_active_control import event, ready
from test_grid_profile import GridStation, start_grid
from test_protocol import peer

from custom_components.wallbox_manager import async_setup_entry, async_unload_entry


async def session(station, *, resumed=False):
    await station.call(
        call.TransactionEvent(
            event_type="Updated" if resumed else "Started",
            timestamp=datetime.now(UTC).isoformat(),
            trigger_reason="TxResumed" if resumed else "CablePluggedIn",
            seq_no=1 if resumed else 0,
            transaction_info={
                "transaction_id": "net-test",
                "charging_state": "EVConnected",
            },
            evse={"id": 1, "connector_id": 1},
        )
    )


def periods(station):
    return [
        p[1]["charging_schedule"][0]["charging_schedule_period"][0]
        for p in station.profiles
    ]


@pytest.mark.parametrize("recovery", ["reconnect", "reload", "takeover"])
@pytest.mark.parametrize("offered", [16, 0])
async def test_release_off_reconciles_recovered_offer_once(ha, recovery, offered):
    entry = await ha.install()
    if recovery != "takeover":
        async with peer(entry, station_type=GridStation) as station:
            grid = await start_grid(entry, station)
            assert station.amps == 16
            if recovery == "reconnect":
                # Reconnect preserves an active business release; this case
                # explicitly exercises release-OFF retained-offer recovery.
                await grid.stop_release()
            if recovery == "reload":
                assert await async_unload_entry(ha, entry)
        if recovery == "reload":
            assert await async_setup_entry(ha, entry)

    async with peer(entry, station_type=GridStation) as station:
        station.values.update(
            ControlAuthority="Local" if recovery == "takeover" else "OCPP",
            PhaseRotation="RST",
        )
        station.amps, station.phases = offered, 3
        control = await ready(entry, station)
        grid, settings, state = (
            entry.runtime_data.grid,
            entry.runtime_data.settings,
            entry.runtime_data.state,
        )
        assert settings.get("charge_release") is False
        assert not station.profiles  # No transaction yet.
        await session(station, resumed=recovery != "takeover")
        if recovery == "takeover":
            assert (
                not station.profiles
                and "GetCompositeSchedule" not in station.operations
            )
            assert await control.request("ControlAuthority", "OCPP") == "confirmed"
        await eventually(lambda: grid.task is not None and grid.task.done())
        assert station.amps == 0
        assert grid.confirmed.amps == 0 and grid.status == "confirmed"
        assert settings.get("charge_release") is False
        assert settings.approved("soll_power") == (
            None if recovery == "takeover" else 11
        )
        if recovery == "takeover":
            # Fresh discovery initializes only the draft, never an approval.
            await eventually(lambda: settings.get("soll_power") == 1.4)
        assert station.values["ChargingEnabled"] == "true" and station.phases == 3
        assert grid.transaction() == "net-test" and state.session["ended_at"] is None
        assert periods(station) == (
            [{"start_period": 0, "limit": 0}] if offered else []
        )
        for _, profile in station.profiles:
            assert profile["transaction_id"] == "net-test"
        count = len(station.profiles)
        for _ in range(3):
            await grid.reconcile(grid.epoch, entry.runtime_data.server.adapter)
        assert len(station.profiles) == count  # Confirmed zero never repeats.


@pytest.mark.parametrize("authority,transaction", [("Local", True), ("OCPP", False)])
async def test_release_off_recovery_cannot_write_without_authority_or_transaction(
    ha, authority, transaction
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values.update(ControlAuthority=authority, PhaseRotation="RST")
        station.amps, station.phases = 16, 3
        await ready(entry, station)
        if transaction:
            await session(station)
        grid = entry.runtime_data.grid
        await grid.reconcile(grid.epoch, entry.runtime_data.server.adapter)
        assert not station.profiles and station.amps == 16
        assert not grid.eligible() and grid.status == "waiting"
        assert entry.runtime_data.settings.get("charge_release") is False
        assert "GetCompositeSchedule" not in station.operations


async def test_unknown_offer_after_recovery_stays_unconfirmed_without_blind_write(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values.update(ControlAuthority="OCPP", PhaseRotation="RST")
        station.amps, station.phases, station.readback = 16, 3, False
        await ready(entry, station)
        await session(station)
        grid = entry.runtime_data.grid
        await eventually(lambda: grid.task is not None and grid.task.done())
        assert grid.status == "unconfirmed" and grid.current_point() is None
        assert not station.profiles and grid.timer is None


@pytest.mark.parametrize("fence", ["local", "disconnect", "boot", "transaction"])
async def test_recovery_zero_is_fenced_before_wire_dispatch(ha, monkeypatch, fence):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values.update(ControlAuthority="OCPP", PhaseRotation="RST")
        station.amps, station.phases = 16, 3
        await ready(entry, station)
        grid = entry.runtime_data.grid
        entered, release = asyncio.Event(), asyncio.Event()
        original = grid.dispatch_point

        async def delayed(point, epoch, adapter, transaction):
            entered.set()
            await release.wait()
            return await original(point, epoch, adapter, transaction)

        monkeypatch.setattr(grid, "dispatch_point", delayed)
        await session(station)
        await asyncio.wait_for(entered.wait(), 5)
        task = grid.task
        state = entry.runtime_data.state
        if fence == "local":
            event(entry, "ControlAuthority", "Local")
        elif fence == "disconnect":
            state.reset(connected=False)
        elif fence == "boot":
            state.reset(connected=True, boot=True)
        else:
            state.session["ended_at"] = datetime.now(UTC).isoformat()
            state.publish(("session", state.binding, "active"))
        release.set()
        await task
        assert not station.profiles and station.amps == 16
        assert entry.runtime_data.settings.get("charge_release") is False


@pytest.mark.parametrize("fence", ["local", "disconnect", "boot", "transaction"])
async def test_late_recovery_zero_ack_cannot_confirm_superseded_generation(
    ha, monkeypatch, fence
):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values.update(ControlAuthority="OCPP", PhaseRotation="RST")
        station.amps, station.phases = 16, 3
        await ready(entry, station)
        grid, adapter = entry.runtime_data.grid, entry.runtime_data.server.adapter
        entered, release = asyncio.Event(), asyncio.Event()
        original = adapter.request

        async def delayed(payload):
            response = await original(payload)
            if isinstance(payload, call.SetChargingProfile):
                entered.set()
                await release.wait()
            return response

        monkeypatch.setattr(adapter, "request", delayed)
        await session(station)
        await asyncio.wait_for(entered.wait(), 5)
        task = grid.task
        state = entry.runtime_data.state
        if fence == "local":
            event(entry, "ControlAuthority", "Local")
        elif fence == "disconnect":
            state.reset(connected=False)
        elif fence == "boot":
            state.reset(connected=True, boot=True)
        else:
            state.session["ended_at"] = datetime.now(UTC).isoformat()
            state.publish(("session", state.binding, "active"))
        release.set()
        await task
        assert periods(station) == [{"start_period": 0, "limit": 0}]
        assert grid.acknowledged is None
        assert grid.status != "confirmed"
        assert entry.runtime_data.settings.get("charge_release") is False


@pytest.mark.parametrize("result", ["Rejected", "Accepted"])
async def test_failed_or_unconfirmed_recovery_pause_does_not_claim_zero(ha, result):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        station.values.update(ControlAuthority="OCPP", PhaseRotation="RST")
        station.amps, station.phases = 16, 3
        station.profile_result, station.apply_profile = result, False
        station.profile_info = {"reason_code": "Busy"} if result == "Rejected" else None
        await ready(entry, station)
        await session(station)
        grid = entry.runtime_data.grid
        await eventually(lambda: grid.task is not None and grid.task.done())
        assert station.amps == 16 and grid.status == (
            "rejected" if result == "Rejected" else "accepted_unconfirmed"
        )
        assert grid.timer is None
        assert periods(station) == [{"start_period": 0, "limit": 0}]
        assert grid.confirmed.amps == 16
