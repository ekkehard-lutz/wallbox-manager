"""Manual runtime -> solver -> command boundary -> real OCPP simulated peer."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from types import SimpleNamespace

import pytest
from ocpp.exceptions import InternalError
from test_ocpp21_control import connected as connected
from test_ocpp21_control import transaction

from custom_components.wallbox_manager.control.commands import (
    CommandReason,
    CommandStatus,
)
from custom_components.wallbox_manager.control.requests import Direction
from custom_components.wallbox_manager.core.capabilities import (
    CurrentLimit,
    EvidenceState,
)
from custom_components.wallbox_manager.core.models import ConnectorId, Phase, PhaseMode
from custom_components.wallbox_manager.core.telemetry import (
    Channel,
    Observation,
    Quantity,
)
from custom_components.wallbox_manager.protocols.ocpp.v21.control_runtime import (
    create_control_runtime,
)


def measured(runtime, token, target, age=0):
    now = datetime.now(UTC) - timedelta(seconds=age)
    runtime.observe(
        token,
        tuple(
            Observation(
                Channel(target, Quantity(f"voltage_{p.value}")),
                Fraction(230),
                now,
                now,
                now + timedelta(seconds=60),
                "test_meter",
            )
            for p in Phase
        ),
    )


class Source:
    def __init__(self, bound):
        self.bound = bound
        self.snapshot = bound._capabilities()
        self.permitted = ()
        self.mode = None
        self.proof = True

    def capabilities(self, target):
        return self.snapshot if target == self.bound.target else None

    def phase_operation_evidence(self, snapshot, mode):
        return snapshot.envelopes[0].evidence if self.proof else None

    def permission_evidence(self, target):
        return self.snapshot.envelopes[0].evidence if self.snapshot else None

    def limits(self, target):
        return self.permitted

    def current_mode(self, target):
        return self.mode


@pytest.fixture
async def manual(connected):
    bound, peer, wire = connected
    live = bound.adapter
    target = ConnectorId(bound.target, "1")
    snapshot = replace(bound._capabilities(), scope=target)
    bound = live.bind_control(
        target,
        lambda: snapshot,
        phase_operation_evidence=lambda caps, mode: caps.envelopes[0].evidence,
    )
    live.permission_inventory = (
        live.token,
        (
            {
                "component": {
                    "name": "WallboxController",
                    "evse": {"id": 1, "connector_id": 1},
                },
                "variable": {"name": "ChargingEnabled"},
                "variable_attribute": [{"mutability": "ReadWrite"}],
            },
        ),
    )
    source = Source(bound)
    server = SimpleNamespace(
        sessions={bound.target.station: SimpleNamespace(adapter=live)}
    )
    control = create_control_runtime(live.runtime, server, source)
    measured(live.runtime, live.token, bound.target)
    await asyncio.wait_for(transaction(peer, connector=1), 2)
    return control, bound, peer, wire, source, server


@pytest.mark.parametrize("direction", list(Direction))
async def test_intent_direction_and_limits(manual, direction):
    control, bound, peer, _, source, _ = manual
    source.permitted = (CurrentLimit(PhaseMode((Phase.L1,)), 0, 8, "user"),)
    source.snapshot = replace(
        source.snapshot, envelopes=(source.snapshot.envelopes[0],)
    )
    await control.change(bound.target, target_w=1700, allowed=True, direction=direction)
    intent = control.intent(bound.target)
    assert intent.request.direction == direction
    assert intent.command_result.status == CommandStatus.APPLIED
    point = intent.solver_result.point
    assert point.current_a <= 8
    if direction == Direction.DOWN:
        assert point.offered_power_w <= 1700
    if direction == Direction.UP:
        assert point.offered_power_w >= 1700
    assert len(peer.requests) == 1
    power = intent.request.target_w
    await control.change(bound.target, allowed=False)
    assert intent.request.target_w == power
    assert intent.status == "applied"
    assert len(peer.requests) == 1


async def test_default_five_percent(manual):
    control, bound, _, _, source, _ = manual
    source.mode = PhaseMode(tuple(Phase))
    await control.change(bound.target, target_w=4000, allowed=True)
    assert control.intent(bound.target).phase_switch_deviation_pct == 5
    assert control.intent(bound.target).solver_result.point.mode.count == 1
    await control.change(bound.target, phase_switch_deviation_pct=0)
    assert control.intent(bound.target).solver_result.point.mode.count == 1


async def test_fractional_fixed_l2(manual):
    control, bound, peer, _, source, _ = manual
    envelope = replace(
        source.snapshot.envelopes[0],
        mode=PhaseMode((Phase.L2,)),
        current_step_a=Fraction(1, 2),
    )
    source.snapshot = replace(source.snapshot, envelopes=(envelope,))
    source.mode = envelope.mode
    await control.change(bound.target, target_w=1495, allowed=True)
    assert control.intent(bound.target).command_result.status == CommandStatus.APPLIED
    assert peer.requests[0][1]["charging_schedule"][0]["charging_schedule_period"] == [
        {"start_period": 0, "limit": 6.5, "number_phases": 1}
    ]


@pytest.mark.parametrize(
    "missing", ["caps", "unverified", "voltage", "expired", "phase", "session"]
)
async def test_missing_inputs_fail_closed(manual, missing):
    control, bound, peer, wire, source, _ = manual
    if missing == "caps":
        source.snapshot = None
    elif missing == "unverified":
        source.snapshot = replace(
            source.snapshot,
            envelopes=tuple(
                replace(e, evidence=replace(e.evidence, state=EvidenceState.ADVERTISED))
                for e in source.snapshot.envelopes
            ),
        )
    elif missing in ("voltage", "expired"):
        state = bound.adapter.runtime.get(bound.target.station)
        bound.adapter.runtime._publish(replace(state, observations=()))
        if missing == "expired":
            measured(bound.adapter.runtime, bound.token, bound.target, age=120)
    elif missing == "phase":
        source.proof = False
    else:
        await transaction(peer, kind="Ended")
    before = len(wire.sent)
    await control.change(bound.target, target_w=4000, allowed=True)
    assert control.intent(bound.target).status != "applied"
    assert not control.attributes(bound.target)["execution_ready"]
    assert len(wire.sent) == before


@pytest.mark.parametrize(
    "outcome", ["Accepted", "Rejected", "timeout", "disconnect", "protocol"]
)
async def test_results_propagate(manual, outcome):
    control, bound, peer, wire, _, _ = manual
    if outcome == "timeout":
        peer.release.clear()
    elif outcome == "disconnect":
        wire.error = ConnectionError("private")
    elif outcome == "protocol":
        peer.error = InternalError("private")
    else:
        peer.status = outcome
    result = await control.change(bound.target, target_w=4000, allowed=True)
    assert result.status == {
        "Accepted": CommandStatus.APPLIED,
        "Rejected": CommandStatus.TEMPORARILY_REJECTED,
    }.get(outcome, CommandStatus.FAILED)
    assert control.intent(bound.target).command_result == result
    if outcome == "timeout":
        assert result.reason == CommandReason.TIMEOUT
    elif outcome in ("disconnect", "protocol"):
        assert result.reason == CommandReason.COMMUNICATION_ERROR


async def test_restore_and_telemetry_never_dispatch(manual):
    control, bound, peer, wire, _, _ = manual
    before = len(wire.sent)
    control.restore(bound.target, target_w=4000, allowed=True, direction=Direction.UP)
    measured(bound.adapter.runtime, bound.token, bound.target)
    assert len(wire.sent) == before and not peer.requests
    await control.change(bound.target, target_w=5000)
    control.restore(bound.target, target_w=1000)
    assert control.intent(bound.target).request.target_w == 5000


async def test_superseded_in_library_queue(manual):
    control, bound, peer, _, _, _ = manual
    live = bound.adapter
    entered = asyncio.Event()

    class Lock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                entered.set()
            return await super().acquire()

    live._call_lock = Lock()
    await live._call_lock.acquire()
    first = asyncio.create_task(
        control.change(bound.target, target_w=4000, allowed=True)
    )
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(control.change(bound.target, target_w=5000))
    await asyncio.sleep(0)
    live._call_lock.release()
    old, new = await asyncio.gather(first, second)
    assert old.reason == CommandReason.STALE
    assert new.status == CommandStatus.APPLIED
    assert len(peer.requests) == 1
    assert control.intent(bound.target).request.target_w == 5000


async def test_late_accepted_does_not_publish_over_new_intent(manual):
    control, bound, peer, _, _, _ = manual
    peer.release.clear()
    old = asyncio.create_task(control.change(bound.target, target_w=4000, allowed=True))
    await peer.received.wait()
    new = asyncio.create_task(control.change(bound.target, allowed=False))
    await asyncio.sleep(0)
    peer.release.set()
    await new
    assert (await old).reason == CommandReason.STALE
    assert control.intent(bound.target).status == "applied"
    assert control.intent(bound.target).command_result.status == CommandStatus.APPLIED


async def test_solver_off_reaches_unsupported_adapter(manual):
    control, bound, peer, _, source, _ = manual
    source.snapshot = replace(
        source.snapshot,
        stop=replace(source.snapshot.stop, state=EvidenceState.VERIFIED),
    )
    result = await control.change(bound.target, target_w=4000, allowed=False)
    assert result.status == CommandStatus.APPLIED
    assert not peer.requests


REFERENCE = {
    "reference_station_id": "station",
    "reference_evse_id": 1,
    "reference_connector_id": 1,
    "reference_phases": "1,3",
    "reference_modes": "l1;l1,l2,l3",
    "reference_min_a": "6",
    "reference_step_a": "1",
    "reference_max_1a": "20",
    "reference_max_3a": "16",
    "reference_phase_switching": True,
    "reference_enable_disable": True,
}


async def physical_report(peer, value, at=None, **changes):
    from ocpp.v21 import call

    at = (at or datetime.now(UTC)).isoformat()
    event = {
        "event_id": 0,
        "timestamp": at,
        "component": {"name": "Connector", "evse": {"id": 1, "connector_id": 1}},
        "variable": {"name": "PhaseRotation"},
        "actual_value": value,
        "event_notification_type": "HardWiredNotification",
        "trigger": "Periodic",
        **changes,
    }
    await peer.call(
        call.NotifyEvent(generated_at=at, seq_no=0, tbc=False, event_data=[event]),
        suppress=False,
    )


async def test_site_limit_constrains_resolved_offer(manual):
    control, bound, peer, _, source, _ = manual
    mode = source.snapshot.envelopes[0].mode
    source.snapshot = replace(
        source.snapshot, envelopes=(source.snapshot.envelopes[0],)
    )
    source.permitted = (CurrentLimit(mode, 0, 8, "site"),)
    await control.change(bound.target, target_w=4000, allowed=True)
    assert control.intent(bound.target).solver_result.point.current_a == 8
    assert source.snapshot.envelopes[0].max_current_a == 32
    assert len(peer.requests) == 1
    await control.change(bound.target, direction=Direction.UP)
    assert control.intent(bound.target).status == "direction_unreachable"
    assert len(peer.requests) == 1


def test_runtime_imports_without_ha_or_ocpp():
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys; import custom_components.wallbox_manager.control.runtime; "
            "assert not any(n.split('.')[0] in ('homeassistant', 'ocpp') "
            "for n in sys.modules)",
        ],
        check=True,
        capture_output=True,
    )
