"""Per-field discovery, fallback precedence and conservative envelope construction."""

from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction

import pytest

from custom_components.wallbox_manager.control.capabilities import CapabilityResolver
from custom_components.wallbox_manager.control.reference import ConfiguredReference
from custom_components.wallbox_manager.core.capabilities import (
    CapabilityEvidence,
    EvidenceState,
)
from custom_components.wallbox_manager.core.electrical import resolve_capabilities
from custom_components.wallbox_manager.core.events import StationIdentity
from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.protocols.ocpp.v21.capabilities import (
    parse_capabilities,
)
from custom_components.wallbox_manager.runtime import Runtime

STATION = StationId("any-station")
EVSE = EvseId(STATION, "4")
CONNECTOR = ConnectorId(EVSE, "7")


def row(name, value, *, connector=False):
    return {
        "component": {
            "name": "Connector" if connector else "EVSE",
            "evse": {"id": 4, **({"connector_id": 7} if connector else {})},
        },
        "variable": {"name": name},
        "variable_attribute": [{"type": "Actual", "value": value}],
    }


def inventory():
    return [
        row("SupportedPhaseModes", "1,3"),
        row("MinimumCurrent", "6.25"),
        row("CurrentStep", "0.125"),
        row("PhaseSwitchingSupported", "true"),
        row("MaximumCurrent1Phase", "20.5", connector=True),
        row("MaximumCurrent3Phase", "27.25", connector=True),
        row("ChargingEnableDisableSupported", "true", connector=True),
    ]


def test_complete_decimal_inventory():
    caps = parse_capabilities(STATION, inventory())
    values = {c.key: c.value for c in caps}
    assert values == {
        "supported_phases": (1, 3),
        "minimum_current": Fraction(25, 4),
        "current_step": Fraction(1, 8),
        "phase_switching": True,
        "maximum_current_1": Fraction(41, 2),
        "maximum_current_3": Fraction(109, 4),
        "enable_disable": True,
    }
    assert all(c.evidence.state == EvidenceState.VERIFIED for c in caps)
    assert all(
        c.scope
        == (
            CONNECTOR
            if c.key.startswith("maximum") or c.key == "enable_disable"
            else EVSE
        )
        for c in caps
    )


def test_two_phase_only_when_reported():
    rows = [
        row("SupportedPhaseModes", "1,2,3"),
        row("MaximumCurrent2Phase", "12.75", connector=True),
    ]
    caps = parse_capabilities(STATION, rows)
    assert caps[0].value == (1, 2, 3)
    assert caps[1].value == Fraction(51, 4)
    assert len(caps) == 2


@pytest.mark.parametrize(
    "name,value",
    [
        ("MinimumCurrent", "0"),
        ("CurrentStep", "-0.5"),
        ("CurrentStep", "NaN"),
        ("CurrentStep", "inf"),
        ("SupportedPhaseModes", "1,1"),
        ("SupportedPhaseModes", "1,4"),
        ("SupportedPhaseModes", ""),
        ("PhaseSwitchingSupported", "maybe"),
    ],
)
def test_invalid_fails_closed(name, value):
    caps = parse_capabilities(STATION, [row(name, value)])
    assert caps[0].value is None
    assert caps[0].evidence.state == EvidenceState.DEGRADED


def test_duplicate_conflict_and_scope():
    caps = parse_capabilities(
        STATION, [row("MinimumCurrent", "6"), row("MinimumCurrent", "7")]
    )
    assert caps[0].value is None
    invalid = row("MinimumCurrent", "6", connector=True)
    assert parse_capabilities(STATION, [invalid]) == ()
    invalid = row("MinimumCurrent", "6")
    invalid["variable"]["instance"] = "other"
    assert parse_capabilities(STATION, [invalid]) == ()
    assert (
        parse_capabilities(
            STATION,
            [
                {
                    "component": {"name": "SmartChargingCtrlr"},
                    "variable": {"name": "Available"},
                    "variable_attribute": [{"value": "true"}],
                }
            ],
        )
        == ()
    )


def fallback(**changes):
    return ConfiguredReference(
        {
            "reference_station_id": STATION.value,
            "reference_evse_id": 4,
            "reference_connector_id": 7,
            "reference_phases": "1,2,3",
            "reference_min_a": "6",
            "reference_step_a": "0.5",
            "reference_max_1a": "32",
            "reference_max_2a": "32",
            "reference_max_3a": "32",
            "reference_phase_switching": True,
            **changes,
        }
    )


def test_ocpp_wins_and_reference_fills_each_missing_field():
    observed = parse_capabilities(
        STATION, [r for r in inventory() if r["variable"]["name"] != "CurrentStep"]
    )
    resolved = resolve_capabilities(
        observed, fallback().capabilities(CONNECTOR, datetime.now(UTC))
    )
    fields = {c.key: c for c in resolved}
    assert fields["supported_phases"].value == (1, 3)
    assert fields["minimum_current"].value == Fraction(25, 4)
    assert fields["maximum_current_1"].value == Fraction(41, 2)
    assert fields["current_step"].value == Fraction(1, 2)
    assert fields["current_step"].evidence.source == "configured_operator_fallback"
    assert "maximum_current_2" not in fields


def test_explicit_false_and_invalid_override_reference():
    rows = [row("PhaseSwitchingSupported", "false"), row("CurrentStep", "0")]
    caps = resolve_capabilities(
        parse_capabilities(STATION, rows),
        fallback().capabilities(CONNECTOR, datetime.now(UTC)),
    )
    fields = {c.key: c for c in caps}
    assert fields["phase_switching"].value is False
    assert fields["current_step"].value is None


def test_maximum_below_minimum_unresolved():
    rows = inventory() + [row("MaximumCurrent1Phase", "2", connector=True)]
    caps = resolve_capabilities(parse_capabilities(STATION, rows), ())
    assert next(c for c in caps if c.key == "maximum_current_1").value is None
    rows = [
        row("SupportedPhaseModes", "1"),
        row("MinimumCurrent", "10"),
        row("MaximumCurrent1Phase", "6", connector=True),
    ]
    caps = resolve_capabilities(parse_capabilities(STATION, rows), ())
    assert next(c for c in caps if c.key == "maximum_current_1").value is None


def test_generation_and_mapping_fences():
    runtime = Runtime()
    token = runtime.connect(STATION, protocol="ocpp", protocol_version="2.1")
    proof = CapabilityEvidence(EvidenceState.VERIFIED, "inventory", datetime.now(UTC))
    assert runtime.discover(
        token,
        discovery=proof,
        charging_schedule=replace(proof, state=EvidenceState.ADVERTISED),
        connectors=(CONNECTOR,),
        electrical=parse_capabilities(STATION, inventory()),
    )
    source = CapabilityResolver(runtime)
    # Counts now identify canonical EVSE-local modes without a grid mapping.
    caps = source.capabilities(CONNECTOR)
    assert [e.mode.count for e in caps.envelopes] == [1, 3]
    runtime.boot(token, StationIdentity("Generic", "Generic"))
    assert source.capabilities(CONNECTOR) is None
    assert not runtime.discover(
        token,
        discovery=proof,
        charging_schedule=proof,
        electrical=parse_capabilities(STATION, inventory()),
    )
    assert runtime.get(STATION).connectors == (CONNECTOR,)


def test_evse_reference_shared_but_connector_maximum_not_broadened():
    refs = fallback().capabilities(ConnectorId(EVSE, "8"), datetime.now(UTC))
    assert {c.key for c in refs} == {
        "supported_phases",
        "minimum_current",
        "current_step",
        "phase_switching",
    }
    assert all(c.scope == EVSE for c in refs)


async def test_complete_inventory_and_boot_fencing_over_wire():
    import asyncio

    from test_ocpp_bidirectional import paired
    from test_ocpp_transport import state_when

    async with paired() as (server, station):
        station.emit_report = False
        await station.boot()
        await asyncio.wait_for(station.first_request.wait(), 1)
        request = station.requests[-1]

        async def report(request_id, rows, seq=0, tbc=False):
            await station.call(
                station._call.NotifyReport(
                    request_id=request_id,
                    generated_at=datetime.now(UTC).isoformat(),
                    seq_no=seq,
                    tbc=tbc,
                    report_data=rows,
                ),
                suppress=False,
            )

        rows = inventory()
        await report(request, rows[:3], tbc=True)
        assert not server.runtime.stations[0].electrical
        await report(request, rows[3:], seq=1)
        state = await state_when(server.runtime, lambda s: len(s.electrical) == 7)
        assert state.electrical[1].value == Fraction(25, 4)
        station.first_request.clear()
        await station.boot()
        await asyncio.wait_for(station.first_request.wait(), 1)
        assert not server.runtime.stations[0].electrical
        await report(request, rows)
        assert not server.runtime.stations[0].electrical
        await report(station.requests[-1], rows)
        state = await state_when(server.runtime, lambda s: len(s.electrical) == 7)
        assert state.token.boot_generation == 2


def test_partial_maximum_keeps_independent_evidence_without_inventing_mode():
    observed = parse_capabilities(
        STATION, [row("MaximumCurrent2Phase", "17.5", connector=True)]
    )
    resolved = resolve_capabilities(observed, ())
    assert len(resolved) == 1 and resolved[0].value == Fraction(35, 2)
    assert resolved[0].key == "maximum_current_2"


@pytest.mark.parametrize("counts", [(1, 3), (1, 2, 3)])
def test_count_only_discovery_builds_canonical_local_envelopes(counts):
    from custom_components.wallbox_manager.core.models import Phase

    runtime = Runtime()
    token = runtime.connect(STATION, protocol="ocpp", protocol_version="2.1")
    rows = [r for r in inventory() if r["variable"]["name"] != "SupportedPhaseModes"]
    rows += [row("SupportedPhaseModes", ",".join(map(str, counts)))]
    if 2 in counts:
        rows += [row("MaximumCurrent2Phase", "16.5", connector=True)]
    proof = CapabilityEvidence(EvidenceState.VERIFIED, "inventory", datetime.now(UTC))
    runtime.discover(
        token,
        discovery=proof,
        charging_schedule=proof,
        connectors=(CONNECTOR,),
        electrical=parse_capabilities(STATION, rows),
    )
    caps = CapabilityResolver(runtime).capabilities(CONNECTOR)
    assert [e.mode.phases for e in caps.envelopes] == [tuple(Phase)[:n] for n in counts]
    expected = {1: Fraction("20.5"), 2: Fraction("16.5"), 3: Fraction("27.25")}
    assert {e.mode.count: e.max_current_a for e in caps.envelopes} == {
        n: expected[n] for n in counts
    }
