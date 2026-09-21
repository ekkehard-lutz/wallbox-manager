"""Generic snapshot store and synchronous generation fencing for adapter events."""

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from .core.capabilities import CapabilityEvidence, CapabilitySnapshot, EvidenceState
from .core.events import SessionToken, StationIdentity, StationSnapshot
from .core.models import ConnectorId, EvseId, StationId

_LOGGER = logging.getLogger(__name__)


class Runtime:
    """Owned by one config entry; no protocol objects escape through snapshots."""

    def __init__(self) -> None:
        self.runtime_id = str(uuid4())
        self._stations: dict[StationId, StationSnapshot] = {}
        self._listeners: set[Callable[[StationSnapshot], None]] = set()

    @property
    def stations(self) -> tuple[StationSnapshot, ...]:
        return tuple(
            self._stations[k] for k in sorted(self._stations, key=lambda s: s.value)
        )

    def get(self, station: StationId) -> StationSnapshot | None:
        return self._stations.get(station)

    def subscribe(
        self, listener: Callable[[StationSnapshot], None]
    ) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _publish(self, snapshot: StationSnapshot) -> None:
        self._stations[snapshot.token.station] = snapshot
        for listener in tuple(self._listeners):
            try:
                listener(snapshot)
            except Exception:
                _LOGGER.exception("Snapshot subscriber failed")

    def current(self, token: SessionToken) -> bool:
        state = self.get(token.station)
        return state is not None and state.connected and state.token == token

    def _reset(
        self,
        token: SessionToken,
        identity: StationIdentity,
        connected: bool,
        reason: str,
    ) -> StationSnapshot:
        old = self.get(token.station)
        now = datetime.now(UTC)
        unknown = CapabilityEvidence(EvidenceState.UNKNOWN, "runtime", now, reason)
        return StationSnapshot(
            token,
            connected,
            identity,
            old.evses if old else (),
            old.connectors if old else (),
            CapabilitySnapshot(
                token.station,
                identity.firmware,
                token.connection_generation,
                token.boot_generation,
                old.capabilities.revision + 1 if old else 1,
                now,
                "runtime",
                (),
                unknown,
            ),
            unknown,
            unknown,
        )

    def connect(self, station: StationId) -> SessionToken:
        old = self.get(station)
        token = SessionToken(
            self.runtime_id,
            station,
            old.token.connection_generation + 1 if old else 1,
            old.token.boot_generation if old else 0,
        )
        self._publish(
            self._reset(
                token, old.identity if old else StationIdentity(), True, "connected"
            )
        )
        return token

    def boot(
        self, token: SessionToken, identity: StationIdentity
    ) -> SessionToken | None:
        if not self.current(token):
            return None
        token = replace(token, boot_generation=token.boot_generation + 1)
        self._publish(self._reset(token, identity, True, "boot_notification"))
        return token

    def disconnect(self, token: SessionToken) -> None:
        if self.current(token):
            self._publish(
                self._reset(
                    token, self.get(token.station).identity, False, "disconnected"
                )
            )

    def discover(
        self,
        token: SessionToken,
        *,
        discovery: CapabilityEvidence,
        charging_schedule: CapabilityEvidence,
        evses: tuple[EvseId, ...] = (),
        connectors: tuple[ConnectorId, ...] = (),
    ) -> bool:
        if not self.current(token):
            return False
        old = self.get(token.station)
        if any(e.station != token.station for e in evses) or any(
            c.evse.station != token.station for c in connectors
        ):
            raise ValueError("discovery identity belongs to another station")
        known_evses = set(old.evses) | set(evses) | {c.evse for c in connectors}
        known_connectors = set(old.connectors) | set(connectors)
        self._publish(
            replace(
                old,
                evses=tuple(sorted(known_evses, key=lambda e: e.value)),
                connectors=tuple(
                    sorted(known_connectors, key=lambda c: (c.evse.value, c.value))
                ),
                capabilities=replace(
                    old.capabilities,
                    revision=old.capabilities.revision + 1,
                    observed_at=discovery.observed_at,
                    source=discovery.source,
                ),
                charging_schedule=charging_schedule,
                discovery=discovery,
            )
        )
        return True
