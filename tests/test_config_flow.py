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
        assert fields["reference_verified"]["type"] == "boolean"
        assert fields["reference_min_a"]["type"] == "float"
        for key in ("reference_vendor", "reference_model", "reference_serial"):
            assert fields[key]["type"] == "string"
        result = await flow.async_step_init({"reference_verified": True})
        assert result["errors"] == {"base": "invalid_reference"}
        result = await flow.async_step_init({"reference_verified": False})
        assert result["type"] == "create_entry" and result["data"] == {}
    finally:
        await hass.async_stop()


@pytest.mark.parametrize(
    "change",
    [
        {"reference_firmware": ""},
        {"reference_station_id": ""},
        {"reference_min_a": 20},
        {"reference_min_a": 6.5},
        {"limit_1a": -1},
        {"reference_max_3a": float("nan")},
    ],
)
def test_reference_options_require_explicit_valid_evidence(change):
    from test_control_runtime import REFERENCE

    from custom_components.wallbox_manager.config_flow import validate_reference_options

    with pytest.raises(ValueError):
        validate_reference_options({**REFERENCE, **change})


def test_reference_verification_and_limits_are_separate():
    from test_control_runtime import REFERENCE

    from custom_components.wallbox_manager.config_flow import validate_reference_options

    data = validate_reference_options({**REFERENCE, "limit_1a": 7.5})
    assert data["reference_max_1a"] == 16
    assert data["limit_1a"] == 7.5


@pytest.mark.parametrize("key", ["reference_vendor", "reference_model"])
@pytest.mark.parametrize("value", [None, "", " ", " padded "])
def test_identity_attestation_required(key, value):
    from test_control_runtime import REFERENCE

    from custom_components.wallbox_manager.config_flow import validate_reference_options

    options = dict(REFERENCE)
    if value is None:
        options.pop(key)
    else:
        options[key] = value
    with pytest.raises(ValueError):
        validate_reference_options(options)


def test_serial_attestation_is_optional():
    from test_control_runtime import REFERENCE

    from custom_components.wallbox_manager.config_flow import validate_reference_options

    options = {k: v for k, v in REFERENCE.items() if k != "reference_serial"}
    assert validate_reference_options(options) == options
