"""Prospective retained-current accounting through real ledger and HA projections."""

import asyncio
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from test_control_runtime import manual as manual
from test_ocpp21_control import connected as connected
from test_pv_optimum_hold import configure
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.core.events import StationIdentity
from custom_components.wallbox_manager.core.sessions import (
    SessionEvent,
    SessionEventKind,
)
from custom_components.wallbox_manager.core.telemetry import (
    Channel,
    Observation,
    Quantity,
)
from custom_components.wallbox_manager.pv_economy import (
    ECONOMIC_REASON,
    LIMIT_WS,
    GridImportPolicy,
    MeterSample,
    RestartReference,
)
from custom_components.wallbox_manager.session_entity import SessionSensor


@pytest.fixture
async def retained(grid, monkeypatch):
    p, target, context, clock = configure(grid)
    control, bound, peer, *_ = context
    await p.economic_end_scope(target)
    # A confirmed permission readback, with no positive manager TxProfile.
    peer.enabled = True
    assert await bound.read_enabled() is True
    base = datetime.now(UTC) + timedelta(seconds=2)

    class ClockMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    class Clock(datetime, metaclass=ClockMeta):
        @classmethod
        def now(cls, tz=None):
            return base + timedelta(seconds=float(clock[0]))

    monkeypatch.setattr(
        "custom_components.wallbox_manager.pv_economy_control.datetime", Clock
    )
    monkeypatch.setattr("custom_components.wallbox_manager.runtime.datetime", Clock)
    monkeypatch.setattr(
        "custom_components.wallbox_manager.protocols.ocpp.v21.enabled.datetime", Clock
    )
    monkeypatch.setattr("custom_components.wallbox_manager.pv_surplus.datetime", Clock)
    monkeypatch.setattr("custom_components.wallbox_manager.pv_optimum.datetime", Clock)
    monkeypatch.setattr(
        "custom_components.wallbox_manager.control.runtime.datetime", Clock
    )
    assert await p.economic_prepare(target, resume=False)
    assert control.confirmed_point(target) is None
    assert not peer.requests
    return p, target, context, clock, Clock


async def meter(
    retained,
    second,
    *,
    actual=2300,
    imported=2000,
    discharge=1000,
    source=None,
    attributes=None,
    power=True,
):
    p, target, (control, bound, _, *_), clock, Clock = retained
    clock[0] = Fraction(second)
    at = Clock.now(UTC) if source is None else source
    from custom_components.wallbox_manager.core.enabled import EnabledObservation

    control.runtime.observe_enabled(
        bound.adapter.token,
        EnabledObservation(
            target, True, Clock.now(UTC), Clock.now(UTC) + timedelta(seconds=15)
        ),
    )
    control.runtime.observe(
        bound.adapter.token,
        tuple(
            Observation(
                Channel(target, Quantity(f"voltage_l{phase}")),
                230,
                Clock.now(UTC),
                Clock.now(UTC),
                Clock.now(UTC) + timedelta(seconds=60),
                "test:voltage",
            )
            for phase in (1, 2, 3)
        ),
    )
    if power:
        sample = Observation(
            Channel(target, Quantity.POWER),
            actual,
            at,
            at,
            at + timedelta(seconds=30),
            "test:real-meter",
        )
        assert control.runtime.observe(bound.adapter.token, (sample,))
        sensor = SessionSensor(control.runtime, p.entry_id, target, "power")
        attrs = {
            **sensor.extra_state_attributes,
            "unit_of_measurement": "W",
            **(attributes or {}),
        }
        p.hass.states.async_set(
            "sensor.selected", actual, attrs, timestamp=Clock.now(UTC).timestamp()
        )
        await p.hass.async_block_till_done()
    for key, value in (
        ("leistung_pv", 4000),
        ("leistung_verbraucher", actual + 1000),
        ("storage_discharge_power", discharge),
        ("grid_export_power", 0),
        ("grid_import_power", imported),
    ):
        p.hass.states.async_set(
            p.references[key],
            value,
            {"unit_of_measurement": "W", "observed_at": at.isoformat()},
            timestamp=Clock.now(UTC).timestamp(),
        )
    await p.hass.async_block_till_done()
    p.economic_observe(target)


@pytest.mark.parametrize("paused", [False, True])
def test_observed_activity_never_confirms_restart(paused):
    reference = RestartReference(3000, 1380)
    policy = GridImportPolicy(
        LIMIT_WS,
        paused=paused,
        restart_required=True,
        pause_reason=ECONOMIC_REASON,
        restart_reference=reference,
    )
    before = policy.serialize()
    policy.observe_active(True, at=0)
    assert policy.serialize() == before
    assert policy.observed_active and policy.accounting_active
    assert not policy.active
    for second in (0, 5):
        policy.observe(MeterSample(second, second, second, 2000), threshold_w=690)
    assert policy.energy_ws == LIMIT_WS + 10000
    assert policy.paused is paused
    assert policy.restart_required and policy.restart_reference is reference
    restored = GridImportPolicy.restore(policy.serialize())
    assert not restored.observed_active and not restored.active
    assert restored.restart_required and restored.paused is paused
    assert restored.energy_ws == LIMIT_WS + 10000


async def test_q1_real_measurements_anchor_and_count_without_positive_command(retained):
    p, target, (_, _, peer, *_), _, _ = retained
    policy = p.economic_policies[target]
    await meter(retained, 0)
    assert policy.energy_ws == 0
    assert policy.observed_active and not policy.active
    assert p.economic_attributes(target)["pv_economic_monitoring_active"] is True
    await meter(retained, 10)
    assert policy.energy_ws == 20000
    await meter(retained, 10, power=False)
    assert policy.energy_ws == 20000
    assert not peer.requests and not peer.permissions
    assert p.control.runtime.enabled(target) is True


@pytest.mark.parametrize(
    "missing",
    [
        "transaction",
        "owner",
        "authority",
        "permission",
        "ev",
        "ambiguous_ev",
        "transaction_attribute",
        "session_attribute",
        "runtime_attribute",
        "expired_ev",
        "grid",
        "minimum",
        "history",
        "stored_identity",
        "write_failure",
    ],
)
async def test_each_missing_q1_condition_prevents_monitoring_and_no_permission_off(
    retained, missing, monkeypatch
):
    p, target, (control, _, peer, _, source, _), clock, Clock = retained
    await meter(retained, 0)
    policy = p.economic_policies[target]
    policy.energy_ws = 1234
    clock[0] = 1
    if missing == "transaction":
        control.runtime.session_event(
            control.runtime.get(target.station).token,
            SessionEvent(
                target,
                "unexplained",
                SessionEventKind.STARTED,
                Clock.now(UTC) + timedelta(microseconds=1),
            ),
            live=True,
        )
    elif missing == "owner":
        source.permitted = ()
        p.suppressed.add(target)
        monkeypatch.setattr(control, "profile_permitted", lambda _: False)
    elif missing == "authority":
        control.runtime.observe_authority(
            control.runtime.get(target.station).token,
            AuthorityObservation(
                target.station, ControlAuthority.LOCAL, Clock.now(UTC), "test"
            ),
        )
    elif missing == "permission":
        from custom_components.wallbox_manager.core.enabled import EnabledObservation

        at = Clock.now(UTC)
        control.runtime.observe_enabled(
            control.runtime.get(target.station).token,
            EnabledObservation(target, None, at, at + timedelta(seconds=60)),
        )
    elif missing in (
        "ev",
        "ambiguous_ev",
        "transaction_attribute",
        "session_attribute",
        "runtime_attribute",
        "expired_ev",
    ):
        old = p.hass.states.get("sensor.selected")
        attrs = dict(old.attributes)
        if missing == "ev":
            p.hass.states.async_remove("sensor.selected")
        elif missing == "ambiguous_ev":
            p.hass.states.async_set("sensor.duplicate", 2300, attrs)
        else:
            key, value = {
                "transaction_attribute": ("external_transaction_id", "other"),
                "session_attribute": ("session_id", "other"),
                "runtime_attribute": ("runtime_incarnation", "other"),
                "expired_ev": (
                    "valid_until",
                    (Clock.now(UTC) - timedelta(seconds=1)).isoformat(),
                ),
            }[missing]
            p.hass.states.async_set("sensor.selected", 2300, {**attrs, key: value})
    elif missing == "grid":
        p.hass.states.async_set(
            p.references["grid_import_power"],
            "unavailable",
            {"unit_of_measurement": "W"},
        )
    elif missing == "minimum":
        source.proof = False
        source.mode = None
    elif missing == "history":
        p.economic_reconciled.pop(target)
    elif missing == "stored_identity":
        p.economic_store.forget(p.economic_identities[target], explicit_scope_exit=True)
    elif missing == "write_failure":
        p.economic_store.failure_reason = "economic_history_write_failed"
    await p.hass.async_block_till_done()
    p.economic_observe(target, activity_event=True)
    attrs = p.economic_attributes(target)
    assert not attrs["pv_economic_monitoring_active"]
    assert policy.energy_ws == 1234
    assert not peer.permissions and not peer.requests


async def test_observing_retained_flow_preserves_exhaustion_and_restart_reference(
    retained,
):
    p, target, _, _, _ = retained
    policy = p.economic_policies[target]
    policy.energy_ws = LIMIT_WS
    policy.confirm_off(at=0)
    policy.capture_restart_reference(3000, 1380)
    reference = policy.restart_reference
    await meter(retained, 0)
    await meter(retained, 5)
    assert policy.energy_ws == LIMIT_WS + 10000
    assert policy.paused and policy.restart_required
    assert policy.restart_reference is reference
    assert not p.economic_restart_permits(target, p.economic_minimum(target).point)


async def test_acceptance_gap_discards_unknown_interval_and_keeps_budget(retained):
    p, target, _, _, _ = retained
    policy = p.economic_policies[target]
    await meter(retained, 0)
    await meter(retained, 11)
    assert policy.energy_ws == 0 and policy.below_seconds == 0
    await meter(retained, 16)
    assert policy.energy_ws == 10000, p.economic_attributes(target)


async def test_retained_reset_requires_strictly_more_than_fifteen_seconds(retained):
    p, target, _, _, _ = retained
    policy = p.economic_policies[target]
    policy.energy_ws = 1234
    await meter(retained, 0, imported=690)
    await meter(retained, 10, imported=690)
    await meter(retained, 15, imported=690)
    assert policy.energy_ws == 1234 and policy.below_seconds == 15, (
        p.economic_attributes(target)
    )
    await meter(retained, 16, imported=690)
    assert policy.energy_ws == 0


async def test_retained_exhaustion_uses_exact_50wh_and_entire_import(retained):
    p, target, _, _, _ = retained
    policy = p.economic_policies[target]
    await meter(retained, 0, imported=10000)
    await meter(retained, 10, imported=10000)
    await meter(retained, Fraction(17999, 1000), imported=10000)
    assert policy.energy_ws == 179990 and not policy.exhausted, p.economic_attributes(
        target
    )
    await meter(retained, 18, imported=10000)
    assert policy.energy_ws == LIMIT_WS and policy.exhausted


@pytest.mark.parametrize("transition", ["socket", "boot", "authority"])
async def test_observational_anchors_do_not_cross_generation_or_authority(
    retained, transition
):
    p, target, (control, bound, _, *_), clock, Clock = retained
    policy = p.economic_policies[target]
    await meter(retained, 0)
    oldtoken = bound.adapter.token
    clock[0] = 5
    if transition == "authority":
        control.runtime.observe_authority(
            oldtoken,
            AuthorityObservation(
                target.station, ControlAuthority.LOCAL, Clock.now(UTC), "test"
            ),
        )
    elif transition == "boot":
        bound.adapter.token = control.runtime.boot(oldtoken, StationIdentity())
    else:
        bound.adapter.token = control.runtime.connect(target.station)
    p.economic_observe(target)
    assert policy.energy_ws == 0
    assert not p.economic_attributes(target)["pv_economic_monitoring_active"]
    assert not policy.observed_active


async def test_identity_known_before_first_activity_starts_at_activity_anchor(retained):
    p, target, _, _, _ = retained
    policy = p.economic_policies[target]
    await meter(retained, 0, actual=0)
    assert not policy.accounting_active and policy.energy_ws == 0
    await meter(retained, 5, actual=2300)
    assert policy.observed_active and policy.energy_ws == 0
    await meter(retained, 10)
    assert policy.energy_ws == 10000


async def test_late_identity_never_backfills_preidentity_power_or_import(retained):
    p, target, (control, bound, peer, *_), clock, Clock = retained
    await p.economic_end_scope(target)
    old = control.runtime.sessions.get(target)
    assert control.runtime.session_event(
        bound.adapter.token,
        SessionEvent(
            target,
            old.external_transaction_id,
            SessionEventKind.ENDED,
            Clock.now(UTC),
            end_reason="EVDeparted",
            sequence=1,
        ),
        live=True,
    )
    p.economic_activation_pending[target] = False
    assert not await p.economic_prepare(target, resume=False)
    await meter(retained, 0, power=False)
    # Independent real power can arrive before any usable transaction.
    at = Clock.now(UTC)
    control.runtime.observe(
        bound.adapter.token,
        (
            Observation(
                Channel(target, Quantity.POWER),
                2300,
                at,
                at,
                at + timedelta(seconds=30),
                "test:preidentity",
            ),
        ),
    )
    p.economic_observe(target)
    assert target not in p.economic_policies
    assert not p.economic_attributes(target)["pv_economic_monitoring_active"]
    clock[0] = 10
    assert control.runtime.session_event(
        bound.adapter.token,
        SessionEvent(
            target, "late-real", SessionEventKind.STARTED, Clock.now(UTC), sequence=0
        ),
        live=True,
    )
    assert await p.economic_prepare(target, resume=False)
    policy = p.economic_policies[target]
    assert policy.energy_ws == 0 and not policy.accounting_active
    await meter(retained, 10)
    assert policy.energy_ws == 0 and policy.observed_active
    await meter(retained, 15)
    assert policy.energy_ws == 10000
    assert not peer.requests and not peer.permissions


async def test_extended_missing_transaction_keeps_permission_without_new_scope(
    retained,
):
    from custom_components.wallbox_manager.core.enabled import EnabledObservation

    p, target, (control, bound, peer, *_), clock, Clock = retained
    await p.economic_end_scope(target)
    old = control.runtime.sessions.get(target)
    control.runtime.session_event(
        bound.adapter.token,
        SessionEvent(
            target,
            old.external_transaction_id,
            SessionEventKind.ENDED,
            Clock.now(UTC),
            end_reason="EVDeparted",
            sequence=1,
        ),
        live=True,
    )
    p.economic_activation_pending[target] = False
    for seconds in (0, 60, 3600, 86400):
        clock[0] = seconds
        at = Clock.now(UTC)
        control.runtime.observe_enabled(
            bound.adapter.token,
            EnabledObservation(target, True, at, at + timedelta(seconds=15)),
        )
        p.economic_observe(target)
        attrs = p.economic_attributes(target)
        assert control.runtime.enabled(target) is True
        assert target not in p.economic_policies
        assert not attrs["pv_economic_monitoring_active"]
        assert attrs["pv_unattributed_flow_duration_bounded"] is False
        assert attrs["pv_transaction_bound_zero_identity_available"] is False
        assert (
            attrs["pv_retained_current_inhibition"]
            == "retained_transaction_unavailable"
        )
    assert not peer.requests and not peer.permissions


@pytest.mark.parametrize("kind", ["superseded", "unknown_end"])
async def test_changed_or_unknown_transaction_cannot_retire_exhausted_history(
    retained, kind
):
    p, target, (control, bound, peer, *_), clock, Clock = retained
    policy = p.economic_policies[target]
    policy.energy_ws = LIMIT_WS
    p.economic_checkpoint(target)
    assert await p.economic_store.flush()
    original = p.economic_identities[target]
    clock[0] = 1
    assert control.runtime.session_event(
        bound.adapter.token,
        SessionEvent(
            target,
            "changed",
            SessionEventKind.STARTED
            if kind == "superseded"
            else SessionEventKind.ENDED,
            Clock.now(UTC),
            end_reason="EVDeparted",
            sequence=0,
        ),
        live=True,
    )
    await p.hass.async_block_till_done()
    assert p.economic_policies[target] is policy
    assert policy.energy_ws == LIMIT_WS
    assert p.economic_store.identity_for_target(target) == original
    assert not await p.economic_prepare(target, resume=True)
    assert p.economic_policies[target] is policy
    assert p.economic_store.identity_for_target(target) == original
    assert policy.energy_ws == LIMIT_WS
    assert all(
        request[1]["charging_schedule"][0]["charging_schedule_period"][0]["limit"] == 0
        for request in peer.requests
    )


async def test_genuine_end_and_new_start_create_only_the_new_real_scope(retained):
    p, target, (control, bound, peer, *_), clock, Clock = retained
    old = control.runtime.sessions.get(target)
    p.economic_policies[target].energy_ws = LIMIT_WS
    p.economic_checkpoint(target)
    assert await p.economic_store.flush()
    clock[0] = 1
    assert control.runtime.session_event(
        bound.adapter.token,
        SessionEvent(
            target,
            old.external_transaction_id,
            SessionEventKind.ENDED,
            Clock.now(UTC),
            end_reason="EVDeparted",
            sequence=1,
        ),
        live=True,
    )
    clock[0] = 2
    assert control.runtime.session_event(
        bound.adapter.token,
        SessionEvent(
            target,
            "genuinely-new",
            SessionEventKind.STARTED,
            Clock.now(UTC),
            sequence=0,
        ),
        live=True,
    )
    await p.hass.async_block_till_done()
    await asyncio.gather(*p.economic_lifecycle_tasks)
    assert p.economic_reconciled[target] == p.economic_identities[target]
    assert p.economic_identities[target].transaction_id == "genuinely-new"
    assert p.economic_policies[target].energy_ws == 0
    assert not p.economic_policies[target].accounting_active
    await meter(retained, 3)
    await meter(retained, 8)
    assert p.economic_policies[target].energy_ws == 10000
    assert not peer.permissions


async def test_retained_history_reconciliation_and_reload_do_not_integrate_downtime(
    retained,
):
    p, target, _, clock, Clock = retained
    await meter(retained, 0)
    await meter(retained, 5)
    policy = p.economic_policies[target]
    assert policy.energy_ws == 10000
    identity = p.economic_identities[target]
    assert await p.economic_store.flush()
    await p.economic_store.close()
    p.economic_unsubscribe()
    p.economic_initialize()
    await p.economic_load()
    clock[0] = 100
    assert await p.economic_prepare(target, resume=True)
    new = p.economic_policies[target]
    assert new.energy_ws == 10000 and not new.accounting_active
    assert p.economic_identities[target] == identity
    await meter(retained, 100)
    assert new.energy_ws == 10000
    await meter(retained, 105)
    assert new.energy_ws == 20000


async def test_fresh_retained_power_cannot_clear_restored_restart_provenance(retained):
    p, target, _, _, _ = retained
    policy = p.economic_policies[target]
    policy.energy_ws = LIMIT_WS
    policy.confirm_off(at=0)
    policy.capture_restart_reference(3000, 1380)
    p.economic_checkpoint(target)
    assert await p.economic_store.flush()
    record = policy.serialize()
    p.economic_policies.pop(target)
    p.economic_identities.pop(target)
    assert not await p.economic_prepare(target, resume=True)
    restored = p.economic_policies[target]
    await meter(retained, 0)
    assert restored.restart_required
    assert restored.restart_reference == RestartReference.restore(
        record["restart_reference"]
    )
    assert restored.energy_ws >= LIMIT_WS


async def test_actual_ev_zero_ends_only_observational_exposure(retained):
    p, target, _, _, _ = retained
    policy = p.economic_policies[target]
    await meter(retained, 0)
    await meter(retained, 5, actual=0)
    assert policy.energy_ws == 10000
    assert not policy.observed_active and not policy.active
    await meter(retained, 10, actual=0)
    assert policy.energy_ws == 10000


async def test_store_failure_prevents_new_observational_interval_but_keeps_history(
    retained,
):
    p, target, _, _, _ = retained
    await meter(retained, 0)
    policy = p.economic_policies[target]
    policy.energy_ws = 1234
    p.economic_store.failure_reason = "economic_history_write_failed"
    await meter(retained, 5)
    assert policy.energy_ws == 1234 and not policy.observed_active
    assert not p.economic_attributes(target)["pv_economic_monitoring_active"]
    p.economic_store.failure_reason = None
    await meter(retained, 10)
    assert policy.energy_ws == 1234
    await meter(retained, 15)
    assert policy.energy_ws == 11234


async def test_initial_durability_gates_observational_and_positive_activity(
    retained, monkeypatch
):
    p, target, _, clock, _ = retained
    await p.economic_end_scope(target)
    entered, release = asyncio.Event(), asyncio.Event()
    real = p.economic_store.store.async_save

    async def held(*args, **kwargs):
        entered.set()
        await release.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(p.economic_store.store, "async_save", held)
    task = asyncio.create_task(p.economic_prepare(target, resume=False))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await meter(retained, 0)
        assert not p.economic_attributes(target)["pv_economic_monitoring_active"]
        assert not p.economic_restart_permits(target, p.economic_minimum(target).point)
        clock[0] = 1
        release.set()
        assert await task
        policy = p.economic_policies[target]
        assert policy.energy_ws == 0 and not policy.accounting_active
        await meter(retained, 2)
        assert policy.energy_ws == 0
        await meter(retained, 7)
        assert policy.energy_ws == 10000
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("failure", ["corrupt", "write"])
async def test_corrupt_or_failed_initial_history_cannot_grant_a_new_allowance(
    retained, monkeypatch, failure
):
    from unittest.mock import AsyncMock

    p, target, (_, _, peer, *_), _, _ = retained
    if failure == "corrupt":
        await p.economic_store.close()
        p.economic_unsubscribe()
        p.economic_initialize()
        monkeypatch.setattr(
            p.economic_store.store,
            "async_load",
            AsyncMock(return_value={"version": 1, "records": "invalid"}),
        )
        await p.economic_load()
        assert not await p.economic_prepare(target, resume=True)
        assert target not in p.economic_policies
        assert p.economic_store._load_reason == "economic_history_corrupt"
    else:
        await p.economic_end_scope(target)
        monkeypatch.setattr(
            p.economic_store.store,
            "async_save",
            AsyncMock(side_effect=OSError("test disk failure")),
        )
        assert not await p.economic_prepare(target, resume=False)
        await meter(retained, 1)
        assert p.economic_policies[target].energy_ws == 0
        assert p.economic_store.failure_reason == "economic_history_write_failed"
    assert not p.economic_attributes(target)["pv_economic_monitoring_active"]
    assert all(
        request[1]["charging_schedule"][0]["charging_schedule_period"][0]["limit"] == 0
        for request in peer.requests
    )
    assert not peer.permissions


async def test_source_generation_repeats_and_conflicts_never_add_energy(retained):
    p, target, _, clock, Clock = retained
    policy = p.economic_policies[target]
    await meter(retained, 0)
    source = Clock.now(UTC)
    await meter(retained, 5, source=source)
    assert policy.energy_ws == 0
    await meter(retained, 6, source=source, imported=2100)
    assert policy.energy_ws == 0
    assert policy.coverage_reason == "conflicting_meter_generation"
    await meter(retained, 10)
    assert policy.energy_ws == 0
    await meter(retained, 15)
    assert policy.energy_ws == 10000


async def test_changed_physical_minimum_is_prospective_not_a_budget_reset(retained):
    from custom_components.wallbox_manager.core.models import PhaseMode

    p, target, (control, _, _, _, source, _), clock, _ = retained
    policy = p.economic_policies[target]
    await meter(retained, 0, imported=1000)
    clock[0] = 5
    state = control.runtime.get(target.station)
    source.mode = PhaseMode.canonical(3)
    control._phase_restrictions[target] = (
        state.token,
        state.authority_revision,
        source.mode,
    )
    control.publish(target)
    await meter(retained, 5, imported=1000)
    assert policy.energy_ws == 5000 and policy.threshold_w == 2070
    await meter(retained, 10, imported=1000)
    assert policy.energy_ws == 5000 and policy.below_seconds == 5
    clock[0] = 15
    source.mode = PhaseMode.canonical(1)
    control._phase_restrictions.pop(target)
    control.publish(target)
    await meter(retained, 15, imported=1000)
    assert policy.energy_ws == 5000 and policy.threshold_w == 690
    await meter(retained, 20, imported=1000)
    assert policy.energy_ws == 10000


async def test_explicit_off_during_attribution_retires_scope_and_fences_late_history(
    retained, monkeypatch
):
    p, target, (control, _, peer, *_), clock, _ = retained
    await p.economic_end_scope(target)
    entered, release = asyncio.Event(), asyncio.Event()
    real = p.economic_store.store.async_save

    async def held(*args, **kwargs):
        entered.set()
        await release.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(p.economic_store.store, "async_save", held)
    preparation = asyncio.create_task(p.economic_prepare(target, resume=False))
    await asyncio.wait_for(entered.wait(), 1)
    clock[0] = 1
    off = asyncio.create_task(p.permission(target, False))
    await asyncio.sleep(0)
    assert target in p.suppressed or p.epochs.get(target, 0) > 0
    release.set()
    result = await off
    assert result.status.value == "applied"
    assert not await preparation
    assert control.runtime.enabled(target) is False
    assert target not in p.economic_policies and target not in p.economic_reconciled
    assert p.economic_store.identity_for_target(target) is None
    assert not peer.requests


async def test_unrelated_earlier_end_cannot_validate_a_later_superseded_handover(
    retained,
):
    p, target, (control, bound, _, *_), clock, Clock = retained
    current = control.runtime.sessions.get(target)
    # A genuine old end precedes a new real transaction, which is then
    # superseded without another valid end. Only the immediate predecessor counts.
    clock[0] = 1
    control.runtime.session_event(
        bound.adapter.token,
        SessionEvent(
            target,
            current.external_transaction_id,
            SessionEventKind.ENDED,
            Clock.now(UTC),
            end_reason="EVDeparted",
            sequence=1,
        ),
        live=True,
    )
    clock[0] = 2
    control.runtime.session_event(
        bound.adapter.token,
        SessionEvent(
            target, "middle", SessionEventKind.STARTED, Clock.now(UTC), sequence=0
        ),
        live=True,
    )
    await asyncio.gather(*p.economic_lifecycle_tasks)
    clock[0] = 3
    control.runtime.session_event(
        bound.adapter.token,
        SessionEvent(
            target, "unexplained", SessionEventKind.STARTED, Clock.now(UTC), sequence=0
        ),
        live=True,
    )
    assert not p.economic_new_transaction_proven(target)
    assert not await p.economic_prepare(target, resume=True)
    assert p.economic_identities[target].transaction_id == "middle"


async def test_existing_profile_takes_over_retained_charge_through_real_sender(
    retained,
):
    from test_pv_optimum_hold import evaluate

    p, target, (control, _, peer, *_), _, _ = retained
    await meter(retained, 0, imported=0)
    assert p.economic_policies[target].observed_active
    result = await evaluate(p, target)
    assert result is not None and result.status.value == "applied", str(
        (p.status, p.economic_attributes(target), p.optimum_regulators.get(target))
    )
    assert control.confirmed_point(target).charging
    assert len(peer.requests) == 1
    assert (
        peer.requests[0][1]["transaction_id"]
        == control.runtime.sessions.get(target).external_transaction_id
    )
    assert not peer.permissions


async def test_late_positive_ack_cannot_erase_new_retained_current_exhaustion(retained):
    from test_pv_optimum_hold import evaluate

    p, target, (_, _, peer, *_), clock, _ = retained
    await meter(retained, 0, imported=0)
    policy = p.economic_policies[target]
    policy.energy_ws = LIMIT_WS - 10000
    clock[0] = 1
    peer.received.clear()
    peer.release.clear()
    task = asyncio.create_task(evaluate(p, target))
    try:
        await asyncio.wait_for(peer.received.wait(), 1)
        await meter(retained, 5)
        assert policy.energy_ws == LIMIT_WS - 10000
        await meter(retained, 10)
        assert policy.exhausted
        policy.mark_off_pending()
        reference = policy.restart_reference
        peer.release.set()
        assert (await task).status.value == "applied"
        assert policy.exhausted and policy.off_pending
        assert policy.restart_reference is reference
        result = await evaluate(p, target)
        assert result.status.value == "applied"
        assert policy.paused and policy.restart_required
        assert not policy.observed_active
        assert (
            peer.requests[-1][1]["charging_schedule"][0]["charging_schedule_period"][0][
                "limit"
            ]
            == 0
        )
    finally:
        peer.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_retained_positive_readback_alone_cannot_activate_or_confirm_restart(
    retained, monkeypatch
):
    p, target, (control, _, peer, *_), _, _ = retained
    policy = p.economic_policies[target]
    policy.energy_ws = 1234
    policy.restart_required = True
    policy.pause_reason = ECONOMIC_REASON
    policy.restart_reference = RestartReference(3000, 1380)
    point = p.economic_minimum(target).point
    monkeypatch.setattr(control, "confirmed_point", lambda _: point)
    p.economic_observe(target)
    assert not policy.accounting_active and policy.energy_ws == 1234
    p.economic_point_confirmed(target, point)
    assert not policy.active
    assert policy.restart_required and policy.restart_reference == RestartReference(
        3000, 1380
    )
    assert not peer.requests and not peer.permissions


async def test_real_transaction_wakes_startup_without_waiting_sixty_seconds(retained):
    p, target, (control, bound, peer, *_), clock, Clock = retained
    assert (await p.permission(target, False)).status.value == "applied"
    old = control.runtime.sessions.get(target)
    control.runtime.session_event(
        bound.adapter.token,
        SessionEvent(
            target,
            old.external_transaction_id,
            SessionEventKind.ENDED,
            Clock.now(UTC),
            end_reason="EVDeparted",
            sequence=1,
        ),
        live=True,
    )
    waits = asyncio.Queue()

    async def park(seconds):
        waits.put_nowait(seconds)
        await asyncio.Event().wait()

    p.wait = park
    old_discharge = p.hass.states.get(p.references["storage_discharge_power"])
    p.hass.states.async_set(old_discharge.entity_id, 500, old_discharge.attributes)
    clock[0] = 1
    peer.received.clear()
    assert (await p.permission(target, True)).status.value == "applied"
    worker = p.tasks[target]
    assert await asyncio.wait_for(waits.get(), 1) == 60
    before = len(peer.permissions)
    clock[0] = 2
    control.runtime.session_event(
        bound.adapter.token,
        SessionEvent(
            target, "event-wake", SessionEventKind.STARTED, Clock.now(UTC), sequence=0
        ),
        live=True,
    )
    await meter(retained, 2, actual=0, imported=0, discharge=500)
    await asyncio.wait_for(peer.received.wait(), 1)
    await asyncio.wait_for(worker, 2)
    assert clock[0] == 2
    assert control.confirmed_point(target).charging
    assert p.economic_identities[target].transaction_id == "event-wake"
    assert len(peer.permissions) == before


@pytest.mark.parametrize("change", ["socket", "boot", "authority", "transaction"])
async def test_initial_history_completion_cannot_cross_live_context(
    retained, monkeypatch, change
):
    from custom_components.wallbox_manager.core.enabled import EnabledObservation

    p, target, (control, bound, peer, *_), clock, Clock = retained
    await p.economic_end_scope(target)
    entered, release = asyncio.Event(), asyncio.Event()
    real = p.economic_store.store.async_save

    async def held(*args, **kwargs):
        entered.set()
        await release.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(p.economic_store.store, "async_save", held)
    task = asyncio.create_task(p.economic_prepare(target, resume=False))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        clock[0] = 1
        if change == "transaction":
            control.runtime.session_event(
                bound.adapter.token,
                SessionEvent(
                    target,
                    "changed-during-save",
                    SessionEventKind.STARTED,
                    Clock.now(UTC),
                    sequence=0,
                ),
                live=True,
            )
        elif change == "socket":
            bound.adapter.token = control.runtime.connect(target.station)
        elif change == "boot":
            bound.adapter.token = control.runtime.boot(
                bound.adapter.token, StationIdentity()
            )
        else:
            control.runtime.observe_authority(
                bound.adapter.token,
                AuthorityObservation(
                    target.station, ControlAuthority.LOCAL, Clock.now(UTC), "test"
                ),
            )
        if change != "transaction":
            clock[0] = 2
            control.runtime.observe_authority(
                bound.adapter.token,
                AuthorityObservation(
                    target.station, ControlAuthority.REMOTE, Clock.now(UTC), "test"
                ),
            )
            control.runtime.observe_enabled(
                bound.adapter.token,
                EnabledObservation(
                    target, True, Clock.now(UTC), Clock.now(UTC) + timedelta(seconds=15)
                ),
            )
        release.set()
        assert not await task
        assert target not in p.economic_reconciled
        assert not p.economic_attributes(target)["pv_economic_monitoring_active"]
        assert not peer.requests and not peer.permissions
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_reused_old_zero_does_not_erase_later_real_observed_flow(retained):
    from test_pv_optimum_hold import evaluate

    p, target, (_, _, peer, *_), clock, _ = retained
    await meter(retained, 0)
    policy = p.economic_policies[target]
    policy.energy_ws = LIMIT_WS
    assert (await evaluate(p, target)).status.value == "applied"
    assert policy.paused and not policy.observed_active
    await meter(retained, 1)
    await meter(retained, 6)
    assert policy.energy_ws == LIMIT_WS + 10000 and policy.observed_active
    before = len(peer.requests)
    clock[0] = 7
    assert (await evaluate(p, target)).status.value == "applied"
    assert len(peer.requests) == before
    assert policy.observed_active and policy.paused and policy.restart_required
    await meter(retained, 11)
    assert policy.energy_ws == LIMIT_WS + 20000


async def test_minimum_recovery_cannot_replay_samples_seen_while_ineligible(retained):
    from custom_components.wallbox_manager.core.capabilities import CurrentLimit
    from custom_components.wallbox_manager.core.models import PhaseMode

    p, target, (control, _, _, _, source, _), clock, _ = retained
    await meter(retained, 0)
    policy = p.economic_policies[target]
    policy.energy_ws = 1234
    clock[0] = 1
    source.permitted = tuple(
        CurrentLimit(PhaseMode.canonical(n), 0, 0, "test") for n in (1, 3)
    )
    control.publish(target)
    await meter(retained, 5)
    assert policy.energy_ws == 1234 and not policy.observed_active
    clock[0] = 10
    source.permitted = ()
    p.economic_observe(target)
    assert not policy.observed_active and policy.energy_ws == 1234
    await meter(retained, 10)
    assert policy.observed_active and policy.energy_ws == 1234
    await meter(retained, 15)
    assert policy.energy_ws == 11234


async def test_duplicate_grid_poll_cannot_keep_expired_ev_monitoring_active(retained):
    p, target, (_, _, peer, *_), clock, Clock = retained
    await meter(
        retained,
        0,
        attributes={"valid_until": (Clock.now(UTC) + timedelta(seconds=1)).isoformat()},
    )
    policy = p.economic_policies[target]
    assert p.economic_attributes(target)["pv_economic_monitoring_active"]
    clock[0] = 2
    p.economic_observe(target)
    assert not p.economic_attributes(target)["pv_economic_monitoring_active"]
    assert not policy.observed_active and policy.energy_ws == 0
    assert not peer.permissions and not peer.requests
    await meter(retained, 5)
    assert policy.energy_ws == 0
    await meter(retained, 10)
    assert policy.energy_ws == 10000


@pytest.mark.parametrize(
    "missing", ["failure", "reconciliation", "binding", "durability"]
)
async def test_duplicate_poll_rechecks_durable_history(retained, missing):
    p, target, (_, _, peer, *_), clock, _ = retained
    await meter(retained, 0)
    policy = p.economic_policies[target]
    assert p.economic_attributes(target)["pv_economic_monitoring_active"]
    if missing == "failure":
        p.economic_store.failure_reason = "economic_history_write_failed"
    elif missing == "reconciliation":
        p.economic_reconciled.pop(target)
    elif missing == "binding":
        p.economic_store.forget(p.economic_identities[target], explicit_scope_exit=True)
    else:
        p.economic_pending_durability[target] = policy
    clock[0] = 1
    p.economic_observe(target)
    assert not p.economic_attributes(target)["pv_economic_monitoring_active"]
    assert not policy.observed_active and policy.energy_ws == 0
    assert not peer.permissions and not peer.requests
