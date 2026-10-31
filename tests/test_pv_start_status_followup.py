"""Durable PV authorization is independent of older electrical command outcomes."""

import asyncio
from unittest.mock import Mock

import pytest
from conftest import entities, eventually
from homeassistant.exceptions import HomeAssistantError
from test_active_control import ready
from test_grid_profile import GridStation, start_grid
from test_protocol import peer
from test_pv_runtime import install, release
from test_safe_charge_actions import context, invoke


@pytest.mark.parametrize("profile", ["PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM"])
@pytest.mark.parametrize("status", ["rejected", "communication_failure", "unsupported"])
async def test_pv_start_service_accepts_durable_release_despite_old_outcome(
    ha, profile, status
):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid, _ = await release(entry, station, profile=profile)
        await grid.stop_release()
        assert grid.eligible()
        grid.status, grid.reason = status, "previous electrical result"
        grid.publish()
        before = len(station.profiles)
        await invoke(ha, entry, context(grid))
        assert grid.settings.get("charge_release") is True
        assert (await grid.settings.store.async_load())[grid.settings.key][
            "charge_release"
        ] is True
        assert grid.status == status  # Do not invent a successful electrical outcome.
        assert len(station.profiles) == before
        button = next(e for e in entities(ha, entry) if e.role == "charge_action")
        assert ha.states.get(button.entity_id).attributes["charge_action"] == "stop"


async def test_pv_start_does_not_wait_for_an_older_electrical_task(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        grid, _ = await release(entry, station)
        await grid.stop_release()
        assert grid.eligible()
        pending = asyncio.create_task(asyncio.Event().wait())
        grid.task = pending
        grid.status = "communication_failure"
        try:
            async with asyncio.timeout(5):
                await invoke(ha, entry, context(grid))
            assert grid.settings.get("charge_release") is True
            assert not pending.done()
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_real_pv_persistence_failure_still_fails_start(ha, monkeypatch):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        control = await ready(entry, station)
        await control.request("ControlAuthority", "OCPP")
        grid = entry.runtime_data.grid
        await grid.settings.set("charging_profile", "PV_MAXIMUM")

        async def fail(_):
            raise OSError("Durable write failed")

        monkeypatch.setattr(grid.settings, "persist", fail)
        with pytest.raises(HomeAssistantError, match="Durable write failed"):
            await invoke(ha, entry, context(grid))
        assert not grid.settings.get("charge_release")
        assert not station.profiles


async def test_netz_electrical_rejection_still_fails_service(ha):
    entry = await ha.install()
    async with peer(entry, station_type=GridStation) as station:
        grid = await start_grid(entry, station)
        await grid.stop_release()
        station.profile_result = "Rejected"
        with pytest.raises(HomeAssistantError):
            await invoke(ha, entry, context(grid))
        assert grid.status == "rejected"


async def test_source_mapping_projection_updates_once_without_power_churn(
    ha, monkeypatch
):
    entry = await ha.install()
    roles = (
        "storage_charge_power",
        "storage_discharge_power",
        "grid_import_power",
        "grid_export_power",
    )
    profile = next(e for e in entities(ha, entry) if e.role == "charging_profile")
    writes = Mock(wraps=profile.async_write_ha_state)
    monkeypatch.setattr(profile, "async_write_ha_state", writes)
    options = {role: f"sensor.followup_{role}" for role in roles}
    ha.config_entries.async_update_entry(entry, options={**entry.options, **options})
    await ha.async_block_till_done()
    for role in roles:
        assert (
            ha.states.get(profile.entity_id).attributes[f"{role}_entity_id"]
            == options[role]
        )
    assert writes.call_count == 1
    writes.reset_mock()
    for n in range(300):
        for role in roles:
            ha.states.async_set(options[role], str(n), {"unit_of_measurement": "W"})
        await asyncio.sleep(0)
    await ha.async_block_till_done()
    assert writes.call_count == 0
    assert not entry.runtime_data.settings.get("charge_release")
    ha.config_entries.async_update_entry(
        entry,
        options={**entry.options, "storage_charge_power": "sensor.replaced_charge"},
    )
    await eventually(lambda: writes.call_count == 1)
    await ha.async_block_till_done()
    assert writes.call_count == 1
