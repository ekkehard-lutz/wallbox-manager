"""Capability maxima initialize intent exactly once, without electrical inference."""

from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction

import pytest
from test_electrical_capabilities import CONNECTOR, STATION, row

from custom_components.wallbox_manager.control.capabilities import CapabilityResolver
from custom_components.wallbox_manager.control.runtime import ControlRuntime
from custom_components.wallbox_manager.core.capabilities import (
    CapabilityEvidence,
    EvidenceState,
)
from custom_components.wallbox_manager.core.models import ConnectorId
from custom_components.wallbox_manager.protocols.ocpp.v21.capabilities import (
    parse_capabilities,
)
from custom_components.wallbox_manager.runtime import Runtime


def forbidden(*args):
    raise AssertionError("initialization/restoration must not dispatch or solve")


@pytest.fixture
def defaults():
    runtime = Runtime()
    token = runtime.connect(STATION, protocol="ocpp", protocol_version="2.1")
    source = CapabilityResolver(runtime)
    control = ControlRuntime(
        runtime, forbidden, forbidden, electrical_capabilities=source.resolved
    )
    yield runtime, token, source, control
    control.close()


def publish(runtime, token, maxima, *, evidence=EvidenceState.VERIFIED):
    rows = [
        row(
            "MaximumCurrent" + (f"{count}Phase" if count else ""),
            str(value),
            connector=True,
        )
        for count, value in maxima.items()
    ]
    capabilities = tuple(
        replace(c, evidence=replace(c.evidence, state=evidence))
        for c in parse_capabilities(STATION, rows)
    )
    proof = CapabilityEvidence(
        EvidenceState.VERIFIED, "complete_inventory", datetime.now(UTC)
    )
    runtime.discover(
        token,
        discovery=proof,
        charging_schedule=proof,
        connectors=(CONNECTOR,),
        electrical=capabilities,
    )
    return capabilities


@pytest.mark.parametrize(
    "maxima,expected",
    [
        ({1: 20, 3: 27}, {1: 20, 2: 27, 3: 27}),
        ({1: 20, 2: 23, 3: 27}, {1: 20, 2: 23, 3: 27}),
        ({0: 32}, {1: 32, 2: 32, 3: 32}),
        ({0: 16, 1: 20, 3: 27}, {1: 20, 2: 16, 3: 27}),
        ({1: 32, 3: 16}, {1: 32, 2: 32, 3: 16}),
        (
            {2: Fraction(41, 6)},
            {1: Fraction(41, 6), 2: Fraction(41, 6), 3: Fraction(41, 6)},
        ),
        ({}, {}),
    ],
)
def test_default_precedence(defaults, maxima, expected):
    runtime, token, _, control = defaults
    before = publish(runtime, token, maxima)
    assert control.intent(CONNECTOR).current_limits == expected
    assert all(
        isinstance(v, Fraction)
        for v in control.intent(CONNECTOR).current_limits.values()
    )
    assert runtime.get(STATION).electrical == before


@pytest.mark.parametrize(
    "state",
    [
        EvidenceState.UNKNOWN,
        EvidenceState.ADVERTISED,
        EvidenceState.DEGRADED,
        EvidenceState.UNSUPPORTED,
    ],
)
def test_untrustworthy_maximum_leaves_unset(defaults, state):
    runtime, token, _, control = defaults
    publish(runtime, token, {0: 32, 1: 20}, evidence=state)
    assert control.intent(CONNECTOR).current_limits == {}


@pytest.mark.parametrize("stored", [0, 16, Fraction(37, 3)])
@pytest.mark.parametrize("first", ["restore", "discovery"])
async def test_stored_and_edited_values_always_win(defaults, stored, first):
    runtime, token, _, control = defaults
    if first == "restore":
        control.restore(CONNECTOR, allowed_current_1p=stored)
    publish(runtime, token, {1: 20, 3: 27})
    control.restore(CONNECTOR, allowed_current_1p=stored)
    await control.change(CONNECTOR, allowed_current_2p=stored)
    # A late HA restore cannot replace an explicit Energy Manager/user edit.
    control.restore(CONNECTOR, allowed_current_2p=99)
    publish(runtime, token, {1: 25, 2: 35, 3: 43})
    assert control.intent(CONNECTOR).current_limits == {1: stored, 2: stored, 3: 27}


def test_reconnect_refresh_and_stale_generation(defaults):
    runtime, token, _, control = defaults
    publish(runtime, token, {1: 20, 3: 27})
    runtime.disconnect(token)
    new = runtime.connect(STATION, protocol="ocpp", protocol_version="2.1")
    publish(runtime, token, {0: 50})
    publish(runtime, new, {1: 25, 3: 43})
    assert control.intent(CONNECTOR).current_limits == {1: 20, 2: 27, 3: 27}


def test_capabilities_before_control_startup(defaults):
    runtime, token, source, _ = defaults
    publish(runtime, token, {0: Fraction(129, 4)})
    other = ControlRuntime(
        runtime, forbidden, forbidden, electrical_capabilities=source.resolved
    )
    assert other.intent(CONNECTOR).current_limits == dict.fromkeys(
        (1, 2, 3), Fraction(129, 4)
    )
    other.close()


@pytest.mark.parametrize("maxima", [{0: 32}, {1: 20, 3: 27}])
def test_defaults_create_no_phase_support_or_mapping(defaults, maxima):
    runtime, token, source, control = defaults
    publish(runtime, token, maxima)
    assert control.intent(CONNECTOR).current_limits[2] > 0
    assert source.capabilities(CONNECTOR).envelopes == ()
    assert not any(
        c.key in ("supported_phases", "phase_switching")
        for c in source.resolved(CONNECTOR)
    )
    assert source.current_mode(CONNECTOR) is None
    assert control.intent(ConnectorId(CONNECTOR.evse, "8")).current_limits == {}


def test_generic_discovery_is_explicit_scoped_extension():
    generic = row("MaximumCurrent", "27.125", connector=True)
    parsed = parse_capabilities(STATION, [generic])
    assert parsed[0].key == "maximum_current"
    assert parsed[0].value == Fraction(217, 8)
    assert parsed[0].evidence.state == EvidenceState.VERIFIED
    assert parse_capabilities(STATION, [row("MaximumCurrent", "32")]) == ()
    assert parse_capabilities(STATION, [row("MaxCurrent", "32", connector=True)]) == ()
    generic["variable_characteristics"] = {"unit": "mA"}
    assert parse_capabilities(STATION, [generic])[0].value is None


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "unknown"])
def test_invalid_generic_maximum(value):
    parsed = parse_capabilities(STATION, [row("MaximumCurrent", value, connector=True)])
    assert parsed[0].value is None
    assert parsed[0].evidence.state == EvidenceState.DEGRADED


def test_reference_station_defaults_do_not_add_two_phase_envelope(defaults):
    runtime, token, source, control = defaults
    proof = CapabilityEvidence(
        EvidenceState.VERIFIED, "complete_inventory", datetime.now(UTC)
    )
    rows = [
        row("SupportedPhaseModes", "1,3"),
        row("MinimumCurrent", "6"),
        row("CurrentStep", "1"),
        row("MaximumCurrent1Phase", "20", connector=True),
        row("MaximumCurrent3Phase", "27", connector=True),
    ]
    runtime.discover(
        token,
        discovery=proof,
        charging_schedule=proof,
        connectors=(CONNECTOR,),
        electrical=parse_capabilities(STATION, rows),
    )
    assert control.intent(CONNECTOR).current_limits == {1: 20, 2: 27, 3: 27}
    assert next(
        c.value for c in source.resolved(CONNECTOR) if c.key == "supported_phases"
    ) == (1, 3)
    assert all(e.mode.count != 2 for e in source.capabilities(CONNECTOR).envelopes)
    assert source.current_mode(CONNECTOR) is None
    assert not any(c.key == "phase_switching" for c in source.resolved(CONNECTOR))


async def test_complete_inventory_initializes_after_discovery_only():
    import asyncio

    from test_ocpp_bidirectional import paired
    from test_ocpp_transport import state_when

    from custom_components.wallbox_manager.core.models import EvseId, StationId
    from custom_components.wallbox_manager.protocols.ocpp.v21.control_runtime import (
        create_control_runtime,
    )

    async with paired() as (server, station):
        control = create_control_runtime(server.runtime, server)
        target = ConnectorId(EvseId(StationId("station-a"), "4"), "7")
        try:
            station.emit_report = False
            await station.boot()
            await asyncio.wait_for(station.first_request.wait(), 1)
            request_id = station.requests[-1]
            for sequence, rows, tbc in (
                (0, [row("MaximumCurrent", "32.125", connector=True)], True),
                (1, [], False),
            ):
                await station.call(
                    station._call.NotifyReport(
                        request_id=request_id,
                        generated_at=datetime.now(UTC).isoformat(),
                        seq_no=sequence,
                        tbc=tbc,
                        report_data=rows or None,
                    ),
                    suppress=False,
                )
                if tbc:
                    assert control.intent(target).current_limits == {}
            await state_when(server.runtime, lambda state: bool(state.electrical))
            assert control.intent(target).current_limits == dict.fromkeys(
                (1, 2, 3), Fraction(257, 8)
            )
            assert control.capability_source.capabilities(target).envelopes == ()
            assert station.requests == [request_id]
        finally:
            control.close()
