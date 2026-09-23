"""Generic connector feedback, independent of vendor identity or references."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_control_runtime import REFERENCE, measured, physical_report
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected

from custom_components.wallbox_manager.control.capabilities import CapabilityResolver
from custom_components.wallbox_manager.control.reference import ConfiguredReference
from custom_components.wallbox_manager.core.events import StationIdentity
from custom_components.wallbox_manager.core.models import ConnectorId, Phase, PhaseMode
from custom_components.wallbox_manager.protocols.ocpp.v21.control_runtime import (
    create_control_runtime,
)


@pytest.fixture
async def reference(manual):
    _, bound, peer, _, _, server = manual
    live = bound.adapter
    state = live.runtime.get(bound.target.station)
    live.runtime._publish(replace(state, protocol_version="2.1"))
    source = CapabilityResolver(live.runtime, ConfiguredReference(REFERENCE))
    control = create_control_runtime(live.runtime, server, source)
    return bound, peer, source, control


@pytest.mark.parametrize(
    "value,count", [("Rxx", 1), ("RST", 3), ("", None), ("xxx", None), ("RTS", None)]
)
async def test_physical_mapping(reference, value, count):
    bound, peer, source, _ = reference
    await physical_report(peer, value)
    mode = source.current_mode(bound.target)
    assert (mode.count if mode else None) == count
    assert (
        bound.adapter.runtime.get(bound.target.station).physical_phases[0].scope
        == bound.target
    )


async def test_unknown_invalidates_known(reference):
    bound, peer, source, _ = reference
    await physical_report(peer, "RST")
    await physical_report(peer, "")
    assert source.current_mode(bound.target) is None
    assert (
        source.phase_operation_evidence(
            source.capabilities(bound.target), PhaseMode((Phase.L1,))
        )
        is None
    )


async def test_stale_and_out_of_order(reference):
    bound, peer, source, _ = reference
    await physical_report(peer, "Rxx")
    old = bound.adapter.runtime.get(bound.target.station).physical_phases[0]
    await physical_report(peer, "RST", at=old.observed_at - timedelta(seconds=1))
    assert source.current_mode(bound.target).count == 1
    state = bound.adapter.runtime.get(bound.target.station)
    expired = replace(
        old,
        observed_at=datetime.now(UTC) - timedelta(seconds=10),
        valid_until=datetime.now(UTC) - timedelta(seconds=5),
    )
    bound.adapter.runtime._publish(replace(state, physical_phases=(expired,)))
    assert source.current_mode(bound.target) is None


@pytest.mark.parametrize("reset", ["boot", "reconnect"])
async def test_generation_reset_and_old_replay(reference, reset):
    bound, peer, source, _ = reference
    live = bound.adapter
    await physical_report(peer, "RST")
    old = live.runtime.get(bound.target.station).physical_phases[0]
    token = live.token
    if reset == "boot":
        live.token = live.runtime.boot(token, StationIdentity("Other", "Other"))
    else:
        live.runtime.disconnect(token)
        live.token = live.runtime.connect(
            token.station, protocol="ocpp", protocol_version="2.1"
        )
    assert source.current_mode(bound.target) is None
    assert not live.runtime.observe_physical_phase(token, old)
    await physical_report(peer, "RST", at=old.observed_at)
    assert source.current_mode(bound.target) is None
    await physical_report(peer, "Rxx")
    assert source.current_mode(bound.target).count == 1


async def test_no_reference_or_vendor_gate(reference):
    bound, peer, _, _ = reference
    live = bound.adapter
    live.token = live.runtime.boot(live.token, StationIdentity("Any", "Any"))
    source = CapabilityResolver(live.runtime)
    await physical_report(peer, "Rxx")
    assert source.current_mode(bound.target).count == 1
    assert source.capabilities(bound.target) is None


async def test_exact_connector_scope(reference):
    bound, peer, source, _ = reference
    await physical_report(
        peer,
        "RST",
        component={"name": "Connector", "evse": {"id": 1, "connector_id": 2}},
    )
    assert source.current_mode(bound.target) is None
    assert source.current_mode(ConnectorId(bound.target.evse, "2")).count == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"variable": {"name": "SupplyPhases"}},
        {"component": {"name": "EVSE", "evse": {"id": 1}}},
        {"timestamp": "2026-01-01T00:00:00"},
        {"event_notification_type": "CustomMonitor"},
    ],
)
async def test_invalid_scope_or_evidence(reference, changes):
    bound, peer, source, _ = reference
    await physical_report(peer, "RST", **changes)
    assert source.current_mode(bound.target) is None


async def test_inactive_relay_does_not_retain(reference):
    bound, peer, source, control = reference
    await physical_report(peer, "RST")
    result = await control.change(bound.target, target_w=4000, allowed=True)
    assert result.status.value == "applied"
    assert control.intent(bound.target).solver_result.point.mode.count == 1
    assert source.current_mode(bound.target).count == 3
    await control.change(bound.target, allowed=False)
    assert control.intent(bound.target).request.target_w == 4000
    measured(bound.adapter.runtime, bound.adapter.token, bound.target)
    await control.change(bound.target, allowed=True)
    assert control.intent(bound.target).solver_result.point.mode.count == 1


async def test_retention_uses_fresh_positive_measurement(reference):
    from fractions import Fraction

    from custom_components.wallbox_manager.core.telemetry import (
        Channel,
        Observation,
        Quantity,
        State,
    )

    bound, peer, _, control = reference
    runtime = bound.adapter.runtime
    await physical_report(peer, "RST")
    peer.enabled = True
    await bound.read_enabled()
    control.restore(bound.target, target_w=4000, allowed=True)
    now = datetime.now(UTC)
    runtime.observe(
        bound.token,
        (
            Observation(
                Channel(bound.target, Quantity.CHARGING_STATE),
                State.CHARGING,
                now,
                now,
                None,
                "test",
            ),
            Observation(
                Channel(bound.target, Quantity.POWER),
                Fraction(4140),
                now,
                now,
                now + timedelta(seconds=60),
                "test",
            ),
        ),
    )
    await control.change(bound.target, target_w=4000)
    assert control.intent(bound.target).solver_result.point.mode.count == 3
    later = datetime.now(UTC)
    runtime.observe(
        bound.token,
        (
            Observation(
                Channel(bound.target, Quantity.POWER),
                Fraction(0),
                later,
                later,
                later + timedelta(seconds=60),
                "test",
            ),
        ),
    )
    await control.change(bound.target, target_w=4000)
    assert control.intent(bound.target).solver_result.point.mode.count == 1


async def test_zero_target_does_not_send_positive_profile(reference):
    bound, peer, _, control = reference
    await physical_report(peer, "Rxx")
    await control.change(bound.target, allowed=True, target_w=0)
    assert control.intent(bound.target).status == "zero_current_unverified"
    assert not peer.requests and not peer.permissions
