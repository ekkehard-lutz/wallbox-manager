"""Read-only discovery orchestration shared by version-specific wire adapters.

Lifecycle/boot pattern adapted from lbbrhzn/ocpp chargepoint.py and version handlers
at 848407c11ff659ce59779a99ce69984bbb0e3ce1. Copyright (c) 2021 lbbrhzn, MIT.
See ../../../THIRD_PARTY_NOTICES.md and docs/upstream-ocpp-analysis.md.
"""

import asyncio
from datetime import UTC, datetime

from ocpp.exceptions import OCPPError
from websockets.exceptions import ConnectionClosed

from ....core.capabilities import CapabilityEvidence, EvidenceState
from ....core.events import StationIdentity


def evidence(state: EvidenceState, source: str, reason: str | None = None):
    return CapabilityEvidence(state, source, datetime.now(UTC), reason)


class DiscoveryAdapter:
    """Mixin over a specific ocpp library ChargePoint, never a core object."""

    def __init__(
        self, station_id, connection, runtime, session, token, response_timeout=10
    ):
        super().__init__(station_id, connection, response_timeout=response_timeout)
        self.runtime = runtime
        self.session = session
        self.token = token
        self.discovery_task = None
        self._boot_pending = False
        self._attempt = 0

    async def call(self, *args, **kwargs):
        """Let a sent request settle even when its discovery attempt is replaced.

        The library serializes calls with a lock and consumes their responses
        from one queue. Cancelling that consumer releases the lock while its
        response is still in flight; the next call would discard the old reply.
        Shield only the session-owned call, not the discovery attempt. Teardown
        still cancels and joins both, and stale discovery remains token-fenced.
        """
        pending = self.session.spawn(super().call(*args, **kwargs))
        return await asyncio.shield(pending)

    def register_boot(self, identity: StationIdentity) -> None:
        token = self.runtime.boot(self.token, identity)
        if token is not None:
            self.token = token
            self._boot_pending = True
            if self.discovery_task is not None:
                self.discovery_task.cancel()

    async def _handle_call(self, msg):
        await super()._handle_call(msg)
        # Start outbound discovery only after the BootNotification response.
        if self._boot_pending:
            self._boot_pending = False
            self.start_discovery()

    def start_discovery(self) -> None:
        if self.discovery_task is not None:
            self.discovery_task.cancel()
        self._attempt += 1
        self.discovery_task = self.session.spawn(
            self._discover(self.token, self._attempt)
        )

    def publish(
        self, token, attempt, state, schedule, evses=(), connectors=(), electrical=()
    ):
        if attempt == self._attempt:
            self.runtime.discover(
                token,
                discovery=state,
                charging_schedule=schedule,
                evses=tuple(evses),
                connectors=tuple(connectors),
                electrical=tuple(electrical),
            )

    async def _discover(self, token, attempt):
        source = f"ocpp{self._ocpp_version}:discovery"
        self.publish(
            token,
            attempt,
            evidence(EvidenceState.UNKNOWN, source, "in_progress"),
            evidence(EvidenceState.UNKNOWN, source),
        )
        try:
            await self.discover(token, attempt)
        except asyncio.CancelledError:
            raise
        except ConnectionClosed:
            return  # Transport finalizer invalidates the entire connection.
        except (TimeoutError, OCPPError, ValueError, TypeError, KeyError) as exc:
            # Unsupported discovery is not proof that charging is unsupported.
            state = (
                EvidenceState.UNSUPPORTED
                if getattr(exc, "code", "") in ("NotImplemented", "NotSupported")
                else EvidenceState.DEGRADED
            )
            self.publish(
                token,
                attempt,
                evidence(state, source, type(exc).__name__),
                evidence(EvidenceState.UNKNOWN, source, "discovery_incomplete"),
            )
