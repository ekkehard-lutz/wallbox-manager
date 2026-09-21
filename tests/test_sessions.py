"""Pure immutable ledger, accounting, ordering, fencing and restoration contracts."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from custom_components.wallbox_manager.core.events import StationIdentity
from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.core.sessions import (
    ChargingSession,
    SessionEvent,
)
from custom_components.wallbox_manager.core.sessions import SessionEventKind as Kind
from custom_components.wallbox_manager.core.telemetry import (
    Channel,
    Observation,
    Quantity,
    State,
)
from custom_components.wallbox_manager.runtime import Runtime
from custom_components.wallbox_manager.session_ledger import SessionLedger

AT = datetime(2026, 1, 1, tzinfo=UTC)
SCOPE = ConnectorId(EvseId(StationId("station"), "2"), "7")


def event(kind=Kind.STARTED, seconds=0, external="tx", scope=SCOPE, **kwargs):
    return SessionEvent(
        scope, external, kind, AT + timedelta(seconds=seconds), **kwargs
    )


def meter(quantity, value, seconds=0, scope=SCOPE):
    at = AT + timedelta(seconds=seconds)
    return Observation(
        Channel(scope, quantity), value, at, at, at + timedelta(seconds=120), "test"
    )


def test_immutable_model_duration_accounting_and_max():
    ledger = SessionLedger()
    assert ledger.apply(event(meter_wh=1000))
    first = ledger.get(SCOPE)
    with pytest.raises(FrozenInstanceError):
        first.started_at = AT
    ledger.observe(
        (
            meter(Quantity.POWER, 3000, 1),
            meter(Quantity.POWER, 0, 2),
            meter(Quantity.ENERGY, 1600, 2),
        )
    )
    active = ledger.get(SCOPE)
    assert active.active and active.current_power_w == 0
    assert active.max_power_w == 3000 and active.energy_charged_wh == 600
    assert first.energy_charged_wh == 0  # Previous immutable snapshot unchanged.
    assert active.duration(AT + timedelta(seconds=7)) == 7
    assert ledger.apply(event(Kind.ENDED, 10, meter_wh=1800, end_reason="Local"))
    final = ledger.get(SCOPE)
    assert not final.active and final.ended_at == AT + timedelta(seconds=10)
    assert final.duration(AT + timedelta(days=1)) == 10
    assert final.current_power_w == 0 and final.energy_charged_wh == 800
    assert final.energy_start_wh == 1000 and final.energy_end_wh == 1800
    assert final.max_power_w == 3000 and final.end_reason == "Local"
    assert ledger.history() == (final,)


@pytest.mark.parametrize(
    "field",
    [
        "started_at",
        "updated_at",
        "ended_at",
        "energy_at",
        "power_at",
        "state_at",
        "power_valid_until",
    ],
)
def test_reject_naive_timestamps(field):
    session = ChargingSession("id", "tx", SCOPE, AT, AT)
    with pytest.raises(ValueError):
        replace(session, **{field: datetime(2026, 1, 1)})
    with pytest.raises(ValueError):
        replace(event(), at=datetime(2026, 1, 1))


@pytest.mark.parametrize(
    "changes",
    [
        {"ended_at": AT},
        {"updated_at": AT - timedelta(seconds=1)},
        {"energy_charged_wh": Fraction(1)},
        {"current_power_w": -1},
        {"max_power_w": float("nan")},
        {"scope": StationId("bad")},
    ],
)
def test_model_invariants(changes):
    with pytest.raises(ValueError):
        replace(ChargingSession("id", "tx", SCOPE, AT, AT), **changes)


@pytest.mark.parametrize("reading", [0, 900, None])
def test_reset_invalidates_delta_permanently(reading):
    ledger = SessionLedger()
    ledger.apply(event(meter_wh=1000))
    ledger.observe(
        (meter(Quantity.ENERGY, reading, 1), meter(Quantity.ENERGY, 1500, 2))
    )
    assert ledger.get(SCOPE).energy_charged_wh is None
    ledger.apply(event(Kind.ENDED, 3, meter_wh=1600))
    assert ledger.history()[0].energy_charged_wh is None
    assert ledger.history()[0].energy_end_wh == 1600


def test_unknown_start_and_missing_final_register():
    ledger = SessionLedger()
    ledger.apply(event())
    ledger.observe((meter(Quantity.ENERGY, 1500, 2),))
    assert ledger.get(SCOPE).energy_start_wh is None
    assert ledger.get(SCOPE).energy_charged_wh is None
    ledger.apply(event(Kind.ENDED, 3))
    assert ledger.get(SCOPE).energy_end_wh is None


def test_duplicate_and_out_of_order_events_never_reopen():
    ledger = SessionLedger()
    start = event(meter_wh=1000, sequence=0)
    assert ledger.apply(start)
    assert not ledger.apply(start)
    update = event(Kind.UPDATED, 2, sequence=2, charging_state=State.CHARGING)
    assert ledger.apply(update)
    saved = ledger.dump()
    for old in (
        update,
        start,
        event(Kind.UPDATED, 1, sequence=1),
        event(Kind.STARTED, 3),
    ):
        assert not ledger.apply(old)
        assert ledger.dump() == saved
    assert ledger.apply(event(Kind.ENDED, 3, sequence=3, meter_wh=1200))
    final = ledger.dump()
    for old in (
        update,
        event(Kind.ENDED, 3, sequence=3),
        event(Kind.UPDATED, 10, sequence=10),
        start,
    ):
        assert not ledger.apply(old)
        assert ledger.dump() == final
    assert len(ledger.history()) == 1


def test_equal_time_sequence_orders_and_conflicting_meter_is_unknown():
    ledger = SessionLedger()
    ledger.apply(event(sequence=0, meter_wh=1000))
    assert ledger.apply(event(Kind.UPDATED, sequence=1, charging_state=State.CHARGING))
    ledger.observe((meter(Quantity.ENERGY, 1001),))
    assert ledger.get(SCOPE).energy_charged_wh is None
    assert ledger.apply(event(Kind.ENDED, sequence=2))
    assert ledger.get(SCOPE).duration(AT) == 0


def test_superseded_transaction_and_late_previous_event():
    ledger = SessionLedger()
    ledger.apply(event(meter_wh=1000))
    old_id = ledger.get(SCOPE).session_id
    ledger.apply(event(seconds=10, external="new", meter_wh=1500))
    assert ledger.history()[0].session_id == old_id
    assert ledger.history()[0].end_reason == "superseded"
    assert ledger.get(SCOPE).energy_charged_wh == 0
    assert ledger.get(SCOPE).ended_at is None
    assert not ledger.apply(event(Kind.ENDED, 11, meter_wh=1700))
    assert ledger.get(SCOPE).external_transaction_id == "new"


def test_runtime_fence_disconnect_boot_and_resume():
    runtime = Runtime()
    token = runtime.connect(SCOPE.evse.station)
    assert runtime.session_event(token, event(meter_wh=100))
    original = runtime.sessions.get(SCOPE).session_id
    runtime.disconnect(token)
    assert runtime.sessions.get(SCOPE).active
    assert not runtime.session_event(token, event(Kind.ENDED, 1))
    current = runtime.connect(token.station)
    boot = runtime.boot(current, StationIdentity())
    assert not runtime.session_event(current, event(Kind.ENDED, 1))
    assert runtime.session_event(boot, event(Kind.UPDATED, 2))
    assert runtime.sessions.get(SCOPE).session_id == original
    restored = Runtime()
    restored.sessions.restore(runtime.sessions.dump())
    new_token = restored.connect(token.station)
    assert not restored.session_event(boot, event(Kind.ENDED, 3))
    assert restored.session_event(new_token, event(Kind.ENDED, 3, meter_wh=200))
    assert restored.sessions.history()[0].session_id == original


@pytest.mark.parametrize(
    "other",
    [
        EvseId(StationId("station"), "2"),
        ConnectorId(EvseId(StationId("station"), "2"), "8"),
        EvseId(StationId("station"), "3"),
        ConnectorId(EvseId(StationId("elsewhere"), "2"), "7"),
    ],
)
def test_scope_isolation(other):
    ledger = SessionLedger()
    ledger.apply(event(meter_wh=100))
    ledger.apply(event(scope=other, meter_wh=300))
    ledger.observe(
        (
            meter(Quantity.ENERGY, 400, 1, scope=other),
            meter(Quantity.POWER, 500, 1, scope=other),
        )
    )
    assert ledger.get(SCOPE).energy_charged_wh == 0
    assert ledger.get(SCOPE).current_power_w is None
    assert ledger.get(other).energy_charged_wh == 100
    ledger.apply(event(Kind.ENDED, 2, scope=other, meter_wh=500))
    assert ledger.get(SCOPE).active
    assert ledger.history(SCOPE) == ()
    assert len(ledger.history(other)) == 1


def test_transaction_id_filters_legacy_meters():
    ledger = SessionLedger()
    external = ledger.start_legacy(SCOPE, AT, 100)
    assert ledger.start_legacy(SCOPE, AT, 100) == external
    ledger.observe((meter(Quantity.POWER, 500, 1),), "wrong")
    assert ledger.get(SCOPE).current_power_w is None
    ledger.observe((meter(Quantity.POWER, 500, 1),), external)
    assert ledger.get(SCOPE).max_power_w == 500
    restored = SessionLedger()
    restored.restore(ledger.dump())
    assert restored.start_legacy(SCOPE, AT, 100) == external
    assert int(restored.start_legacy(SCOPE, AT + timedelta(seconds=2), 100)) > int(
        external
    )


def test_ended_embedded_meter_and_power_are_accounted_without_live_projection():
    ledger = SessionLedger()
    ledger.apply(event(), (meter(Quantity.ENERGY, 1000),))
    ledger.apply(
        event(Kind.ENDED, 3),
        (meter(Quantity.POWER, 4000, 2), meter(Quantity.ENERGY, 1800, 3)),
    )
    final = ledger.history()[0]
    assert final.energy_charged_wh == 800 and final.max_power_w == 4000
    assert final.current_power_w == 0


def test_missing_started_keeps_energy_unknown_and_marks_first_seen_time():
    ledger = SessionLedger()
    ledger.apply(event(Kind.UPDATED, 1), (meter(Quantity.ENERGY, 100, 1),))
    assert not ledger.get(SCOPE).start_known
    ledger.apply(event(Kind.ENDED, 2, meter_wh=200))
    assert ledger.get(SCOPE).energy_charged_wh is None
    assert ledger.get(SCOPE).energy_start_wh is None


def test_legacy_zero_duration_session_and_late_unknown_start():
    ledger = SessionLedger()
    external = ledger.start_legacy(SCOPE, AT, 100)
    assert ledger.apply(event(Kind.ENDED, external=external, meter_wh=100))
    assert ledger.history()[0].duration(AT) == 0
    assert ledger.start_legacy(SCOPE, AT - timedelta(seconds=1), 100) is None
    assert len(ledger.history()) == 1
