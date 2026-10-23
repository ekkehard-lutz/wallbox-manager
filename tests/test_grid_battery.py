"""Real HA number services and durable reserve recovery; no external HA instance."""

import asyncio
from datetime import UTC, datetime

from conftest import config, eventually
from homeassistant.core import ServiceCall, callback

from custom_components.wallbox_manager.battery import BatteryReserve


async def battery(ha):
    entry = config()
    ha.config_entries._entries[entry.entry_id] = entry
    ha.config_entries.async_update_entry(
        entry,
        options={
            "min_soc_speicher": "number.reserve",
            "soc_speicher_aktuell": "sensor.soc",
        },
    )
    ha.states.async_set("number.reserve", "20", {"min": 0, "max": 100, "step": 1})
    ha.states.async_set("sensor.soc", "65")
    writes = []

    @callback
    def write(call: ServiceCall):
        writes.append(call.data["value"])
        ha.states.async_set(
            "number.reserve", str(call.data["value"]), {"min": 0, "max": 100, "step": 1}
        )

    ha.services.async_register("number", "set_value", write)
    reserve = BatteryReserve(ha, entry)
    await reserve.load(preserve=True)
    return reserve, writes, entry


async def test_reserve_rounding_restore_and_external_release(ha):
    b, writes, _ = await battery(ha)
    await b.update(40)
    assert writes == [40]
    assert (await b.store.async_load())["original"] == 20
    await b.update(40)
    assert writes == [40]
    ha.states.async_set("sensor.soc", "37.8")
    await b.update(40)
    assert writes == [40, 37]
    await b.update(None)
    assert writes == [40, 37, 20]
    assert b.record is None
    await b.update(40)
    ha.states.async_set("number.reserve", "45", {"min": 0, "max": 100, "step": 1})
    await b.update(40)
    await b.update(40)
    await b.update(None)
    assert writes == [40, 37, 20, 37]
    assert ha.states.get("number.reserve").state == "45"


async def test_restart_journal_adopts_without_oscillation_then_restores(ha):
    b, writes, entry = await battery(ha)
    await b.update(40)
    recovered = BatteryReserve(ha, entry)
    await recovered.load(preserve=True)
    assert recovered.recovering
    assert writes == [40]
    recovered.resume()
    await recovered.update(40)
    assert writes == [40]
    await recovered.update(None)
    assert writes == [40, 20]
    assert await recovered.store.async_load() is None


async def test_delayed_readback_and_original_protection(ha):
    b, writes, _ = await battery(ha)
    release = asyncio.Event()

    async def delayed(call):
        writes.append(call.data["value"])
        await release.wait()
        ha.states.async_set(
            "number.reserve", str(call.data["value"]), {"min": 0, "max": 100, "step": 1}
        )

    ha.services.async_register("number", "set_value", delayed)
    pending = asyncio.create_task(b.update(40))
    await eventually(lambda: bool(writes))
    assert b.record["original"] == 20
    assert b.status == "confirmation_pending"
    release.set()
    await pending
    assert b.status == "active"
    ha.states.async_set("sensor.soc", "10")
    await b.update(40)
    assert writes == [40, 20]
    assert b.record is None


async def test_missing_references_do_not_write(ha):
    b = BatteryReserve(ha, config())
    await b.load()
    await b.update(40)
    assert b.record is None


async def test_soc_events_adjust_reserve_without_ocpp_setpoint(ha):
    from ocpp.v21 import call
    from test_grid_profile import GridStation, start_grid
    from test_protocol import peer

    _, writes, entry = await battery(ha)
    await ha.install(entry)
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await eventually(lambda: grid.status == "confirmed")
        at = datetime.now(UTC).isoformat()
        await station.call(
            call.TransactionEvent(
                event_type="Updated",
                timestamp=at,
                trigger_reason="ChargingStateChanged",
                seq_no=1,
                transaction_info={
                    "transaction_id": "net-test",
                    "charging_state": "Charging",
                },
                evse={"id": 1, "connector_id": 1},
                meter_value=[
                    {
                        "timestamp": at,
                        "sampled_value": [
                            {
                                "measurand": "Power.Active.Import",
                                "value": 10000,
                                "unit_of_measure": {"unit": "W"},
                            }
                        ],
                    }
                ],
            )
        )
        await entry.runtime_data.settings.set("min_soc", 40)
        await eventually(lambda: writes == [40])
        ha.states.async_set("sensor.soc", "37.8")
        await eventually(lambda: writes == [40, 37])
        assert len(station.profiles) == 1
        await entry.runtime_data.server.control.request("ChargingEnabled", "false")
        await eventually(lambda: writes == [40, 37, 20])
        assert len(station.profiles) == 1
