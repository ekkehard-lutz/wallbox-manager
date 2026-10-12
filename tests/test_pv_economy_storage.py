"""Real HA storage and identity recovery, with deterministic checkpoint timers."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util.file import WriteError

from custom_components.wallbox_manager.core.events import StationIdentity
from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.pv_economy import (
    ECONOMIC_REASON,
    GridImportPolicy,
    MeterSample,
)
from custom_components.wallbox_manager.pv_economy_storage import (
    CHECKPOINT_SECONDS,
    POLICY_METADATA,
    STORAGE_VERSION,
    EconomicIdentity,
    EconomicStorage,
)

IDENTITY = EconomicIdentity(
    "entry",
    ConnectorId(EvseId(StationId("station"), "1"), "1"),
    StationIdentity("vendor", "model", "serial", "firmware"),
    "transaction",
)


def observe(policy, at, imported):
    return policy.observe(
        MeterSample(("site-import", str(at)), at, at, imported), threshold_w=690
    )


def recover(storage, identity=IDENTITY, **proof):
    return storage.restore(
        identity,
        authority_valid=proof.get("authority_valid", True),
        transaction_valid=proof.get("transaction_valid", True),
    )


@pytest.fixture
async def storage(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    wrapper = EconomicStorage(hass, "entry")
    await wrapper.load()
    try:
        yield wrapper, hass, tmp_path
    finally:
        await wrapper.close()
        await hass.async_stop()


def disk_data(tmp_path):
    return json.loads(
        (tmp_path / ".storage/wallbox_manager.entry.pv_economy").read_text()
    )


async def test_missing_history_is_inhibited_until_proven_new_scope(storage):
    wrapper, _, tmp_path = storage
    missing = recover(wrapper)
    assert missing.policy is None
    assert missing.reason == "economic_history_missing"
    with pytest.raises(ValueError, match="explicit new scope"):
        await wrapper.begin_scope(
            IDENTITY, GridImportPolicy(), explicit_new_scope=False
        )
    with pytest.raises(ValueError, match="explicit begin_scope"):
        wrapper.checkpoint(IDENTITY, GridImportPolicy())
    assert await wrapper.begin_scope(
        IDENTITY, GridImportPolicy(), explicit_new_scope=True
    )
    assert recover(wrapper).restored
    disk = disk_data(tmp_path)
    assert disk["version"] == STORAGE_VERSION
    assert disk["data"]["records"][0]["policy"] == POLICY_METADATA
    assert disk["data"]["records"][0]["identity"] == IDENTITY.serialize()


@pytest.mark.parametrize(
    "lifecycle",
    [
        "active",
        "reset_pending",
        "off_pending",
        "paused",
        "reset_paused",
        "flight_reset",
        "historical_reassert",
    ],
)
async def test_round_trip_keeps_exact_energy_without_anchors_or_reset_dwell(
    tmp_path, lifecycle
):
    policy = GridImportPolicy(Fraction(123456, 7), active=True)
    if lifecycle == "reset_pending":
        observe(policy, 0, 0)
        observe(policy, 10, 0)
        assert policy.below_seconds == 10
    if lifecycle in (
        "off_pending",
        "paused",
        "reset_paused",
        "flight_reset",
        "historical_reassert",
    ):
        policy = GridImportPolicy(Fraction(180001, 1), active=True)
        policy.mark_off_pending()
        if lifecycle == "flight_reset":
            policy.capture_restart_reference(3770, 1380)
            policy.mark_off_in_flight()
            for at in (0, 10, 16):
                observe(policy, at, 0)
            assert policy.energy_ws == 0 and policy.off_in_flight and policy.off_pending
        if lifecycle in ("paused", "reset_paused", "historical_reassert"):
            if lifecycle == "historical_reassert":
                policy.capture_restart_reference(3770, 1380)
            policy.confirm_off(at=0)
        if lifecycle in ("reset_paused", "historical_reassert"):
            for at in (0, 10, 16):
                observe(policy, at, 0)
            assert policy.energy_ws == 0
            assert policy.paused and policy.pause_reason == ECONOMIC_REASON
        if lifecycle == "historical_reassert":
            reference = policy.restart_reference.serialize()
            policy.set_active(True, at=17)
            policy.mark_off_pending()
            for at in (18, 28, 34):
                observe(policy, at, 0)
            policy.mark_off_not_applied()
            assert policy.energy_ws == 0 and policy.active and not policy.paused
            assert policy.restart_required and policy.off_pending
            assert policy.restart_reference.serialize() == reference
    hass = HomeAssistant(str(tmp_path))
    first = EconomicStorage(hass, "entry")
    await first.load()
    assert await first.begin_scope(IDENTITY, policy, explicit_new_scope=True)
    assert await first.close()
    await hass.async_stop()
    saved = disk_data(tmp_path)["data"]["records"][0]["state"]
    assert set(saved) == {
        "version",
        "energy_ws",
        "active",
        "off_pending",
        "off_in_flight",
        "paused",
        "restart_required",
        "pause_reason",
        "restart_reference",
    }
    restarted = HomeAssistant(str(tmp_path))
    second = EconomicStorage(restarted, "entry")
    try:
        await second.load()
        restored = recover(second).policy
        assert restored.serialize() == policy.serialize()
        assert restored.energy_ws == policy.energy_ws
        assert restored.snapshot.exhausted == policy.exhausted
        assert restored.threshold_w is None and restored.last_import_w is None
        assert restored.below_seconds == 0
        observe(restored, 1000, 0)
        assert restored.energy_ws == policy.energy_ws
        assert restored.below_seconds == 0  # Downtime never proves a reset.
        assert await second.close()
    finally:
        await restarted.async_stop()


@pytest.mark.parametrize(
    "proof",
    [
        {"authority_valid": False},
        {"authority_valid": 1},
        {"authority_valid": None},
        {"transaction_valid": False},
        {"transaction_valid": 1},
        {"transaction_valid": None},
    ],
)
async def test_live_authority_and_transaction_proof_are_both_mandatory(storage, proof):
    wrapper, _, _ = storage
    await wrapper.begin_scope(
        IDENTITY, GridImportPolicy(180000, active=True), explicit_new_scope=True
    )
    restored = recover(wrapper, **proof)
    assert restored.policy is None and restored.reason
    # A temporary proof failure does not destroy or reset the matching history.
    assert recover(wrapper).policy.exhausted


@pytest.mark.parametrize(
    "identity",
    [
        replace(IDENTITY, transaction_id="different-transaction"),
        replace(IDENTITY, entry_id="different-entry"),
        replace(
            IDENTITY, station_identity=replace(IDENTITY.station_identity, serial="s")
        ),
        replace(
            IDENTITY, station_identity=replace(IDENTITY.station_identity, firmware="f")
        ),
        replace(
            IDENTITY, station_identity=replace(IDENTITY.station_identity, vendor="v")
        ),
        replace(IDENTITY, target=replace(IDENTITY.target, value="2")),
        replace(
            IDENTITY,
            target=ConnectorId(EvseId(StationId("different-station"), "1"), "1"),
        ),
    ],
)
async def test_identity_mismatch_never_restores_or_grants_zero_budget(
    storage, identity
):
    wrapper, _, _ = storage
    await wrapper.begin_scope(
        IDENTITY, GridImportPolicy(72000, active=True), explicit_new_scope=True
    )
    restored = recover(wrapper, identity)
    assert restored.policy is None and restored.reason
    assert recover(wrapper).policy.energy_ws == 72000


@pytest.mark.parametrize(
    "damage",
    [
        "document_version",
        "document_boolean_version",
        "record_shape",
        "duplicate_target",
        "wrong_entry",
        "unknown_policy",
        "boolean_policy_version",
        "wrong_convention",
        "unknown_state_version",
        "negative_energy",
        "zero_denominator",
        "boolean_numerator",
        "invalid_identity",
        "contradictory_pause",
        "fresh_pending_off",
        "missing_restart_required",
        "integer_restart_required",
        "paused_without_restart_required",
    ],
)
async def test_corrupt_or_unknown_history_remains_inhibited(storage, damage):
    wrapper, hass, _ = storage
    await wrapper.begin_scope(
        IDENTITY, GridImportPolicy(72000, active=True), explicit_new_scope=True
    )
    data = deepcopy(wrapper._dump())
    record = data["records"][0]
    if damage == "document_version":
        data["version"] = 999
    elif damage == "document_boolean_version":
        data["version"] = True
    elif damage == "record_shape":
        record["extra"] = 1
    elif damage == "duplicate_target":
        data["records"].append(deepcopy(record))
    elif damage == "wrong_entry":
        record["identity"]["entry_id"] = "other-entry"
    elif damage == "unknown_policy":
        record["policy"]["version"] = 999
    elif damage == "boolean_policy_version":
        record["policy"]["version"] = True
    elif damage == "wrong_convention":
        record["policy"]["sample_convention"] = "current_sample"
    elif damage == "unknown_state_version":
        record["state"]["version"] = 999
    elif damage == "negative_energy":
        record["state"]["energy_ws"]["numerator"] = -1
    elif damage == "zero_denominator":
        record["state"]["energy_ws"]["denominator"] = 0
    elif damage == "boolean_numerator":
        record["state"]["energy_ws"]["numerator"] = True
    elif damage == "invalid_identity":
        record["identity"]["station_identity"]["serial"] = 5
    elif damage == "contradictory_pause":
        record["state"]["paused"] = True
    elif damage == "fresh_pending_off":
        record["state"]["off_pending"] = True
    elif damage == "missing_restart_required":
        del record["state"]["restart_required"]
    elif damage == "integer_restart_required":
        record["state"]["restart_required"] = 1
    elif damage == "paused_without_restart_required":
        record["state"].update(
            active=False,
            paused=True,
            pause_reason=ECONOMIC_REASON,
            restart_required=False,
        )
    await wrapper.store.async_save(data)
    second = EconomicStorage(hass, "entry")
    await second.load()
    recovered = recover(second)
    assert recovered.policy is None and recovered.reason == "economic_history_corrupt"
    assert second._records == {}
    assert await second.close()  # Does not overwrite unknown history with zeros.


async def test_exhaustion_is_durable_before_off_and_rejected_off_keeps_state(storage):
    wrapper, _, tmp_path = storage
    policy = GridImportPolicy(179999, active=True)
    await wrapper.begin_scope(IDENTITY, policy, explicit_new_scope=True)
    observe(policy, 0, 1000)
    observe(policy, Fraction(1, 1000), 1000)
    assert policy.energy_ws == 180000
    policy.mark_off_pending()
    wrapper.checkpoint(IDENTITY, policy)
    assert await wrapper.flush()
    state = disk_data(tmp_path)["data"]["records"][0]["state"]
    assert state["energy_ws"] == {"numerator": 180000, "denominator": 1}
    assert state["off_pending"] and state["pause_reason"] == ECONOMIC_REASON
    # A BUSY/rejected command leaves policy lifecycle unchanged.
    wrapper.checkpoint(IDENTITY, policy)
    assert recover(wrapper).policy.exhausted and recover(wrapper).policy.off_pending
    policy.confirm_off(at=Fraction(1, 1000))
    wrapper.checkpoint(IDENTITY, policy)
    assert await wrapper.flush()
    paused = recover(wrapper).policy
    assert paused.exhausted and paused.paused and not paused.active


class Scheduled:
    def __init__(self, deadline, callback):
        self.deadline = deadline
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def when(self):
        return self.deadline


async def test_checkpoint_is_first_dirty_plus_exact_five_seconds_without_trailing(
    storage, monkeypatch
):
    wrapper, hass, tmp_path = storage
    policy = GridImportPolicy(active=True)
    await wrapper.begin_scope(IDENTITY, policy, explicit_new_scope=True)
    clock = [0]
    scheduled = []

    def call_later(delay, callback):
        item = Scheduled(clock[0] + delay, callback)
        scheduled.append(item)
        return item

    with monkeypatch.context() as patch:
        patch.setattr(hass.loop, "call_later", call_later)
        policy.energy_ws = Fraction(10000)
        wrapper.checkpoint(IDENTITY, policy)
        first = wrapper._timer
        assert first.when() == CHECKPOINT_SECONDS == 5
        clock[0] = Fraction(4999, 1000)
        policy.energy_ws = Fraction(20000)
        wrapper.checkpoint(IDENTITY, policy)
        assert wrapper._timer is first and len(scheduled) == 1
        assert (
            disk_data(tmp_path)["data"]["records"][0]["state"]["energy_ws"]["numerator"]
            == 0
        )
        clock[0] = 5
        first.callback()
        await asyncio.gather(*wrapper._tasks)
        assert (
            disk_data(tmp_path)["data"]["records"][0]["state"]["energy_ws"]["numerator"]
            == 20000
        )
        clock[0] = Fraction(5001, 1000)
        policy.energy_ws = Fraction(30000)
        wrapper.checkpoint(IDENTITY, policy)
        assert wrapper._timer.when() == Fraction(10001, 1000)


async def test_unchanged_snapshot_does_not_schedule_extra_write(storage):
    wrapper, _, _ = storage
    policy = GridImportPolicy(72000, active=True)
    await wrapper.begin_scope(IDENTITY, policy, explicit_new_scope=True)
    for _ in range(20):
        wrapper.checkpoint(IDENTITY, policy)
    assert wrapper._timer is None
    assert wrapper._revision == wrapper._saved_revision


async def test_crash_before_checkpoint_restores_only_last_verified_durable_segment(
    storage, monkeypatch
):
    wrapper, hass, tmp_path = storage
    policy = GridImportPolicy(active=True)
    await wrapper.begin_scope(IDENTITY, policy, explicit_new_scope=True)
    clock = [10]

    def call_later(delay, callback):
        return Scheduled(clock[0] + delay, callback)

    with monkeypatch.context() as patch:
        patch.setattr(hass.loop, "call_later", call_later)
        observe(policy, 0, 1000)
        observe(policy, 10, 1000)
        wrapper.checkpoint(IDENTITY, policy)
        timer = wrapper._timer
        assert timer.when() == 15
        clock[0] = Fraction(14999, 1000)
        crashed = EconomicStorage(hass, "entry")
        await crashed.load()
        restored = recover(crashed).policy
        assert restored.energy_ws == 0
        assert restored.last_import_w is None
        # At healthy IO cadence, this lost supported 10s segment lies within
        # 10s sample closure +5s scheduled checkpoint exposure at observed P.
        assert policy.energy_ws - restored.energy_ws == 1000 * 10 <= 1000 * 15
        assert await crashed.close()
        clock[0] = 15
        timer.callback()
        await asyncio.gather(*wrapper._tasks)
        assert (
            disk_data(tmp_path)["data"]["records"][0]["state"]["energy_ws"]["numerator"]
            == 10000
        )


async def test_teardown_flushes_dirty_supported_energy_and_removes_timer(storage):
    wrapper, _, tmp_path = storage
    policy = GridImportPolicy(active=True)
    await wrapper.begin_scope(IDENTITY, policy, explicit_new_scope=True)
    observe(policy, 0, 1000)
    observe(policy, 10, 1000)
    wrapper.checkpoint(IDENTITY, policy)
    timer = wrapper._timer
    assert await wrapper.close()
    assert wrapper._timer is None and timer.cancelled()
    assert (
        disk_data(tmp_path)["data"]["records"][0]["state"]["energy_ws"]["numerator"]
        == 10000
    )


async def test_explicit_exit_and_new_transaction_never_leak_old_energy(storage):
    wrapper, _, _ = storage
    await wrapper.begin_scope(
        IDENTITY, GridImportPolicy(180000, active=True), explicit_new_scope=True
    )
    other = replace(IDENTITY, transaction_id="new-transaction")
    wrapper.forget(other, explicit_scope_exit=True)
    assert recover(wrapper).policy.exhausted
    with pytest.raises(ValueError, match="explicit lifecycle exit"):
        wrapper.forget(IDENTITY, explicit_scope_exit=False)
    wrapper.forget(IDENTITY, explicit_scope_exit=True)
    assert await wrapper.flush()
    assert recover(wrapper).policy is None
    assert await wrapper.begin_scope(other, GridImportPolicy(), explicit_new_scope=True)
    assert recover(wrapper, other).policy.energy_ws == 0
    assert recover(wrapper).policy is None


async def test_failed_write_is_visible_and_never_grants_automatic_positive_recovery(
    storage, monkeypatch
):
    wrapper, _, _ = storage
    policy = GridImportPolicy(179999, active=True)
    await wrapper.begin_scope(IDENTITY, policy, explicit_new_scope=True)
    policy.energy_ws = Fraction(180000)
    policy.mark_off_pending()
    wrapper.checkpoint(IDENTITY, policy)

    def failed_write(*_):
        raise WriteError("simulated storage failure")

    with monkeypatch.context() as patch:
        patch.setattr(wrapper.store, "_write_prepared_data", failed_write)
        assert not await wrapper.flush()  # Store normally logs and swallows this error.
        assert wrapper.failure_reason == "economic_history_write_failed"
        assert recover(wrapper).policy is None
        assert wrapper._records[IDENTITY.key]["state"]["off_pending"]
        assert policy.exhausted  # Caller must still issue required OFF.
    assert await wrapper.flush()
    assert recover(wrapper).policy.exhausted


async def test_initial_write_failure_does_not_become_a_fresh_allowance(
    storage, monkeypatch
):
    wrapper, _, _ = storage

    def failed_write(*_):
        raise WriteError("simulated initial checkpoint failure")

    with monkeypatch.context() as patch:
        patch.setattr(wrapper.store, "_write_prepared_data", failed_write)
        assert not await wrapper.begin_scope(
            IDENTITY, GridImportPolicy(), explicit_new_scope=True
        )
        assert recover(wrapper).policy is None
    assert await wrapper.flush()
    assert recover(wrapper).restored


async def test_signed_restart_reference_survives_reset_pause_and_store_reload(storage):
    wrapper, hass, tmp_path = storage
    policy = GridImportPolicy(180000, active=True)
    policy.capture_restart_reference(Fraction(-30101, 7), Fraction(412119, 100))
    reference = policy.restart_reference.serialize()
    assert await wrapper.begin_scope(IDENTITY, policy, explicit_new_scope=True)
    policy.mark_off_pending()
    policy.confirm_off(at=0)
    for at in (0, 10, 16):
        observe(policy, at, 0)
    assert policy.energy_ws == 0 and policy.paused
    assert policy.restart_reference.serialize() == reference
    wrapper.checkpoint(IDENTITY, policy)
    assert await wrapper.flush()
    saved = disk_data(tmp_path)["data"]["records"][0]
    assert saved["state"]["restart_reference"] == reference
    assert saved["policy"]["battery_uncertainty_reserve_w"] == 100
    assert saved["policy"]["meaningful_change_deadband_w"] == 100
    reloaded = EconomicStorage(hass, "entry")
    await reloaded.load()
    restored = recover(reloaded).policy
    assert restored.energy_ws == 0 and restored.paused
    assert restored.restart_reference.serialize() == reference
    assert restored.restart_reference.capacity_w == Fraction(-30101, 7)
    assert restored.restart_reference.minimum_w == Fraction(412119, 100)
    assert restored.threshold_w is None and restored.below_seconds == 0
    assert await reloaded.close()


async def test_reference_capture_is_durable_once_and_positive_ack_clears_it(storage):
    wrapper, _, tmp_path = storage
    policy = GridImportPolicy(180000, active=True)
    policy.capture_restart_reference(3770, 1380)
    await wrapper.begin_scope(IDENTITY, policy, explicit_new_scope=True)
    original = policy.restart_reference.serialize()
    policy.capture_restart_reference(500, 4140)
    wrapper.checkpoint(IDENTITY, policy)
    assert wrapper._timer is None
    assert recover(wrapper).policy.restart_reference.serialize() == original
    policy.confirm_off(at=0)
    for at in (0, 10, 16):
        observe(policy, at, 0)
    assert policy.energy_ws == 0 and policy.restart_reference is not None
    policy.confirm_active(at=20)  # Only a confirmed positive command clears history.
    wrapper.checkpoint(IDENTITY, policy)
    assert await wrapper.flush()
    state = disk_data(tmp_path)["data"]["records"][0]["state"]
    assert state["restart_reference"] is None
    assert recover(wrapper).policy.restart_reference is None


async def test_reference_capture_marks_durable_checkpoint_dirty_without_extra_wh(
    storage,
):
    wrapper, _, tmp_path = storage
    policy = GridImportPolicy(180000, active=True)
    await wrapper.begin_scope(IDENTITY, policy, explicit_new_scope=True)
    policy.capture_restart_reference(3770, 1380)
    wrapper.checkpoint(IDENTITY, policy)
    assert wrapper._timer is not None
    assert await wrapper.flush()
    assert disk_data(tmp_path)["data"]["records"][0]["state"]["restart_reference"]
    assert recover(wrapper).policy.energy_ws == 180000


@pytest.mark.parametrize(
    "damage",
    [
        "unknown_version",
        "wrong_model",
        "boolean_capacity",
        "capacity_zero_denominator",
        "zero_minimum",
        "negative_minimum",
        "boolean_minimum",
        "minimum_zero_denominator",
    ],
)
async def test_invalid_restart_reference_inhibits_recovery(storage, damage):
    wrapper, hass, _ = storage
    policy = GridImportPolicy(180000, active=True)
    policy.capture_restart_reference(3770, 1380)
    await wrapper.begin_scope(IDENTITY, policy, explicit_new_scope=True)
    data = deepcopy(wrapper._dump())
    reference = data["records"][0]["state"]["restart_reference"]
    if damage == "unknown_version":
        reference["version"] = 999
    elif damage == "wrong_model":
        reference["model"] = "budget_without_grid_neutrality"
    elif damage == "boolean_capacity":
        reference["capacity_w"]["numerator"] = True
    elif damage == "capacity_zero_denominator":
        reference["capacity_w"]["denominator"] = 0
    elif damage == "zero_minimum":
        reference["minimum_w"]["numerator"] = 0
    elif damage == "negative_minimum":
        reference["minimum_w"]["numerator"] = -1380
    elif damage == "boolean_minimum":
        reference["minimum_w"]["numerator"] = True
    elif damage == "minimum_zero_denominator":
        reference["minimum_w"]["denominator"] = 0
    await wrapper.store.async_save(data)
    reloaded = EconomicStorage(hass, "entry")
    await reloaded.load()
    restored = recover(reloaded)
    assert restored.policy is None and restored.reason == "economic_history_corrupt"
    assert await reloaded.close()


async def test_old_record_without_restart_model_metadata_stays_inhibited(storage):
    wrapper, hass, _ = storage
    await wrapper.begin_scope(
        IDENTITY, GridImportPolicy(180000, active=True), explicit_new_scope=True
    )
    data = deepcopy(wrapper._dump())
    del data["records"][0]["policy"]["restart_model"]
    await wrapper.store.async_save(data)
    reloaded = EconomicStorage(hass, "entry")
    await reloaded.load()
    assert recover(reloaded).policy is None
    assert await reloaded.close()


async def test_explicit_target_retirement_can_find_persisted_identity_after_reload(
    storage,
):
    wrapper, hass, _ = storage
    await wrapper.begin_scope(
        IDENTITY, GridImportPolicy(180000, active=True), explicit_new_scope=True
    )
    reloaded = EconomicStorage(hass, "entry")
    await reloaded.load()
    identity = reloaded.identity_for_target(IDENTITY.target)
    assert identity == IDENTITY
    assert reloaded.identity_for_target(replace(IDENTITY.target, value="2")) is None
    reloaded.forget(identity, explicit_scope_exit=True)
    assert await reloaded.flush()
    assert reloaded.identity_for_target(IDENTITY.target) is None
    assert recover(reloaded).policy is None
    assert await reloaded.close()


async def test_corrupt_document_identity_lookup_never_invents_retirement_proof(storage):
    wrapper, hass, _ = storage
    await wrapper.begin_scope(
        IDENTITY, GridImportPolicy(72000, active=True), explicit_new_scope=True
    )
    data = deepcopy(wrapper._dump())
    data["records"][0]["identity"]["station_identity"]["serial"] = 123
    await wrapper.store.async_save(data)
    reloaded = EconomicStorage(hass, "entry")
    await reloaded.load()
    assert reloaded.identity_for_target(IDENTITY.target) is None
    assert recover(reloaded).policy is None
    assert await reloaded.close()
