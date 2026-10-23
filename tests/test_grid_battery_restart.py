"""Persisted reserve recovery through integration restart and real OCPP frames."""

from datetime import UTC, datetime

import pytest
from conftest import eventually
from ocpp.v21 import call
from test_active_control import ready
from test_grid_battery import battery
from test_grid_profile import GridStation, start_grid
from test_protocol import peer

from custom_components.wallbox_manager import async_setup_entry, async_unload_entry


async def transaction(station, kind, *, charging="Charging", power=None, sequence=1):
    at = datetime.now(UTC).isoformat()
    await station.call(
        call.TransactionEvent(
            event_type=kind,
            timestamp=at,
            trigger_reason="EVDeparted" if kind == "Ended" else "ChargingStateChanged",
            seq_no=sequence,
            transaction_info={"transaction_id": "net-test", "charging_state": charging},
            evse={"id": 1, "connector_id": 1},
            meter_value=[
                {
                    "timestamp": at,
                    "sampled_value": [
                        {
                            "measurand": "Power.Active.Import",
                            "value": power,
                            "unit_of_measure": {"unit": "W"},
                        }
                    ],
                }
            ]
            if power is not None
            else None,
        )
    )


async def connector(station, status):
    await station.call(
        call.StatusNotification(
            timestamp=datetime.now(UTC).isoformat(),
            evse_id=1,
            connector_id=1,
            connector_status=status,
        )
    )


@pytest.fixture
async def restarted_grid(ha):
    _, writes, entry = await battery(ha)
    assert await async_setup_entry(ha, entry)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        await entry.runtime_data.settings.set("min_soc", 40)
        await transaction(station, "Updated", power=10000)
        await eventually(lambda: writes == [40])
        original = entry.runtime_data
        journal = await original.battery.reserve.store.async_load()
        assert journal == {"entity": "number.reserve", "original": 20, "temporary": 40}
        # Deliberate HA integration shutdown preserves the journal; unplug/session
        # termination happens while this integration has no connection or runtime.
        assert await async_unload_entry(ha, entry)
    assert writes == [40]
    assert await async_setup_entry(ha, entry)
    runtime = entry.runtime_data
    assert runtime.state is not original.state
    assert runtime.state.session["generation"] is None
    assert runtime.battery.reserve.recovering
    assert await runtime.battery.reserve.store.async_load() == journal
    assert writes == [40]
    yield entry, writes


async def initialize(entry, station):
    station.values.update(
        ControlAuthority="OCPP", ChargingEnabled="true", PhaseRotation="RST"
    )
    station.amps, station.phases = 16, 3
    await ready(entry, station)
    state = entry.runtime_data.state
    assert state.current(state.registered_generation)
    assert (
        state.value(("station", ("Wallbox01", None, None), "control_authority"))
        == "remote"
    )
    assert state.value(("control", state.binding, "charging_enabled")) is True
    assert entry.runtime_data.settings.get("charging_profile") == "NETZ"


@pytest.mark.parametrize("evidence", ["ended", "absent"])
async def test_restart_restores_reserve_after_authoritative_end(
    ha, restarted_grid, evidence
):
    entry, writes = restarted_grid
    async with peer(entry, station_type=GridStation) as station:
        await initialize(entry, station)
        reserve = entry.runtime_data.battery.reserve
        assert reserve.recovering and writes == [40]
        if evidence == "ended":
            await transaction(station, "Ended", charging="Idle", sequence=2)
            state = entry.runtime_data.state
            assert state.session["generation"] == list(state.generation)
            assert state.session["ended_at"] is not None
        else:
            await connector(station, "Available")
            assert (
                entry.runtime_data.state.value(
                    ("observation", entry.runtime_data.state.binding, "connector_state")
                )
                == "available"
            )
        await eventually(lambda: writes == [40, 20])
        await ha.async_block_till_done()
        assert not reserve.recovering
        assert reserve.record is None
        assert await reserve.store.async_load() is None
        assert station.values["ControlAuthority"] == "OCPP"
        assert station.values["ChargingEnabled"] == "true"
        assert not station.profiles and not station.writes


async def test_restart_preserves_reserve_for_confirmed_ongoing_charging(
    ha, restarted_grid
):
    entry, writes = restarted_grid
    async with peer(entry, station_type=GridStation) as station:
        await initialize(entry, station)
        await connector(station, "Occupied")
        await transaction(station, "Updated", power=10000, sequence=2)
        reserve = entry.runtime_data.battery.reserve
        await eventually(lambda: not reserve.recovering)
        await ha.async_block_till_done()
        assert writes == [40]  # No 40 -> 20 -> 40 recovery oscillation.
        assert reserve.record["original"] == 20
        assert reserve.record["temporary"] == 40
        assert await reserve.store.async_load() == reserve.record
        assert not station.profiles and not station.writes


@pytest.mark.parametrize(
    "evidence",
    [
        "missing",
        "occupied",
        "pause",
        "unknown",
        "historical_end",
        "pre_registration_absence",
    ],
)
async def test_restart_keeps_journal_during_incomplete_or_paused_session(
    ha, restarted_grid, evidence
):
    entry, writes = restarted_grid
    async with peer(entry, station_type=GridStation) as station:
        if evidence == "pre_registration_absence":
            await connector(station, "Available")
            await ha.async_block_till_done()
            assert entry.runtime_data.battery.reserve.recovering
            assert writes == [40]
        await initialize(entry, station)
        state = entry.runtime_data.state
        if evidence == "occupied":
            await connector(station, "Occupied")
        elif evidence == "pause":
            await transaction(
                station, "Updated", charging="SuspendedEV", power=0, sequence=2
            )
        elif evidence == "unknown":
            # Explicit unknown is neither physical absence nor an Ended event.
            state.observe(
                state.generation,
                ("observation", state.binding, "connector_state"),
                None,
                "test:unknown",
            )
        elif evidence == "historical_end":
            state.session["ended_at"] = state.session["updated_at"]
            state.publish(("session", state.binding, "active"))
        await ha.async_block_till_done()
        reserve = entry.runtime_data.battery.reserve
        assert reserve.recovering
        assert writes == [40]
        assert await reserve.store.async_load() == reserve.record
        assert not station.profiles and not station.writes


async def test_restart_end_releases_external_reserve_without_restoring(
    ha, restarted_grid
):
    entry, writes = restarted_grid
    async with peer(entry, station_type=GridStation) as station:
        await initialize(entry, station)
        ha.states.async_set("number.reserve", "45", {"min": 0, "max": 100, "step": 1})
        await connector(station, "Available")
        reserve = entry.runtime_data.battery.reserve
        await eventually(lambda: reserve.record is None)
        await ha.async_block_till_done()
        assert not reserve.recovering
        assert writes == [40]
        assert ha.states.get("number.reserve").state == "45"
        assert await reserve.store.async_load() is None
        assert not station.profiles and not station.writes


async def test_restart_end_restoration_error_keeps_journal_until_entity_recovers(
    ha, restarted_grid
):
    from homeassistant.core import callback

    entry, writes = restarted_grid
    fail = True

    @callback
    def restore(call):
        writes.append(call.data["value"])
        if fail:
            raise OSError("simulated reserve service failure")
        ha.states.async_set(
            "number.reserve", str(call.data["value"]), {"min": 0, "max": 100, "step": 1}
        )

    ha.services.async_register("number", "set_value", restore)
    async with peer(entry, station_type=GridStation) as station:
        await initialize(entry, station)
        await connector(station, "Available")
        reserve = entry.runtime_data.battery.reserve
        await eventually(lambda: reserve.status == "error")
        assert not reserve.recovering
        assert writes == [40, 20]
        assert ha.states.get("number.reserve").state == "40.0"
        assert await reserve.store.async_load() == reserve.record
        assert reserve.record["original"] == 20
        fail = False
        ha.states.async_set("number.reserve", "unavailable")
        await ha.async_block_till_done()
        ha.states.async_set("number.reserve", "40", {"min": 0, "max": 100, "step": 1})
        await eventually(lambda: reserve.record is None)
        await ha.async_block_till_done()
        assert writes == [40, 20, 20]
        assert await reserve.store.async_load() is None
        assert float(ha.states.get("number.reserve").state) == 20
        assert not station.profiles and not station.writes
