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
        assert set(catalog["sensor"]) == {d.key for d in DESCRIPTIONS}
        assert set(catalog["sensor"]["discovery"]["state"]) == {
            state.value for state in EvidenceState
        }
