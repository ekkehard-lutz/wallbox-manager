"""Central options preserve beta.13 values and use native HA sections."""

import pytest

from custom_components.wallbox_manager.regulation import DEFAULTS, migrate_regulation


def test_legacy_values_are_preserved_and_conflicts_are_deterministic():
    profiles = {
        "b": {
            "regulation_interval": 17,
            "pv_start_delay": 20,
            "pv_stop_delay": 90,
            "soc_hysterese": 8,
        },
        "a": {
            "regulation_interval": 13,
            "pv_start_delay": 30,
            "pv_stop_delay": 80,
            "soc_hysterese": 6,
        },
    }
    migrated = migrate_regulation({"soc_speicher_aktuell": "sensor.soc"}, profiles)
    assert {key: migrated[key] for key in profiles["a"]} == profiles["a"]
    assert migrated["legacy_regulation"] == profiles
    assert migrated["soc_speicher_aktuell"] == "sensor.soc"
    assert migrated["power_smoothing_window"] == 5
    assert (
        migrate_regulation(migrated, dict(reversed(list(profiles.items())))) == migrated
    )


def test_explicit_central_values_including_zero_win():
    options = {
        **DEFAULTS,
        "pv_start_delay": 0,
        "pv_stop_delay": 0,
        "power_smoothing_window": 0,
    }
    migrated = migrate_regulation(options, {"a": {"pv_stop_delay": 99}})
    assert {key: migrated[key] for key in DEFAULTS} == options


@pytest.mark.parametrize("profiles", [{}, {"target": {"profile": "NETZ"}}])
def test_defaults(profiles):
    expected = {
        "regulation_interval": 5,
        "pv_start_delay": 0,
        "pv_stop_delay": 90,
        "soc_hysterese": 5,
        "power_smoothing_window": 5,
    }
    migrated = migrate_regulation({}, profiles)
    assert {key: migrated[key] for key in DEFAULTS} == DEFAULTS == expected
    from custom_components.wallbox_manager.pv_surplus import PV_DEFAULTS

    assert {key: PV_DEFAULTS[key] for key in expected if key in PV_DEFAULTS} == {
        key: value for key, value in expected.items() if key != "power_smoothing_window"
    }


async def test_real_entry_migrates_store_and_keeps_regulation_out_of_profile_store(
    tmp_path,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from homeassistant.config_entries import ConfigEntries
    from homeassistant.core import HomeAssistant
    from test_ha_lifecycle import entry

    from custom_components.wallbox_manager.battery import BatteryReserve
    from custom_components.wallbox_manager.profiles import GridProfiles
    from custom_components.wallbox_manager.runtime import Runtime

    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    config = entry()
    hass.config_entries._entries[config.entry_id] = config
    runtime = Runtime()
    control = SimpleNamespace(runtime=runtime)
    profiles = GridProfiles(hass, config, control, BatteryReserve(hass, config))
    stored = {
        "a": {
            "profile": "NETZ",
            "power_kw": 7,
            "min_soc": 25,
            "pv_stop_delay": 123,
            "pv_start_delay": 12,
            "soc_hysterese": 9,
            "regulation_interval": 17,
        }
    }
    profiles.store.async_load = AsyncMock(return_value=stored)
    profiles.store.async_save = AsyncMock()
    try:
        await profiles.load()
        assert config.options["pv_stop_delay"] == 123
        assert config.options["pv_start_delay"] == 12
        assert config.options["regulation_interval"] == 17
        assert config.options["soc_hysterese"] == 9
        assert config.options["power_smoothing_window"] == 5
        assert config.options["legacy_regulation"]["a"]["pv_stop_delay"] == 123
        await profiles.save()
        saved = profiles.store.async_save.call_args.args[0]["a"]
        assert not set(saved) & DEFAULTS.keys()
        assert saved["power_kw"] == 7 and saved["min_soc"] == 25
    finally:
        profiles.unsubscribe_battery()
        profiles.unsubscribe()
        profiles.session_unsubscribe()
        await hass.async_stop()


@pytest.mark.parametrize("delay", [0, 60, 75, 90, 123, 3600])
@pytest.mark.parametrize("source", ["central", "legacy"])
def test_stop_delay_preserves_explicit_values(delay, source):
    options = {"pv_stop_delay": delay} if source == "central" else {}
    legacy = {"target": {"pv_stop_delay": 17 if source == "central" else delay}}
    migrated = migrate_regulation(options, legacy)
    assert migrated["pv_stop_delay"] == delay
    assert migrated["power_smoothing_window"] == 5
    assert migrate_regulation(migrated, legacy) == migrated


@pytest.mark.parametrize("hysteresis", [-1, 0, 5, 5.5, 40, 99, 99.5, 100])
async def test_hysteresis_options_entity_and_backend_ranges_agree(tmp_path, hysteresis):
    from types import SimpleNamespace

    import voluptuous as vol
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
    from test_ha_lifecycle import entry

    from custom_components.wallbox_manager.config_flow import ReferenceOptionsFlow
    from custom_components.wallbox_manager.core.models import (
        ConnectorId,
        EvseId,
        StationId,
    )
    from custom_components.wallbox_manager.number import ProfileNumber
    from custom_components.wallbox_manager.profiles import GridProfiles
    from custom_components.wallbox_manager.pv_surplus import PV_DEFAULTS, decision

    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    config = entry()
    hass.config_entries._entries[config.entry_id] = config
    flow = ReferenceOptionsFlow()
    flow.hass, flow.handler = hass, config.entry_id
    try:
        form = await flow.async_step_init()
        schema = form["data_schema"]
        assert schema({"general": {}, "regulation": {}})["regulation"] == DEFAULTS
        serialized = FlowManagerIndexView(None)._prepare_result_json(form)
        regulation = next(
            field
            for field in serialized["data_schema"]
            if field["name"] == "regulation"
        )
        selector = next(
            field for field in regulation["schema"] if field["name"] == "soc_hysterese"
        )["selector"]["number"]
        target = ConnectorId(EvseId(StationId("test"), "1"), "1")
        entity = ProfileNumber(
            SimpleNamespace(), config.entry_id, target, "soc_hysterese"
        )
        assert selector["min"] == entity.native_min_value == 0
        assert selector["max"] == entity.native_max_value == 99
        settings = {**PV_DEFAULTS, "soll_soc_speicher": 40, "soc_hysterese": hysteresis}
        submitted = {"general": {}, "regulation": {"soc_hysterese": hysteresis}}
        if 0 <= hysteresis <= 99:
            assert schema(submitted)["regulation"]["soc_hysterese"] == hysteresis
            GridProfiles.validate_pv(settings)
            if hysteresis >= 40:
                # Zero/negative thresholds cannot be crossed by valid SoC;
                # the independent strict start threshold still prevents a start.
                assert decision(2000, 0, settings, True)[0] == 2000
                assert decision(2000, 0, settings, False)[0] == 0
        else:
            with pytest.raises(vol.Invalid):
                schema(submitted)
            with pytest.raises(ValueError):
                GridProfiles.validate_pv(settings)
    finally:
        await hass.async_stop()
