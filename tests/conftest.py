"""Pure core fixtures; no Home Assistant instance or protocol library needed."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.wallbox_manager.core.capabilities import (
    CapabilityEvidence,
    CapabilitySnapshot,
    ChargingEnvelope,
    EvidenceState,
)
from custom_components.wallbox_manager.core.models import (
    EvseId,
    Phase,
    PhaseMode,
    PhaseVoltage,
    StationId,
    VoltageObservation,
)


@pytest.fixture
def now():
    return datetime(2026, 9, 21, tzinfo=UTC)


@pytest.fixture
def one():
    return PhaseMode((Phase.L1,))


@pytest.fixture
def three():
    return PhaseMode(tuple(Phase))


@pytest.fixture
def evidence(now):
    return CapabilityEvidence(EvidenceState.VERIFIED, "test-discovery", now)


@pytest.fixture
def capabilities(now, one, three, evidence):
    return CapabilitySnapshot(
        EvseId(StationId("station-a"), "evse-b"),
        "firmware-a",
        4,
        2,
        7,
        now,
        "test-discovery",
        (
            ChargingEnvelope(one, 6, 32, 1, evidence),
            ChargingEnvelope(three, 6, 16, 1, evidence),
        ),
        evidence,
    )


@pytest.fixture
def voltage(now, capabilities):
    return VoltageObservation(
        capabilities.scope,
        capabilities.connection_generation,
        tuple(
            PhaseVoltage(phase, 230, now, now + timedelta(seconds=30), "meter")
            for phase in Phase
        ),
    )
