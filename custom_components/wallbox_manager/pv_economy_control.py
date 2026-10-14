"""Bind measured-import economics to existing PV ownership and command fences."""

import asyncio
from datetime import UTC, datetime
from fractions import Fraction

from homeassistant.core import callback

from .pv_budget import UNCERTAINTY_W, source_time
from .pv_economy import (
    ECONOMIC_REASON,
    GridImportPolicy,
    MeterSample,
    restart_capacity,
    restart_improved,
)
from .pv_economy_storage import EconomicIdentity, EconomicStorage
from .pv_regulators import GRID_DEADBAND_W
from .solver.operating_point import Reason

PV_PROFILES = ("PV_SURPLUS", "PV_MAXIMUM", "PV_OPTIMUM")


def exact_time(at):
    """Avoid floating epoch arithmetic in the source/acceptance contract."""
    if isinstance(at, datetime):
        delta = at - datetime(1970, 1, 1, tzinfo=UTC)
        return Fraction(delta.days * 86400 + delta.seconds) + Fraction(
            delta.microseconds, 1000000
        )
    return Fraction(str(at))


class PVEconomicControl:
    """One session budget; no authority acquisition or parallel command sender."""

    def economic_initialize(self):
        self.economic_store = EconomicStorage(self.hass, self.entry_id)
        self.economic_policies = {}
        self.economic_identities = {}
        self.economic_reasons = {}
        self.economic_off_at = {}
        self.economic_activation_pending = {}
        self.economic_positive_dispatches = {}
        self.economic_waits = set()
        self.economic_refreshing = set()
        self.economic_lifecycle_tasks = set()

        @callback
        def relevant(data):
            return data.get("entity_id") == self.references.get("grid_import_power")

        @callback
        def observe(event):
            if self.closed:
                return
            state = event.data.get("new_state")
            for target in tuple(self.economic_policies):
                if self.setting(target)["profile"] not in PV_PROFILES:
                    continue
                self.economic_observe(
                    target, state=state, reported_at=event.data.get("last_reported")
                )
                # Wake the existing serialized loop; observers never dispatch.
                self.optimum_wakes.setdefault(target, asyncio.Event()).set()
                self.control.publish(target)

        # Identical valued state reports still contain distinct meter generations.
        unchanged = self.hass.bus.async_listen(
            "state_reported", observe, event_filter=relevant
        )
        changed = self.hass.bus.async_listen(
            "state_changed", observe, event_filter=relevant
        )
        control_changed = self.control.subscribe(self.economic_refresh)

        def unsubscribe():
            unchanged()
            changed()
            control_changed()

        self.economic_unsubscribe = unsubscribe

    def economic_refresh(self, target):
        """Place voltage/current/phase threshold changes at their observation."""
        if (
            self.closed
            or target in self.economic_refreshing
            or target not in self.economic_policies
            or self.setting(target)["profile"] not in PV_PROFILES
        ):
            return
        self.economic_refreshing.add(target)
        try:
            self.economic_observe(target)
        finally:
            self.economic_refreshing.discard(target)

    async def economic_load(self):
        await self.economic_store.load()

    def economic_identity(self, target):
        runtime = self.control.runtime
        station = runtime.get(target.station)
        session = runtime.sessions.get(target)
        inputs = self.control.inputs(target)
        if not (
            self.can_control(target)
            and station
            and station.connected
            and session
            and session.active
            and session.external_transaction_id
            and inputs
            and inputs.transaction_id == session.external_transaction_id
        ):
            raise ValueError("economic authority/transaction unknown")
        return EconomicIdentity(
            self.entry_id, target, station.identity, session.external_transaction_id
        )

    async def economic_prepare(self, target, *, resume):
        """Restore before preparation, or durably begin an explicit user scope."""
        epoch = self.epochs.get(target, 0)
        try:
            identity = self.economic_identity(target)
        except ValueError:
            self.economic_reasons[target] = "economic_recovery_transaction"
            return False
        policy = self.economic_policies.get(target)
        if resume:
            if policy is None or self.economic_identities.get(target) != identity:
                recovery = self.economic_store.restore(
                    identity, authority_valid=True, transaction_valid=True
                )
                if not recovery.restored:
                    self.economic_reasons[target] = recovery.reason
                    await self.economic_recovery_off(target, recovery.reason)
                    return False
                policy = recovery.policy
                self.economic_policies[target] = policy
                self.economic_identities[target] = identity
                # No restored anchor or pre-reload OFF-response evidence is live.
                self.economic_off_at[target] = datetime.now(UTC)
        else:
            if policy is None or self.economic_identities.get(target) != identity:
                recovery = self.economic_store.restore(
                    identity, authority_valid=True, transaction_valid=True
                )
                if recovery.restored:
                    policy = recovery.policy
                    self.economic_off_at[target] = datetime.now(UTC)
                elif recovery.reason == "economic_history_missing":
                    # Fresh explicit activation, never an automatic recovery.
                    policy = GridImportPolicy()
                    self.economic_policies[target] = policy
                    self.economic_identities[target] = identity
                    saved = await self.economic_store.begin_scope(
                        identity, policy, explicit_new_scope=True
                    )
                    if (
                        self.closed
                        or self.epochs.get(target, 0) != epoch
                        or self.economic_policies.get(target) is not policy
                        or self.economic_identities.get(target) != identity
                    ):
                        # OFF/scope exit during durability must not resurrect
                        # the retired binding or mutate the newer intent.
                        return False
                    if not saved:
                        self.economic_reasons[target] = (
                            self.economic_store.failure_reason
                        )
                        return False
                else:
                    self.economic_reasons[target] = recovery.reason
                    await self.economic_recovery_off(target, recovery.reason)
                    return False
                self.economic_policies[target] = policy
                self.economic_identities[target] = identity
        self.economic_reasons.pop(target, None)
        self.economic_observe(target)
        if resume and (policy.exhausted or policy.off_pending):
            self.economic_reasons[target] = ECONOMIC_REASON
            if self.control.runtime.enabled(target) is True:
                # Required OFF is allowed even when persistence or inputs fail.
                self.control._edit(target, {"target_w": Fraction(0)})
                self.control.intent(target).policy_pause = True
                self.status[target] = ECONOMIC_REASON
                await self.control.apply_stored(target)
                self.launch(target)
            return False
        if self.economic_store.failure_reason:
            self.economic_reasons[target] = self.economic_store.failure_reason
            return False
        return True

    async def economic_recovery_off(self, target, reason):
        """Untrusted recovery never leaves an owned live charge with a new budget."""
        if self.control.runtime.enabled(target) is True and self.can_control(target):
            self.control._edit(target, {"target_w": Fraction(0)})
            self.status[target] = reason
            await self.control.apply_stored(target)

    async def economic_startup_prepare(self, target):
        """Continue an existing explicit activation once transaction proof arrives."""
        if target not in self.economic_activation_pending:
            return True
        ready = await self.economic_prepare(
            target, resume=self.economic_activation_pending[target]
        )
        if ready:
            self.economic_activation_pending.pop(target, None)
            self.economic_waits.discard(target)
        return ready

    def economic_defer_resume(self, target):
        """Keep persisted ON intent in the existing startup task, with meter wakes."""
        self.economic_activation_pending[target] = True
        self.economic_waits.add(target)
        session = self.control.runtime.sessions.get(target)
        self.enable_sessions[target] = session.session_id if session else None
        self.pv_schedule_startup(target, self.epochs.get(target, 0))

    def economic_minimum(self, target):
        """Physical settling is a phase restriction; past policy mode is not."""
        intent = self.control.intent(target)
        old_modes = intent.profile_modes
        regulator = self.optimum_regulators.get(target)
        inputs = self.control.inputs(target)
        modes = None
        if inputs and (
            self.control.phase_restricted(target)
            or (regulator and regulator.response_settling(self.monotonic()))
        ):
            # Permission to probe a locked phase is not proof it is available.
            modes = (inputs.current_mode,) if inputs.current_mode else ()
        try:
            intent.profile_modes = None
            return self.control.technical_minimum(target, dispatch_modes=modes)
        finally:
            intent.profile_modes = old_modes

    def economic_capacity(self, target):
        """Signed EV-load-neutral derivative of the existing economic H model."""
        from .pv_surplus import reading

        now = datetime.now(UTC)
        candidates = self.selected_power_states(target)
        if len(candidates) != 1:
            raise ValueError("economic EV measurement ambiguous")
        ev = reading(candidates[0], now)
        keys = ["grid_import_power", "grid_export_power"]
        if self.common_pv(target):
            keys += ["storage_discharge_power", "leistung_pv", "leistung_verbraucher"]
        states = [candidates[0]] + [
            self.hass.states.get(self.references.get(key, "")) for key in keys
        ]
        watts = [reading(state, now) for state in states[1:]]
        if min(ev, *watts) < 0:
            raise ValueError("negative economic input")
        ev_source = source_time(candidates[0])
        if any(source_time(state) < ev_source for state in states[1:]):
            raise ValueError("site evidence has not crossed EV acquisition boundary")
        if self.common_pv(target):
            imported, exported, discharge, pv, load = watts
            capacity = restart_capacity(
                ev,
                imported,
                exported,
                reserve_w=UNCERTAINTY_W,
                discharge_w=discharge,
                allowed_discharge_w=self.setting(target)["optimum_max_discharge_w"]
                if self.optimum_modes.get(target) == "FAST_DISCHARGE"
                else 0,
                pv_w=pv,
                load_w=load,
            )
        else:
            capacity = restart_capacity(ev, *watts, reserve_w=UNCERTAINTY_W)
        return capacity, states

    def economic_checkpoint(self, target):
        policy = self.economic_policies.get(target)
        identity = self.economic_identities.get(target)
        if policy is not None and identity is not None:
            try:
                self.economic_store.checkpoint(identity, policy)
            except ValueError:
                policy.invalidate("economic_scope_checkpoint_unknown")
                self.economic_reasons[target] = "economic_history_identity_mismatch"

    def economic_observe(self, target, *, state=None, reported_at=None):
        from .pv_surplus import reading

        policy = self.economic_policies.get(target)
        if policy is None:
            self.economic_reasons[target] = "economic_history_missing"
            return
        try:
            if self.economic_identity(target) != self.economic_identities.get(target):
                raise ValueError("economic scope identity changed")
            minimum = self.economic_minimum(target)
            if not (minimum and minimum.point and minimum.point.charging):
                if minimum is not None and (
                    minimum.point is not None
                    or minimum.reason == Reason.ELECTRICAL_LIMIT
                ):
                    policy.invalidate("technical_no_positive_point")
                    self.economic_reasons[target] = "optimum_no_positive_point"
                    self.economic_checkpoint(target)
                    return
                raise ValueError("technical minimum unknown")
            now, accepted = datetime.now(UTC), exact_time(self.monotonic())
            state = state or self.hass.states.get(
                self.references.get("grid_import_power", "")
            )
            imported = reading(state, now)
            source = exact_time(
                reported_at
                if reported_at is not None and not state.attributes.get("observed_at")
                else source_time(state)
            )
            threshold = minimum.point.offered_power_w / 2
            # Threshold changes at their causal observation, including duplicates.
            policy.set_threshold(threshold, at=accepted)
            point = self.control.confirmed_point(target)
            enabled = self.control.runtime.enabled(target)
            positive = self.economic_positive_dispatches.get(target)
            station = self.control.runtime.get(target.station)
            if positive and positive[2:] != (
                station.token,
                station.authority_revision,
                self.economic_identities.get(target),
                self.control.intent(target).generation,
            ):
                self.economic_positive_dispatches.pop(target, None)
                positive = None
            if enabled is False:
                if (policy.exhausted or policy.off_pending) and not policy.paused:
                    # Fresh device permission OFF proves the requested stop,
                    # including a disabled station recovered after HA restart.
                    policy.confirm_off(at=accepted)
                    self.economic_off_at[target] = now
                active = False
            elif enabled is True and positive is not None:
                candidates = self.selected_power_states(target)
                if (
                    not positive[1]
                    and len(candidates) == 1
                    and (
                        source_time(candidates[0]) > positive[0]
                        and reading(candidates[0], now) > GRID_DEADBAND_W
                    )
                ):
                    # An actual fresh response can precede the protocol ACK.
                    policy.confirm_active(at=accepted)
                    self.economic_positive_dispatches[target] = (
                        positive[0],
                        True,
                        *positive[2:],
                    )
                active = policy.active
            elif point and not point.charging:
                active = False
            elif enabled is True:
                # Retain exposure through uncertain readback; an initial scope
                # does not start charging merely because permission is enabled.
                active = bool(point and point.charging) or policy.active
            else:
                raise ValueError("charging permission unknown")
            policy.set_active(active, at=accepted)
            exhausted_before = policy.exhausted
            policy.observe(
                MeterSample((state.entity_id, source), source, accepted, imported),
                threshold_w=threshold,
                source_now=exact_time(now),
            )
            if policy.exhausted and not exhausted_before:
                try:
                    capacity, _ = self.economic_capacity(target)
                    policy.capture_restart_reference(
                        capacity, minimum.point.offered_power_w
                    )
                except ValueError, TypeError, ZeroDivisionError, OverflowError:
                    # Never fill an unknown STOP-time reference from later OFF data.
                    pass
            if policy.exhausted or policy.off_pending:
                self.economic_reasons[target] = ECONOMIC_REASON
            elif self.economic_store.failure_reason:
                self.economic_reasons[target] = self.economic_store.failure_reason
            else:
                reason = self.economic_reasons.get(target, "")
                if reason != "economic_conditions_unchanged" and not reason.startswith(
                    "economic_restart_"
                ):
                    self.economic_reasons.pop(target, None)
        except ValueError, TypeError, ZeroDivisionError, OverflowError:
            policy.invalidate("economic_measurement_or_identity_gap")
            self.economic_reasons[target] = "measurements_unavailable"
        self.economic_checkpoint(target)

    def economic_stop(self, target):
        self.economic_observe(target)
        policy = self.economic_policies.get(target)
        if policy and (policy.exhausted or policy.off_pending):
            self.economic_reasons[target] = ECONOMIC_REASON
            return True
        if self.economic_store.failure_reason:
            self.economic_reasons[target] = self.economic_store.failure_reason
        reason = self.economic_reasons.get(target, "")
        return bool(
            reason
            and reason != "economic_conditions_unchanged"
            and not reason.startswith("economic_restart_")
        )

    def economic_reason(self, target):
        return (
            self.economic_reasons.get(target)
            or self.economic_store.failure_reason
            or (
                ECONOMIC_REASON
                if (policy := self.economic_policies.get(target))
                and (policy.exhausted or policy.off_pending)
                else "economic_history_unavailable"
            )
        )

    def economic_restart_permits(self, target, point):
        policy = self.economic_policies.get(target)
        if policy is None or self.economic_store.failure_reason:
            self.economic_reasons[target] = "economic_history_unavailable"
            return False
        if policy.exhausted or policy.off_pending:
            self.economic_reasons[target] = ECONOMIC_REASON
            return False
        if not policy.restart_required:
            return True
        if policy.restart_reference is None:
            self.economic_reasons[target] = "economic_restart_reference_unknown"
            return False
        try:
            minimum = self.economic_minimum(target)
            if not (minimum and minimum.point and minimum.point.charging):
                self.economic_reasons[target] = (
                    "economic_restart_technical_minimum_unavailable"
                )
                return False
            # A timed phase probe cannot earn a cheaper cost before acceptance.
            # Real capacity improvement can still authorize the existing probe.
            compared_cost = max(point.offered_power_w, minimum.point.offered_power_w)
            capacity, states = self.economic_capacity(target)
            off_at = self.economic_off_at.get(target)
            if off_at is None or any(source_time(s) <= off_at for s in states):
                self.economic_reasons[target] = "economic_restart_waiting_off_response"
                return False
            from .pv_surplus import reading

            if reading(states[0], datetime.now(UTC)) > GRID_DEADBAND_W:
                self.economic_reasons[target] = "economic_restart_waiting_off_response"
                return False
            regulator = self.optimum_regulators.get(target)
            if (
                regulator
                and regulator.command_key is not None
                and not regulator.command_key[0]
                and not regulator.response_observed
            ):
                self.economic_reasons[target] = "economic_restart_waiting_off_response"
                return False
            if not restart_improved(
                policy.restart_reference,
                capacity,
                compared_cost,
                deadband_w=GRID_DEADBAND_W,
            ):
                self.economic_reasons[target] = (
                    "economic_restart_waiting_phase_feasibility"
                    if compared_cost > point.offered_power_w
                    else "economic_conditions_unchanged"
                )
                return False
        except ValueError, TypeError, ZeroDivisionError, OverflowError:
            self.economic_reasons[target] = "measurements_unavailable"
            return False
        self.economic_reasons.pop(target, None)
        return True

    async def economic_before_apply(self, target, point):
        policy = self.economic_policies.get(target)
        if policy is None:
            return
        if not point.charging and (policy.exhausted or policy.off_pending):
            confirmed = self.control.confirmed_point(target)
            if not (policy.paused and confirmed and not confirmed.charging):
                policy.mark_off_pending()
            self.economic_checkpoint(target)
            # Await durability, but IO failure cannot suppress a required OFF.
            await self.economic_store.flush()

    def economic_point_dispatched(self, target, point):
        if regulator := self.optimum_regulators.get(target):
            regulator.dispatched(point)
        policy = self.economic_policies.get(target)
        if policy and point.charging:
            station = self.control.runtime.get(target.station)
            self.economic_positive_dispatches[target] = (
                datetime.now(UTC),
                False,
                station.token,
                station.authority_revision,
                self.economic_identities.get(target),
                self.control.intent(target).generation,
            )
            if policy.active and self.control.runtime.enabled(target) is True:
                policy.confirm_active(at=exact_time(self.monotonic()))
            self.economic_checkpoint(target)
            return
        if policy and not point.charging and policy.off_pending:
            policy.mark_off_in_flight()
            self.economic_checkpoint(target)

    def economic_point_not_applied(self, target, point):
        policy = self.economic_policies.get(target)
        if point.charging:
            self.economic_positive_dispatches.pop(target, None)
        if policy and not point.charging and policy.off_in_flight:
            policy.mark_off_not_applied()
            self.economic_checkpoint(target)

    def economic_point_confirmed(self, target, point):
        policy = self.economic_policies.get(target)
        if policy is None:
            return
        if regulator := self.optimum_regulators.get(target):
            regulator.acknowledge(point, self.monotonic())
        if point.charging:
            if self.control.runtime.enabled(target) is True:
                policy.confirm_active(at=exact_time(self.monotonic()))
                self.economic_off_at.pop(target, None)
                self.economic_positive_dispatches.pop(target, None)
            else:
                # A disabled station's prepared limit has not restarted charging.
                policy.set_active(False, at=exact_time(self.monotonic()))
        else:
            if policy.exhausted or policy.off_pending or policy.restart_required:
                already_paused = policy.paused
                policy.confirm_off(at=exact_time(self.monotonic()))
                if not already_paused or target not in self.economic_off_at:
                    self.economic_off_at[target] = datetime.now(UTC)
            else:
                policy.set_active(False, at=exact_time(self.monotonic()))
        self.economic_checkpoint(target)

    async def economic_after_apply(self, target, point):
        if not point.charging:
            policy = self.economic_policies.get(target)
            if policy and policy.paused:
                await self.economic_store.flush()

    def economic_invalidate(self, target, *, preserve_evidence=False):
        """Revoke command authorization; same-scope task replacement keeps meters."""
        self.economic_activation_pending.pop(target, None)
        self.economic_waits.discard(target)
        self.economic_positive_dispatches.pop(target, None)
        if not preserve_evidence and (policy := self.economic_policies.get(target)):
            policy.invalidate("lifecycle_invalidated")

    async def economic_end_scope(self, target, *, expected=None):
        if expected is not None and self.economic_identities.get(target) != expected:
            return
        identity = self.economic_identities.pop(target, None)
        if identity is None:
            identity = self.economic_store.identity_for_target(target)
        self.economic_policies.pop(target, None)
        self.economic_reasons.pop(target, None)
        self.economic_off_at.pop(target, None)
        self.economic_positive_dispatches.pop(target, None)
        self.economic_waits.discard(target)
        if identity is not None:
            self.economic_store.forget(identity, explicit_scope_exit=True)
            await self.economic_store.flush()

    def economic_sessions_changed(self):
        for target in tuple(self.economic_policies):
            session = self.control.runtime.sessions.get(target)
            if session and not session.active and session.end_reason != "superseded":
                task = self.hass.async_create_background_task(
                    self.economic_end_scope(
                        target, expected=self.economic_identities.get(target)
                    ),
                    "PV economic session end",
                )
                self.economic_lifecycle_tasks.add(task)
                task.add_done_callback(self.economic_lifecycle_tasks.discard)

    def economic_attributes(self, target):
        policy = self.economic_policies.get(target)
        snapshot = policy.snapshot if policy else None
        minimum = (
            snapshot.threshold_w * 2
            if snapshot and snapshot.threshold_w and snapshot.coverage_reason is None
            else None
        )
        return {
            "pv_actual_grid_import_w": float(snapshot.import_w)
            if snapshot and snapshot.import_w is not None
            else None,
            "pv_technical_minimum_w": float(minimum) if minimum is not None else None,
            "pv_grid_import_threshold_w": float(snapshot.threshold_w)
            if snapshot
            and snapshot.threshold_w is not None
            and snapshot.coverage_reason is None
            else None,
            "pv_grid_import_energy_wh": float(snapshot.energy_wh) if snapshot else None,
            "pv_grid_import_below_seconds": float(snapshot.below_seconds)
            if snapshot
            else 0,
            "pv_economic_state": snapshot.state.value if snapshot else "uninitialized",
            "pv_economic_stop_reason": snapshot.pause_reason if snapshot else None,
            "pv_economic_off_in_flight": snapshot.off_in_flight if snapshot else None,
            "pv_economic_restart_required": snapshot.restart_required
            if snapshot
            else None,
            "pv_economic_inhibition": self.economic_reasons.get(target)
            or self.economic_store.failure_reason,
            "pv_economic_coverage_reason": snapshot.coverage_reason
            if snapshot
            else "economic_history_missing",
        }

    async def economic_close(self):
        self.economic_unsubscribe()
        if self.economic_lifecycle_tasks:
            await asyncio.gather(*self.economic_lifecycle_tasks)
        await self.economic_store.close()
