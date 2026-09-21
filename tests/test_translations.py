"""Check the integration's required language catalogs stay usable together."""

import json
from pathlib import Path
from string import Formatter

import pytest

INTEGRATION = Path(__file__).resolve().parents[1] / "custom_components/wallbox_manager"


def flatten_strings(value, prefix=""):
    """Collect message keys and reject invalid catalog leaves."""
    result = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(flatten_strings(item, path))
        else:
            assert isinstance(item, str) and item.strip(), path
            result[path] = item
    return result


@pytest.mark.parametrize("language", ["en", "de"])
def test_translation_keys_and_placeholders_match(language):
    """Every supported locale must supply all messages and format arguments."""
    source = flatten_strings(json.loads((INTEGRATION / "strings.json").read_text()))
    translated = flatten_strings(
        json.loads((INTEGRATION / "translations" / f"{language}.json").read_text())
    )
    assert source
    assert source.keys() == translated.keys()
    formatter = Formatter()
    for key, message in source.items():
        expected = {field for _, field, _, _ in formatter.parse(message) if field}
        actual = {field for _, field, _, _ in formatter.parse(translated[key]) if field}
        assert actual == expected, key


def test_all_diagnostic_names_and_evidence_states_are_translated():
    from custom_components.wallbox_manager.core.capabilities import EvidenceState
    from custom_components.wallbox_manager.sensor import DESCRIPTIONS

    for filename in ("strings.json", "translations/en.json", "translations/de.json"):
        catalog = json.loads((INTEGRATION / filename).read_text())["entity"]
        assert catalog["binary_sensor"]["connected"]["name"]
        assert {d.key for d in DESCRIPTIONS} <= set(catalog["sensor"])
        assert set(catalog["sensor"]["discovery"]["state"]) == {
            state.value for state in EvidenceState
        }


@pytest.mark.parametrize(
    "filename", ["strings.json", "translations/en.json", "translations/de.json"]
)
def test_listener_url_has_no_unclosed_rich_text_tags(filename):
    description = json.loads((INTEGRATION / filename).read_text())["config"]["step"][
        "user"
    ]["description"]
    # ICU rich-text formatting interprets <port>, etc. as opening tags, not
    # literal endpoint placeholders. This URL intentionally contains no markup.
    assert "ws://HA-address:port/station-id" in description
    assert "<" not in description and ">" not in description


def test_all_operational_entity_names_and_states_are_translated():
    from custom_components.wallbox_manager.core.telemetry import STATE_OPTIONS, Quantity

    for filename in ("strings.json", "translations/en.json", "translations/de.json"):
        catalog = json.loads((INTEGRATION / filename).read_text())["entity"]
        for quantity in Quantity:
            assert "{scope}" in catalog["sensor"][quantity.value]["name"]
        for quantity, states in STATE_OPTIONS.items():
            assert set(catalog["sensor"][quantity.value]["state"]) == {
                s.value for s in states
            }
        assert set(catalog["binary_sensor"]) == {"connected", "session_active"}
        assert "session_charging_state" not in catalog["sensor"]
