"""Normalized sensors retain actual scope and stable offline identity."""

from datetime import UTC, datetime

from homeassistant.helpers import entity_registry as er
from test_electrical_capabilities import CONNECTOR, STATION, inventory
from test_ha_diagnostics import diagnostics as diagnostics

from custom_components.wallbox_manager.core.capabilities import (
    CapabilityEvidence,
    EvidenceState,
)
from custom_components.wallbox_manager.protocols.ocpp.v21.capabilities import (
    parse_capabilities,
)


async def test_normalized_sensor_scopes_and_offline_identity(diagnostics):
    hass, _, runtime, _, setup, unload = diagnostics
    token = runtime.connect(STATION, protocol="ocpp", protocol_version="2.1")
    proof = CapabilityEvidence(EvidenceState.VERIFIED, "inventory", datetime.now(UTC))
    runtime.discover(
        token,
        discovery=proof,
        charging_schedule=proof,
        connectors=(CONNECTOR,),
        electrical=parse_capabilities(STATION, inventory()),
    )
    await hass.async_block_till_done()

    def entries():
        return [
            e
            for e in er.async_get(hass).entities.values()
            if ":capability:" in e.unique_id
        ]

    known = entries()
    assert len(known) == 7
    assert not any("maximum_current_2" in e.unique_id for e in known)
    for entry in known:
        if entry.translation_key in (
            "supported_phases",
            "minimum_current",
            "current_step",
            "phase_switching",
        ):
            assert ',"4",null,' in entry.unique_id
        else:
            assert ',"4","7",' in entry.unique_id
    ids = {e.unique_id for e in known}
    runtime.disconnect(token)
    await hass.async_block_till_done()
    assert {e.unique_id for e in entries()} == ids
    for entry in known:
        assert hass.states.get(entry.entity_id).state == "unavailable"
    await unload()
    await setup()
    assert {e.unique_id for e in entries()} == ids
