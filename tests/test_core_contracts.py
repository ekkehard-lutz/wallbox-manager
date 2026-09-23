"""Validation, immutability and protocol independence of the public contracts."""

import dataclasses
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from custom_components.wallbox_manager.control.requests import Direction, PowerRequest
from custom_components.wallbox_manager.core.capabilities import (
    CapabilityOverride,
    ChargingEnvelope,
    CurrentLimit,
    EvidenceState,
)
from custom_components.wallbox_manager.core.models import (
    ConnectorId,
    EvseId,
    Phase,
    PhaseMode,
    StationId,
)
from custom_components.wallbox_manager.solver.operating_point import (
    OperatingPoint,
    Reason,
    ResultStatus,
    SolverResult,
)


def test_hierarchical_identity():
    a, b = StationId("a"), StationId("b")
    first, second = EvseId(a, "2"), EvseId(a, "3")
    assert first != second != EvseId(b, "3")
    assert ConnectorId(first, "7") != ConnectorId(second, "7")
    assert ConnectorId(first, "7") != ConnectorId(first, "9")
    assert len({a, first, ConnectorId(first, "7")}) == 3


@pytest.mark.parametrize("value", ["", " ", " a", "a ", None, 1, True])
def test_invalid_identity(value):
    with pytest.raises(ValueError):
        StationId(value)
    with pytest.raises(ValueError):
        EvseId(StationId("a"), value)
    with pytest.raises(ValueError):
        ConnectorId(EvseId(StationId("a"), "b"), value)


def test_invalid_identity_parent():
    with pytest.raises(ValueError):
        EvseId("a", "b")
    with pytest.raises(ValueError):
        ConnectorId(StationId("a"), "b")


@pytest.mark.parametrize("state", list(EvidenceState))
def test_evidence_states_and_metadata(state, capabilities, evidence):
    record = dataclasses.replace(evidence, state=state, reason="probe_timeout")
    snapshot = dataclasses.replace(capabilities, stop=record, firmware=None)
    assert snapshot.stop.state == state
    assert snapshot.stop.reason == "probe_timeout"
    assert (
        snapshot.revision,
        snapshot.connection_generation,
        snapshot.boot_generation,
    ) == (7, 4, 2)
    assert snapshot.firmware is None


@pytest.mark.parametrize(
    "field", ["revision", "connection_generation", "boot_generation"]
)
@pytest.mark.parametrize("value", [-1, 1.5, True])
def test_invalid_snapshot_generation(field, value, capabilities):
    with pytest.raises(ValueError):
        dataclasses.replace(capabilities, **{field: value})


def test_snapshot_scopes_and_duplicates(capabilities):
    dataclasses.replace(capabilities, scope=capabilities.scope.station)
    dataclasses.replace(capabilities, scope=ConnectorId(capabilities.scope, "8"))
    with pytest.raises(ValueError):
        dataclasses.replace(capabilities, envelopes=capabilities.envelopes * 2)


def test_override_is_separate(capabilities):
    override = CapabilityOverride("stop", True, "configuration", "operator_setting")
    snapshot = dataclasses.replace(capabilities, overrides=[override])
    assert snapshot.stop == capabilities.stop
    assert snapshot.overrides == (override,)
    with pytest.raises(ValueError):
        dataclasses.replace(override, reason="")


def test_envelopes_are_independent(capabilities):
    one, three = capabilities.envelopes
    assert (
        one.mode.count,
        one.min_current_a,
        one.max_current_a,
        one.current_step_a,
    ) == (1, 6, 32, 1)
    assert (
        three.mode.count,
        three.min_current_a,
        three.max_current_a,
        three.current_step_a,
    ) == (3, 6, 16, 1)


@pytest.mark.parametrize("field", ["min_current_a", "max_current_a", "current_step_a"])
@pytest.mark.parametrize(
    "value", [0, -1, float("nan"), float("inf"), float("-inf"), True]
)
def test_invalid_envelope_numbers(field, value, capabilities):
    with pytest.raises(ValueError):
        dataclasses.replace(capabilities.envelopes[0], **{field: value})


def test_invalid_range_and_non_aligned_max(one, evidence):
    with pytest.raises(ValueError):
        ChargingEnvelope(one, 10, 6, 1, evidence)
    envelope = ChargingEnvelope(one, 6, 7, 2, evidence)
    assert envelope.max_current_a == 7  # Only 6 A is reachable.
    with pytest.raises(ValueError):
        CurrentLimit(one, 5, 4, "site")
    with pytest.raises(ValueError):
        CurrentLimit(one, -1, 4, "site")


@pytest.mark.parametrize("phases", [(), (Phase.L1, Phase.L1), ("l1",), (None,)])
def test_invalid_phase_mapping(phases):
    with pytest.raises(ValueError):
        PhaseMode(phases)


def test_mapping_is_canonical():
    assert PhaseMode((Phase.L3, Phase.L1)) == PhaseMode((Phase.L1, Phase.L3))


@pytest.mark.parametrize(
    "value", [-1, float("inf"), float("nan"), Decimal("NaN"), True, "5"]
)
def test_invalid_request_target(value):
    with pytest.raises(ValueError):
        PowerRequest(value, Direction.DOWN, True)


def test_request_flags_and_exact_numbers():
    with pytest.raises(ValueError):
        PowerRequest(1, "down", True)
    with pytest.raises(ValueError):
        PowerRequest(1, Direction.DOWN, 1)
    with localcontext() as ctx:
        ctx.prec = 2
        assert PowerRequest(
            Decimal("123.456"), Direction.DOWN, True
        ).target_w == Fraction(15432, 125)
    assert PowerRequest(0.1, Direction.UP, False).target_w == Fraction(1, 10)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_voltage(value, voltage):
    with pytest.raises(ValueError):
        dataclasses.replace(voltage.phases[0], voltage_v=value)


def test_voltage_and_evidence_dates(now, voltage, evidence):
    with pytest.raises(ValueError):
        dataclasses.replace(voltage.phases[0], valid_until=now)
    with pytest.raises(ValueError):
        dataclasses.replace(evidence, observed_at=now.replace(tzinfo=None))
    with pytest.raises(ValueError):
        dataclasses.replace(voltage, phases=voltage.phases * 2)
    with pytest.raises(ValueError):
        dataclasses.replace(voltage, connection_generation=-1)
    assert (
        voltage.active_voltages(PhaseMode(tuple(Phase)), now + timedelta(seconds=30))
        is None
    )


def test_deep_immutability(capabilities, voltage):
    envelopes = list(capabilities.envelopes)
    snapshot = dataclasses.replace(capabilities, envelopes=envelopes)
    envelopes.clear()
    assert len(snapshot.envelopes) == 2
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.revision = 8
    phases = list(voltage.phases)
    observation = dataclasses.replace(voltage, phases=phases)
    phases.clear()
    assert len(observation.phases) == 3
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.phases[0].voltage_v = 240


def test_off_has_no_zero_amp_command():
    off = OperatingPoint.off()
    assert not off.charging and off.current_a is None and off.mode is None
    with pytest.raises(ValueError):
        dataclasses.replace(off, current_a=0)


def test_point_requires_consistent_electrical_basis(one):
    point = OperatingPoint(True, one, 6, 1380, (230,))
    for change in (
        {"current_a": 0},
        {"offered_power_w": 1400},
        {"phase_voltages_v": ()},
    ):
        with pytest.raises(ValueError):
            dataclasses.replace(point, **change)


def test_deferred_boundary(one):
    point = OperatingPoint(True, one, 6, 1380, (230,))
    result = SolverResult(
        ResultStatus.DEFERRED,
        Reason.TRANSITION_RESTRICTED,
        suggested=point,
        retry_at_monotonic=123.5,
    )
    assert result.point is None
    assert result.suggested == point
    assert result.retry_at_monotonic == Fraction(247, 2)
    with pytest.raises(ValueError):
        dataclasses.replace(result, retry_at_monotonic=None)
    with pytest.raises(ValueError):
        dataclasses.replace(result, point=point)
    with pytest.raises(ValueError):
        dataclasses.replace(result, suggested=None)


def test_invalid_result_contract():
    with pytest.raises(ValueError):
        SolverResult(ResultStatus.FEASIBLE, Reason.SELECTED)
    with pytest.raises(ValueError):
        SolverResult(ResultStatus.FEASIBLE, Reason.SELECTED, OperatingPoint.off())
    with pytest.raises(ValueError):
        SolverResult(ResultStatus.UNREACHABLE, Reason.NO_ELIGIBLE_MODE, min_power_w=1)


def test_imports_without_site_packages():
    subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            (
                "import sys; "
                "from custom_components.wallbox_manager.solver.power import solve; "
                "assert not any(n.split('.')[0] in ('homeassistant', 'ocpp') "
                "for n in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
