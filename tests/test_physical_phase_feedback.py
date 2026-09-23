"""Reference hardware feedback, wire schemas, ordering and solver integration."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_control_runtime import REFERENCE, measured, physical_report
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected

from custom_components.wallbox_manager.control.reference_wallbox_stationary import (
    WallboxStationaryReference,
)
from custom_components.wallbox_manager.core.events import StationIdentity
from custom_components.wallbox_manager.core.models import Phase, PhaseMode
from custom_components.wallbox_manager.core.telemetry import (
    Channel,
    Observation,
    Quantity,
)
from custom_components.wallbox_manager.protocols.ocpp.v21.control_runtime import (
    create_control_runtime,
)


@pytest.fixture
async def reference(manual):
    _, bound, peer, _, _, server = manual
    live = bound.adapter
    live.token = live.runtime.boot(
        live.token,
        StationIdentity(
            "Lutz", "Lutz-EVSE-DIN", serial="4C75747A00000001", firmware="test-verified"
        ),
    )
    live.runtime._publish(
        replace(live.runtime.get(bound.target.station), protocol_version="2.1")
    )
    measured(live.runtime, live.token, bound.target)
    source = WallboxStationaryReference(live.runtime, REFERENCE)
    control = create_control_runtime(live.runtime, server, source)
    return bound, peer, source, control


@pytest.mark.parametrize(
    "value,mode",
    [
        ("Rxx", PhaseMode((Phase.L1,))),
        ("RST", PhaseMode(tuple(Phase))),
        ("", None),
        ("xxx", None),
    ],
)
async def test_physical_mapping(reference, value, mode):
    bound, peer, source, _ = reference
    await physical_report(peer, value)
    assert source.current_mode(bound.target) == mode


async def test_unknown_invalidates_known(reference):
    bound, peer, source, control = reference
    await physical_report(peer, "RST")
    await physical_report(peer, "")
    assert source.current_mode(bound.target) is None
    assert (
        source.phase_operation_evidence(
            source.capabilities(bound.target), PhaseMode((Phase.L1,))
        )
        is None
    )
    await control.change(bound.target, target_w=4000, allowed=True)
    assert not peer.requests


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
    await physical_report(peer, "RST", at=datetime.now(UTC) - timedelta(seconds=10))
    assert source.current_mode(bound.target) is None


@pytest.mark.parametrize("reset", ["boot", "reconnect"])
async def test_generation_reset_and_old_replay(reference, reset):
    bound, peer, source, _ = reference
    live = bound.adapter
    await physical_report(peer, "RST")
    old = live.runtime.get(bound.target.station).physical_phases[0]
    token = live.token
    if reset == "boot":
        live.token = live.runtime.boot(
            token, live.runtime.get(bound.target.station).identity
        )
    else:
        live.runtime.disconnect(token)
        assert source.current_mode(bound.target) is None
        live.token = live.runtime.connect(
            token.station, protocol="ocpp", protocol_version="2.1"
        )
    assert source.current_mode(bound.target) is None
    assert not live.runtime.observe_physical_phase(token, old)
    await physical_report(peer, "RST", at=old.observed_at)
    assert source.current_mode(bound.target) is None
    await physical_report(peer, "Rxx")
    assert source.current_mode(bound.target).count == 1


async def test_requested_mode_and_current_never_establish_physical_mode(reference):
    bound, peer, source, control = reference
    control.restore(bound.target, target_w=11040, allowed=True)
    now = datetime.now(UTC)
    bound.adapter.runtime.observe(
        bound.adapter.token,
        tuple(
            Observation(
                Channel(bound.target, Quantity(f"current_{phase.value}")),
                16,
                now,
                now,
                now + timedelta(seconds=60),
                "meter",
            )
            for phase in Phase
        ),
    )
    assert source.current_mode(bound.target) is None
    await control.change(bound.target, allowed=True)
    assert not peer.requests


async def test_real_feedback_reaches_retention_and_transition(reference):
    bound, peer, source, control = reference
    await physical_report(peer, "RST")
    assert (
        await control.change(bound.target, target_w=4000, allowed=True)
    ).status.value == "applied"
    assert control.intent(bound.target).solver_result.point.mode.count == 3
    assert control.attributes(bound.target)["physical_phase_mode"] == ["l1", "l2", "l3"]
    # Outside tolerance: at 1500 W, 3P minimum is too high, so select 1P.
    peer.release.clear()
    peer.received.clear()
    pending = asyncio.create_task(control.change(bound.target, target_w=1500))
    await peer.received.wait()
    assert control.intent(bound.target).solver_result.point.mode.count == 1
    assert source.current_mode(bound.target).count == 3  # Sending is not feedback.
    updated = asyncio.Event()
    unsubscribe = bound.adapter.runtime.subscribe(
        lambda state: (
            updated.set() if source.current_mode(bound.target) is None else None
        )
    )
    unknown_report = asyncio.create_task(physical_report(peer, ""))
    await asyncio.wait_for(updated.wait(), 1)
    unsubscribe()
    peer.release.set()
    assert (await pending).status.value == "applied"
    await unknown_report
    await physical_report(peer, "Rxx")
    assert source.current_mode(bound.target).count == 1


@pytest.mark.parametrize(
    "alteration", ["identity", "scope", "variable", "timestamp", "future"]
)
async def test_untrusted_or_inapplicable_events(reference, alteration):
    bound, peer, source, _ = reference
    changes = {}
    at = None
    if alteration == "identity":
        bound.adapter.token = bound.adapter.runtime.boot(
            bound.adapter.token,
            StationIdentity("Generic", "Generic", firmware="test-verified"),
        )
    elif alteration == "scope":
        changes["component"] = {
            "name": "Connector",
            "evse": {"id": 2, "connector_id": 1},
        }
    elif alteration == "variable":
        changes["variable"] = {"name": "SupplyPhases"}
    elif alteration == "timestamp":
        changes["timestamp"] = "2026-01-01T00:00:00"  # No timezone.
    else:
        at = datetime.now(UTC) + timedelta(seconds=10)
    await physical_report(peer, "RST", at=at, **changes)
    assert source.current_mode(bound.target) is None
    assert not bound.adapter.runtime.get(bound.target.station).physical_phases


@pytest.mark.parametrize("field", ["vendor", "model", "firmware", "serial"])
async def test_runtime_identity_mismatch_blocks_capabilities_and_feedback(
    reference, field
):
    bound, peer, source, _ = reference
    live = bound.adapter
    identity = live.runtime.get(bound.target.station).identity
    live.token = live.runtime.boot(
        live.token, replace(identity, **{field: "different"})
    )
    await physical_report(peer, "RST")
    assert source.capabilities(bound.target) is None
    assert source.current_mode(bound.target) is None
    assert not live.runtime.get(bound.target.station).physical_phases


@pytest.mark.parametrize(
    "key",
    [
        "reference_vendor",
        "reference_model",
        "reference_firmware",
        "reference_station_id",
        "reference_verified",
    ],
)
async def test_missing_attestation_blocks_capabilities_and_feedback(reference, key):
    bound, peer, source, _ = reference
    source.options = {k: v for k, v in REFERENCE.items() if k != key}
    await physical_report(peer, "RST")
    assert source.capabilities(bound.target) is None
    assert not bound.adapter.runtime.get(bound.target.station).physical_phases


async def test_other_configured_station_cannot_supply_feedback(reference):
    bound, peer, source, control = reference
    from custom_components.wallbox_manager.core.models import EvseId, StationId

    source.target = EvseId(StationId("Wallbox01"), "1")
    source.options = {**REFERENCE, "reference_station_id": "Wallbox01"}
    await physical_report(peer, "RST")
    assert source.capabilities(bound.target) is None
    assert not bound.adapter.runtime.get(bound.target.station).physical_phases


async def test_serial_attestation_optional_but_no_reference_means_no_feedback(
    reference,
):
    bound, peer, source, _ = reference
    source.options = {k: v for k, v in REFERENCE.items() if k != "reference_serial"}
    await physical_report(peer, "RST")
    assert source.capabilities(bound.target) is not None
    assert source.current_mode(bound.target).count == 3
    live = bound.adapter
    live.token = live.runtime.boot(
        live.token, live.runtime.get(bound.target.station).identity
    )
    live.runtime.physical_phase_authorized = lambda target: False
    await physical_report(peer, "RST")
    assert not live.runtime.get(bound.target.station).physical_phases


async def test_boot_notification_identity_gates_reference_feedback(reference):
    from ocpp.v21 import call

    bound, peer, source, _ = reference
    await peer.call(
        call.BootNotification(
            charging_station={
                "vendor_name": "Lutz",
                "model": "Lutz-EVSE-DIN",
                "serial_number": "4C75747A00000001",
                "firmware_version": "test-verified",
            },
            reason="PowerUp",
        ),
        suppress=False,
    )
    await physical_report(peer, "RST")
    assert source.capabilities(bound.target) is not None
    assert source.current_mode(bound.target).count == 3
