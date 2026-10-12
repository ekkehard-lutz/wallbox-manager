"""Identity-bound PV economics with non-trailing Home Assistant checkpoints.

Only verified energy and command lifecycle are durable. Recovery supplies no
authority, interval anchor, below-threshold dwell or automatic restart approval.
Missing history is unknown, never an implicit new charging allowance.
"""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass

from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .core.events import StationIdentity
from .core.models import ConnectorId, EvseId, StationId
from .pv_budget import UNCERTAINTY_W
from .pv_economy import (
    LIMIT_WS,
    MAX_INTERVAL_SECONDS,
    POLICY_VERSION,
    RESET_SECONDS,
    RESTART_MODEL,
    RESTART_REFERENCE_VERSION,
    SAMPLE_CONVENTION,
    GridImportPolicy,
)
from .pv_regulators import GRID_DEADBAND_W

STORAGE_VERSION = 1
CHECKPOINT_SECONDS = 5
POLICY_METADATA = {
    "version": POLICY_VERSION,
    "scope": "pv_charging",
    "limit_ws": int(LIMIT_WS),
    "max_interval_seconds": int(MAX_INTERVAL_SECONDS),
    "reset_seconds": int(RESET_SECONDS),
    "reset_strict": True,
    "sample_convention": SAMPLE_CONVENTION,
    "restart_model": RESTART_MODEL,
    "restart_reference_version": RESTART_REFERENCE_VERSION,
    "battery_uncertainty_reserve_w": int(UNCERTAINTY_W),
    "meaningful_change_deadband_w": GRID_DEADBAND_W,
}
_LOGGER = logging.getLogger(__name__)


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("economic identity requires nonempty text")
    return value


@dataclass(frozen=True)
class EconomicIdentity:
    """Persisted identity, matched against live ownership and transaction proof."""

    entry_id: str
    target: ConnectorId
    station_identity: StationIdentity
    transaction_id: str

    def __post_init__(self):
        _text(self.entry_id)
        _text(self.transaction_id)
        if not isinstance(self.target, ConnectorId) or not isinstance(
            self.station_identity, StationIdentity
        ):
            raise ValueError("invalid economic station/target identity")

    @property
    def key(self):
        return json.dumps(
            [
                self.entry_id,
                self.target.station.value,
                self.target.evse.value,
                self.target.value,
            ],
            separators=(",", ":"),
        )

    def serialize(self):
        return {
            "entry_id": self.entry_id,
            "station": self.target.station.value,
            "evse": self.target.evse.value,
            "connector": self.target.value,
            "station_identity": asdict(self.station_identity),
            "transaction": self.transaction_id,
        }

    @classmethod
    def restore(cls, record):
        if not isinstance(record, dict) or set(record) != {
            "entry_id",
            "station",
            "evse",
            "connector",
            "station_identity",
            "transaction",
        }:
            raise ValueError("invalid stored economic identity")
        metadata = record["station_identity"]
        if (
            not isinstance(metadata, dict)
            or set(metadata) != {"vendor", "model", "serial", "firmware"}
            or any(v is not None and not isinstance(v, str) for v in metadata.values())
        ):
            raise ValueError("invalid stored station metadata")
        identity = cls(
            _text(record["entry_id"]),
            ConnectorId(
                EvseId(StationId(_text(record["station"])), _text(record["evse"])),
                _text(record["connector"]),
            ),
            StationIdentity(**metadata),
            _text(record["transaction"]),
        )
        if identity.serialize() != record:
            raise ValueError("noncanonical stored economic identity")
        return identity


@dataclass(frozen=True)
class EconomicRecovery:
    policy: GridImportPolicy | None
    reason: str | None

    @property
    def restored(self):
        return self.policy is not None and self.reason is None


class _CheckedStore(Store):
    """Observe write errors that Store logs instead of propagating to async_save."""

    write_succeeded = False

    async def _async_write_data(self, data):
        self.write_succeeded = False
        await super()._async_write_data(data)
        self.write_succeeded = True


class EconomicStorage:
    """One entry's verified policy states; callers retain command/owner fences.

    The first dirty event schedules a checkpoint five seconds later. Subsequent
    measurements update that checkpoint without moving its deadline. Flush at
    exhaustion before requesting OFF, confirmed pause and teardown; a False
    result inhibits future automatic positives but must never suppress OFF.
    Ordinary IO delay adds to this scheduling bound. Without an actual site
    power cap, the bound is a time exposure, not a universal Wh guarantee.
    """

    def __init__(self, hass, entry_id):
        self.hass = hass
        self.entry_id = _text(entry_id)
        self.store = _CheckedStore(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}.pv_economy",
            private=True,
            atomic_writes=True,
        )
        self.loaded = False
        self.closed = False
        self.failure_reason = None
        self._records = {}
        self._load_reason = "economic_history_missing"
        self._revision = 0
        self._saved_revision = 0
        self._timer = None
        self._tasks = set()
        self._lock = asyncio.Lock()

    async def load(self):
        if self.loaded:
            return
        try:
            data = await self.store.async_load()
            if data is not None:
                if (
                    not isinstance(data, dict)
                    or set(data) != {"version", "records"}
                    or type(data["version"]) is not int
                    or data["version"] != STORAGE_VERSION
                    or not isinstance(data["records"], list)
                ):
                    raise ValueError("invalid economic storage document")
                records = {}
                for record in data["records"]:
                    identity, normalized = self._validate(record)
                    if identity.key in records:
                        raise ValueError("duplicate economic target")
                    records[identity.key] = normalized
                self._records = records
                self._load_reason = None
        except Exception:
            self._load_reason = "economic_history_corrupt"
            _LOGGER.exception("PV economic history could not be safely loaded")
        self.loaded = True

    def _validate(self, record):
        if not isinstance(record, dict) or set(record) != {
            "identity",
            "policy",
            "state",
        }:
            raise ValueError("invalid economic record")
        identity = EconomicIdentity.restore(record["identity"])
        if identity.entry_id != self.entry_id:
            raise ValueError("economic record belongs to another config entry")
        metadata = record["policy"]
        if (
            not isinstance(metadata, dict)
            or set(metadata) != set(POLICY_METADATA)
            or any(
                type(metadata[key]) is not type(value) or metadata[key] != value
                for key, value in POLICY_METADATA.items()
            )
        ):
            raise ValueError("economic policy metadata mismatch")
        policy = GridImportPolicy.restore(record["state"])
        return identity, self._record(identity, policy)

    @staticmethod
    def _record(identity, policy):
        if not isinstance(identity, EconomicIdentity) or not isinstance(
            policy, GridImportPolicy
        ):
            raise ValueError("economic checkpoint requires identity and policy")
        state = GridImportPolicy.restore(policy.serialize()).serialize()
        return {
            "identity": identity.serialize(),
            "policy": dict(POLICY_METADATA),
            "state": state,
        }

    def restore(self, identity, *, authority_valid, transaction_valid):
        """Call before either positive preparation or positive readback recovery.

        Both proofs must describe the live, connected, explicitly owned target;
        REMOTE authority alone is insufficient. An invalid/missing record never
        constructs a zero-energy policy. A restored policy still cannot authorize
        restart, and its exhausted state must be handled before any positive write.
        """
        if not self.loaded:
            return EconomicRecovery(None, "economic_history_not_loaded")
        if type(authority_valid) is not bool or not authority_valid:
            return EconomicRecovery(None, "economic_recovery_authority")
        if type(transaction_valid) is not bool or not transaction_valid:
            return EconomicRecovery(None, "economic_recovery_transaction")
        if self.failure_reason:
            return EconomicRecovery(None, self.failure_reason)
        if self._load_reason:
            return EconomicRecovery(None, self._load_reason)
        record = self._records.get(identity.key)
        if record is None:
            return EconomicRecovery(None, "economic_history_missing")
        if record["identity"] != identity.serialize():
            return EconomicRecovery(None, "economic_history_identity_mismatch")
        return EconomicRecovery(GridImportPolicy.restore(record["state"]), None)

    def identity_for_target(self, target):
        """Find validated identity for explicit scope retirement after reload.

        This lookup supplies no energy restoration or authority proof. Corrupt
        documents and unknown targets yield no invented transaction identity.
        """
        if not self.loaded or self._load_reason or not isinstance(target, ConnectorId):
            return None
        key = json.dumps(
            [self.entry_id, target.station.value, target.evse.value, target.value],
            separators=(",", ":"),
        )
        record = self._records.get(key)
        return EconomicIdentity.restore(record["identity"]) if record else None

    async def begin_scope(self, identity, policy, *, explicit_new_scope):
        """Create a budget only after explicit intent or proven new-session lifecycle.

        A persisted ON/reconnect is recovery, never proof of a new scope. The
        caller must await the initial save before granting automatic positives.
        """
        if type(explicit_new_scope) is not bool or not explicit_new_scope:
            raise ValueError("fresh budget requires proven explicit new scope")
        self._ensure_open(identity)
        self._records[identity.key] = self._record(identity, policy)
        self._load_reason = None
        self._changed()
        return await self.flush()

    def checkpoint(self, identity, policy):
        self._ensure_open(identity)
        old = self._records.get(identity.key)
        if old is None or old["identity"] != identity.serialize():
            raise ValueError("unknown economic scope requires explicit begin_scope")
        record = self._record(identity, policy)
        if record != old:
            self._records[identity.key] = record
            self._changed()

    def forget(self, identity, *, explicit_scope_exit):
        """End the matching scope after explicit OFF/exit or genuine session end."""
        if type(explicit_scope_exit) is not bool or not explicit_scope_exit:
            raise ValueError("economic history requires an explicit lifecycle exit")
        self._ensure_open(identity)
        record = self._records.get(identity.key)
        if record is not None and record["identity"] == identity.serialize():
            del self._records[identity.key]
            self._changed()

    def _ensure_open(self, identity):
        if not self.loaded or self.closed:
            raise ValueError("economic storage is not open")
        if (
            not isinstance(identity, EconomicIdentity)
            or identity.entry_id != self.entry_id
        ):
            raise ValueError("economic identity belongs to another entry")

    def _changed(self):
        self._revision += 1
        if self._timer is None:
            self._timer = self.hass.loop.call_later(
                CHECKPOINT_SECONDS, self._checkpoint_due
            )

    def _checkpoint_due(self):
        self._timer = None
        if self.closed:
            return
        task = self.hass.async_create_background_task(
            self.flush(), "PV economic checkpoint"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _dump(self):
        return {
            "version": STORAGE_VERSION,
            "records": [self._records[key] for key in sorted(self._records)],
        }

    async def flush(self):
        """Return durability status; the required safe OFF proceeds even on False."""
        async with self._lock:
            self._cancel_timer()
            if self._revision == self._saved_revision:
                return self.failure_reason is None
            revision = self._revision
            self.store.write_succeeded = False
            try:
                await self.store.async_save(self._dump())
                saved = self.store.write_succeeded
            except Exception:
                saved = False
                _LOGGER.exception("PV economic checkpoint failed")
            if saved:
                self._saved_revision = revision
                self.failure_reason = None
            else:
                self.failure_reason = "economic_history_write_failed"
                _LOGGER.error("PV automatic recovery inhibited: economic save failed")
            if self._revision != self._saved_revision and not self.closed:
                if self._timer is None:
                    self._timer = self.hass.loop.call_later(
                        CHECKPOINT_SECONDS, self._checkpoint_due
                    )
            return saved

    async def close(self):
        self.closed = True
        self._cancel_timer()
        if self._tasks:
            await asyncio.gather(*self._tasks)
        return await self.flush()
