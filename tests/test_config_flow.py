"""Frontend serialization and semantic listener validation on HA 2026.8."""

import json
from types import SimpleNamespace

import pytest
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView

from custom_components.wallbox_manager.config_flow import WallboxManagerConfigFlow


@pytest.mark.parametrize("submitted", [None, {"host": "bad", "port": 9000}])
async def test_config_form_frontend_serialization(submitted):
    """Use HA's HTTP flow response serializer, not just Python schema validation."""
    result = await WallboxManagerConfigFlow().async_step_user(submitted)
    payload = FlowManagerIndexView(None)._prepare_result_json(result)
    fields = {field["name"]: field for field in payload["data_schema"]}
    assert fields["host"]["type"] == "string"
    assert fields["host"]["default"] == "0.0.0.0"
    assert fields["port"]["type"] == "integer"
    assert fields["port"]["valueMin"] == 1
    assert fields["port"]["valueMax"] == 65535
    assert json.loads(json.dumps(payload))["type"] == "form"
    if submitted is not None:
        assert payload["errors"] == {"base": "invalid_listener"}


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", "127.0.0.1"),
        ("0.0.0.0", "0.0.0.0"),
        ("::", "::"),
        ("::1", "::1"),
        ("2001:0db8::1", "2001:db8::1"),
    ],
)
@pytest.mark.parametrize("port", [1, 65535])
async def test_valid_listener_addresses(host, expected, port):
    flow = WallboxManagerConfigFlow()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda *a: [])
    )
    result = await flow.async_step_user({"host": host, "port": port})
    assert result["type"] == "create_entry"
    assert result["data"] == {"host": expected, "port": port}
    assert isinstance(result["data"]["port"], int)


@pytest.mark.parametrize(
    "host,port",
    [
        ("localhost", 9000),
        ("256.0.0.1", 9000),
        ("[::1]", 9000),
        ("", 9000),
        (123, 9000),
        ("::1", 0),
        ("::1", 65536),
        ("::1", "bad"),
    ],
)
async def test_invalid_listener_returns_translated_error(host, port):
    result = await WallboxManagerConfigFlow().async_step_user(
        {"host": host, "port": port}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_listener"}


async def test_duplicate_listener_port_is_rejected():
    flow = WallboxManagerConfigFlow()
    existing = SimpleNamespace(
        entry_id="existing", options={}, data={"host": "0.0.0.0", "port": 9000}
    )
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda *a: [existing])
    )
    with pytest.raises(AbortFlow, match="already_configured"):
        await flow.async_step_user({"host": "::", "port": 9000})


async def test_reference_options_form_serializes():
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.core import HomeAssistant
    from test_ha_lifecycle import entry

    from custom_components.wallbox_manager.config_flow import ReferenceOptionsFlow

    hass = HomeAssistant("/tmp")
    hass.config_entries = ConfigEntries(hass, {})
    config = entry()
    hass.config_entries._entries[config.entry_id] = config
    flow = ReferenceOptionsFlow()
    flow.hass = hass
    flow.handler = config.entry_id
    try:
        result = await flow.async_step_init()
        payload = FlowManagerIndexView(None)._prepare_result_json(result)
        fields = {f["name"]: f for f in payload["data_schema"]}
        assert "reference_verified" not in fields
        assert "reference_min_a" not in fields
        assert "min_soc_speicher" in fields
        assert "soc_speicher_aktuell" in fields
        result = await flow.async_step_init({})
        assert result["type"] == "create_entry" and result["data"] == {}
    finally:
        await hass.async_stop()


@pytest.mark.parametrize(
    "change",
    [
        {"reference_station_id": ""},
        {"reference_min_a": "20"},
        {"reference_step_a": "0"},
        {"reference_max_3a": "nan"},
        {"reference_phases": "1,2"},
    ],
)
def test_invalid_reference(change):
    from test_control_runtime import REFERENCE

    from custom_components.wallbox_manager.config_flow import validate_reference_options

    with pytest.raises(ValueError):
        validate_reference_options({**REFERENCE, **change})


def test_partial_fractional_reference():
    from custom_components.wallbox_manager.config_flow import validate_reference_options

    data = {
        "reference_station_id": "station",
        "reference_evse_id": 2,
        "reference_connector_id": 7,
        "reference_step_a": "0.125",
    }
    assert validate_reference_options(data) == data


@pytest.mark.parametrize("missing", [False, True])
async def test_station_form_only_missing_ocpp_fields(missing):
    from dataclasses import replace

    from homeassistant.config_entries import ConfigEntries
    from homeassistant.core import HomeAssistant
    from test_electrical_capabilities import CONNECTOR, STATION, inventory
    from test_ha_lifecycle import entry

    from custom_components.wallbox_manager.config_flow import WallboxCapabilityFlow
    from custom_components.wallbox_manager.protocols.ocpp.v21.capabilities import (
        parse_capabilities,
    )
    from custom_components.wallbox_manager.runtime import Runtime
    from custom_components.wallbox_manager.station_config import ensure_subentry

    hass = HomeAssistant("/tmp")
    hass.config_entries = ConfigEntries(hass, {})
    config = entry()
    runtime = Runtime()
    runtime.connect(STATION)
    caps = parse_capabilities(STATION, inventory())
    if missing:
        caps = tuple(c for c in caps if c.key != "minimum_current")
    runtime._publish(
        replace(runtime.get(STATION), connectors=(CONNECTOR,), electrical=caps)
    )
    config.runtime_data = SimpleNamespace(state=runtime)
    hass.config_entries._entries[config.entry_id] = config
    subentry = ensure_subentry(hass, config, STATION.value, "4", "7")
    flow = WallboxCapabilityFlow()
    flow.hass, flow.handler = hass, (config.entry_id, "wallbox")
    flow.context = {"source": "reconfigure", "subentry_id": subentry.subentry_id}
    try:
        result = await flow.async_step_reconfigure()
        payload = FlowManagerIndexView(None)._prepare_result_json(result)
        fields = {f["name"] for f in payload["data_schema"]}
        assert fields == ({"reference_min_a"} if missing else set())
        result = await flow.async_step_reconfigure(
            {"reference_min_a": "6"} if missing else {}
        )
        assert result["type"] == "abort"
        assert result["reason"] == "reconfigure_successful"
        assert config.subentries[subentry.subentry_id].data["station"] == STATION.value
    finally:
        await hass.async_stop()
