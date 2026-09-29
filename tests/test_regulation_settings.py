"""Central options preserve beta.13 values and use native HA sections."""

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


def test_defaults():
    assert migrate_regulation({}, {}) == DEFAULTS


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
