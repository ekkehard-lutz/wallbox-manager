"""Event-driven NETZ intent, serialized OCPP dispatch and one-shot deadlines."""

import asyncio
import hashlib
import json
import re
import time

from ocpp.charge_point import camel_to_snake_case, snake_to_camel_case
from ocpp.exceptions import OCPPError
from ocpp.v21 import call
from websockets.exceptions import ConnectionClosed

from .control import DISPATCH, Refused
from .grid_point import NOMINAL_VOLTAGE, Point, envelope, number, rounded_kw


def retry_delay(info):
    if not isinstance(info, dict) or info.get("reason_code") not in (
        "PhaseSwitchLockout",
        "ReenableLockout",
    ):
        return None
    raw = info.get("additional_info", "")
    if isinstance(raw, str) and re.fullmatch(r"remainingSeconds=[0-9]{1,238}", raw):
        return int(raw.split("=")[1]) + 5
    return 60


class Grid:
    def __init__(self, listener, settings):
        self.listener, self.state, self.settings = listener, listener.state, settings
        self.pv = None
        self.epoch = 0
        self.closed = False
        self.task = self.timer = self.schedule_timer = None
        self.deadline_tasks = set()
        self.attempted_edit_revision = None
        self.task_epoch = None
        self.action_task = None
        self.action_lock = asyncio.Lock()
        self.action_generation = 0
        self.allow_issue = False
        self.known_transaction = None
        self.last_authority = None
        self.signature = None
        self.status, self.reason = "waiting", None
        self.requested = self.confirmed = self.acknowledged = None
        self.confirmed_transaction = None
        self.point_uncertain = False
        self.retry_at = self.retry_deadline = None
        self.wall_time = time.time
        settings.wall_time = lambda: self.wall_time()
        self.call_later = lambda delay, callback: asyncio.get_running_loop().call_later(
            delay, callback
        )
        self._published = None
        self._publishing = False
        self.outcome_epoch = None
        self.removers = []
        self.removers.append(self.state.watch(self.state.messages.key, self.publish))
        station, evse, _ = self.state.binding
        self.station_scope = (station, None, None)
        for family, scope, roles in (
            (
                "station",
                self.station_scope,
                ("discovery", "control_authority", "physical_phases"),
            ),
            ("control", self.state.binding, ("charging_enabled",)),
            ("session", self.state.binding, ("active", "id")),
            (
                "capability",
                (station, evse, None),
                ("supported_phases", "minimum_current", "current_step"),
            ),
            (
                "capability",
                self.state.binding,
                ("maximum_current", "maximum_asymmetry_current"),
            ),
        ):
            for role in roles:
                self.removers.append(
                    self.state.watch(
                        (family, scope, role),
                        self.phase_changed
                        if role == "physical_phases"
                        else self.permission_changed
                        if role == "charging_enabled"
                        else self.changed,
                    )
                )
        for role in (
            "charging_profile",
            "power_approximation",
            "allowed_current_1p",
            "allowed_current_2p",
            "allowed_current_3p",
        ):
            self.removers.append(
                settings.watch(role, lambda role=role: self.edited(role))
            )

        self.removers.append(settings.watch("charge_release", self.release_changed))
        self.removers.append(
            settings.watch("grid_timing_edit", self.edit_window_changed)
        )
        self.removers.append(settings.watch("grid_timing_lease", self.schedule))
        settings.grid_equivalent = self.equivalent
        settings.grid_point = self.draft_point
        self.removers.append(
            settings.watch("grid_approved", lambda: self.committed(False))
        )
        self.removers.append(
            settings.watch("grid_equivalent", lambda: self.committed(True))
        )
        self.removers.append(
            settings.watch(
                "grid_timing", lambda: self.committed(True, timing_only=True)
            )
        )

    def permission_changed(self):
        # Hardware permission has no profile/start/stop side effects.
        if self.state.value(self.listener.control.key("ChargingEnabled")) is not True:
            self.invalidate()
            self.schedule()
        self.signature = self.fingerprint()
        self.publish()

    def release_changed(self):
        self.action_generation += 1
        self.allow_issue = True
        self.invalidate()
        self.signature = None
        self.changed()

    def edit_window_changed(self):
        self.schedule()
        self.publish()

    def action_state(self):
        released = self.settings.get("charge_release") is True
        pending = self.settings.approval_attributes()["grid_set_required"]
        grid = self.settings.get("charging_profile") == "NETZ"
        action = (
            "apply"
            if released and grid and pending
            else "stop"
            if released
            else "start"
        )
        reason = None
        if action != "stop":
            if not grid and not self.manager_ready():
                reason = "control_unavailable"
            elif not grid and (
                self.state.value(self.listener.control.key("ChargingEnabled"))
                is not True
            ):
                reason = "hardware_disabled"
            elif grid:
                try:
                    self.settings.validate(
                        "soll_power", self.settings.get("soll_power")
                    )
                    try:
                        point = self.draft_point()
                    except ValueError, TypeError, OverflowError:
                        # Unknown electrical context does not prevent a valid
                        # business countdown; execution remains independently gated.
                        point = None
                    if action == "start" and (
                        float(self.settings.get("soll_power")) <= 0
                        or (point is not None and point.amps <= 0)
                    ):
                        reason = "no_positive_current"
                except ValueError, TypeError, OverflowError:
                    reason = "no_positive_point"
        return {
            "charge_release": released,
            "charge_action_revision": self.action_revision(),
            "charge_action": action,
            "charge_action_ready": reason is None,
            "charge_action_blocked_reason": reason,
        }

    def draft_point(self, values=None):
        values = self.settings.values if values is None else values
        held = self.current_point()
        return envelope(self.state, self.settings).resolve(
            number(rounded_kw(values["power_kw"])) * 1000,
            self.settings.get("power_approximation"),
            held.phases if held and held.amps > 0 else None,
            selected=values.get("power_phases"),
        )

    def action_revision(self):
        """Opaque runtime-scoped context; no persisted revision or cyclic churn."""
        context = (
            self.state.runtime_id,
            self.action_generation,
            self.state.generation,
            self.transaction(),
            self.settings.get("charging_profile"),
            self.settings.get("charge_release"),
            self.settings.values.get("grid_draft_revision"),
            self.settings.approval_attributes()["grid_approved_revision"],
            self.manager_ready(),
            tuple(
                self.settings.get(role)
                for role in (
                    "power_approximation",
                    "allowed_current_1p",
                    "allowed_current_2p",
                    "allowed_current_3p",
                )
            ),
            self.state.value(self.listener.control.key("ChargingEnabled")),
        )
        return hashlib.sha256(json.dumps(context).encode()).hexdigest()

    async def explicit_action(self, action, profile, revision, incarnation):
        """Reject stale intent without reinterpreting its direction."""

        def validate():
            if (
                self.closed
                or incarnation != self.state.runtime_id
                or profile != self.settings.get("charging_profile")
                or revision != self.action_revision()
                or action not in ("start", "stop", "apply")
                or (action != "stop" and action != self.action_state()["charge_action"])
            ):
                raise ValueError("Superseded charge action")

        if action == "stop":
            # Stop must supersede a slow approval/dispatch, not queue behind it.
            validate()
            self.action_generation += 1
            self.invalidate()
            await self.stop_release()
            self.publish()
            return
        async with self.action_lock:
            validate()
            await self._action(action, validate)

    async def action(self):
        # Concurrent cards share one in-flight action, avoiding start-then-stop.
        if self.action_task and not self.action_task.done():
            return await asyncio.shield(self.action_task)
        self.action_task = asyncio.create_task(self.legacy_action())
        return await asyncio.shield(self.action_task)

    async def legacy_action(self):
        """Compatibility-only generic HA button; the card never uses this toggle."""
        async with self.action_lock:
            await self._action()

    async def _action(self, intended=None, validate=lambda: None):
        if self.settings.get("charge_release") and self.phase()[0] == "expired":
            if intended is not None:
                # The business scheduler owns expiry; stale Apply is never Stop.
                raise ValueError("Superseded charge action")
            await self.stop_release()
            return
        state = self.action_state()
        if intended is not None:
            state = {**state, "charge_action": intended}
        if not state["charge_action_ready"]:
            raise ValueError(state["charge_action_blocked_reason"])
        if state["charge_action"] == "stop":
            await self.stop_release()
        elif self.settings.get("charging_profile") == "NETZ":
            retry_deadline = self.retry_deadline
            previous_request = self.settings.values.get("grid_request")
            self.invalidate()
            # Persistence may be delayed; the old business deadline still wins.
            self.schedule()
            epochs = []

            def current():
                # Capture the epoch after acquiring the settings lock. A preceding
                # stop may still be completing its durable write when Start arrives.
                if not epochs:
                    validate()
                    if state["charge_action"] == "apply" and (
                        not self.settings.get("charge_release")
                        or self.settings.values.get("grid_request") != previous_request
                        or self.phase()[0] == "expired"
                    ):
                        return False
                    epochs.append(self.epoch)
                return (
                    epochs[0] == self.epoch
                    and not self.closed
                    and self.settings.get("charging_profile") == "NETZ"
                )

            try:
                equivalent, timing_only = await self.settings._approve_release(
                    start=state["charge_action"] == "start",
                    notify=False,
                    now=self.wall_time,
                    current=current,
                )
            except ValueError, TypeError, OSError:
                if not self.settings.get("charge_release"):
                    self.release_changed()
                elif self.phase()[0] == "expired":
                    await self.stop_release()
                else:
                    self.schedule()
                    if retry_deadline is not None and self.eligible():
                        self.retry(max(0, retry_deadline - self.wall_time()))
                raise
            if state["charge_action"] == "start":
                self.edited("charge_release")
            else:
                self.retry_deadline = retry_deadline
                self.committed(equivalent, timing_only=timing_only)
        else:
            # PV Start grants release; the next regular tick decides its point.
            async with self.settings.lock:
                validate()
                epoch = self.epoch
                profile = self.settings.get("charging_profile")
                await self.settings._save(
                    {**self.settings.values, "charge_release": True}
                )
                if (
                    self.closed
                    or epoch != self.epoch
                    or profile != self.settings.get("charging_profile")
                ):
                    self.settings.values["charge_release"] = False
                    await self.settings._save(
                        {**self.settings.values, "charge_release": False}
                    )
                    self.release_changed()
                    raise ValueError("Superseded charge action")
                self.settings.notify("charge_release")
        if self.task and self.eligible():
            await asyncio.shield(self.task)
        if self.status in ("rejected", "communication_failure", "unsupported"):
            raise ValueError(self.reason or self.status)

    async def stop_release(self):
        if self.settings.get("charge_release") is False and not self.settings.get(
            "grid_request"
        ):
            return
        self.settings.values["charge_release"] = False
        self.settings.values["grid_request"] = None
        self.settings.values["grid_timing_edit"] = None
        self.release_changed()
        await self.settings.save_runtime(
            {
                "charge_release": False,
                "grid_request": None,
                "grid_timing_edit": None,
                "grid_timing_revision": self.settings.values["grid_timing_revision"],
            }
        )
        if self.pv:
            await self.pv.operator_stop()
        self.publish()

    def equivalent(self, values):
        """Compare approved desired points, never the temporary physical offer."""
        desired = self.requested
        if (
            self.closed
            or not desired
            or not self.current_point()
            or self.point_uncertain
            or (self.task and not self.task.done())
        ):
            return False
        try:
            ranges = envelope(self.state, self.settings)
            ongoing = desired.phases if desired.amps > 0 else None
            direction = self.settings.get("power_approximation")
            approved = ranges.resolve(
                number(rounded_kw(self.settings.approved("soll_power"))) * 1000,
                direction,
                ongoing,
                selected=self.settings.approved("power_phases"),
            )
            draft = ranges.resolve(
                number(rounded_kw(values["power_kw"])) * 1000,
                direction,
                ongoing,
                selected=values.get("power_phases"),
            )
            return (
                (approved.amps, approved.phases)
                == (desired.amps, desired.phases)
                == (draft.amps, draft.phases)
            )
        except ValueError, TypeError, OverflowError:
            return False

    def committed(self, equivalent, *, timing_only=False):
        if timing_only:
            previous = (self._published or {}).get("grid_schedule") or {}
            current = self.settings.values.get("grid_request") or {}
            if previous.get("start_at") != current.get("start_at") or (
                self._published or {}
            ).get("charge_release") != self.settings.get("charge_release"):
                self.edited("grid_timing")
                return
        if (
            not equivalent
            or (self.task and not self.task.done())
            or (not timing_only and not self.equivalent(self.settings.values))
        ):
            self.edited("grid_approved")
            return
        deadline = self.retry_deadline
        self.invalidate()
        self.schedule()
        if deadline is not None and self.eligible():
            self.retry(max(0, deadline - self.wall_time()))
        self.publish()

    def retry(self, delay):
        self.retry_at = delay
        self.retry_deadline = self.wall_time() + delay
        self.timer = self.call_later(delay, self.intent_callback())

    def attributes(self):
        point = None if self.point_uncertain else self.current_point()
        physical = self.state.value(("station", self.station_scope, "physical_phases"))
        return {
            **self.settings.approval_attributes(),
            **self.action_state(),
            "execution_ready": self.eligible(),
            "execution_blocked_reason": self.reason,
            "profile_status": self.status,
            "control_status": self.status,
            "profile_control_ready": self.eligible(),
            "command_status": self.status,
            "command_reason": self.reason,
            "active_message": self.state.messages.latest,
            "requested_current_a": self.requested.amps if self.requested else None,
            "requested_phase_count": self.requested.phases if self.requested else None,
            "acknowledged_current_a": self.acknowledged.amps
            if self.acknowledged
            else None,
            "applied_current_a": point.amps if point else None,
            "applied_phase_count": point.phases if point else None,
            "applied_offered_power_w": float(point.watts) if point else None,
            "physical_phase_mode": physical,
            "actual_enabled": self.state.value(
                self.listener.control.key("ChargingEnabled")
            ),
            "control_authority": self.state.value(
                self.listener.control.key("ControlAuthority")
            ),
            "grid_request_armed": bool(self.settings.values.get("grid_request")),
            "grid_schedule": self.settings.values.get("grid_request"),
            "grid_timing_state": self.phase()[0],
            "retry_in_seconds": self.retry_at,
            **(self.pv.attributes() if self.pv else {}),
        }

    def phase_changed(self):
        self.publish()
        self.reevaluate_pending()
        if self.status == "accepted_unconfirmed" and self.eligible():
            self.launch(confirm_only=True)

    def publish(self):
        if self._publishing:
            return
        self._publishing = True
        try:
            self.sync_messages()
            attributes = self.attributes()
            if attributes == self._published:
                return
            self._published = attributes
            for role in (
                "soll_power",
                "charging_profile",
                "power_approximation",
                "charge_action",
                "grid_start_delay",
                "grid_duration",
            ):
                self.state.publish(("grid", self.state.binding, role))
        finally:
            self._publishing = False

    def sync_messages(self):
        messages = self.state.messages
        generation = self.state.generation
        known = self.listener._initialization in ("ready", "missing")
        problem = None
        if known and self.state.current(self.state.registered_generation):
            try:
                if envelope(self.state, self.settings).power_bounds() is None:
                    problem = "All supported phase modes are disabled"
            except (ValueError, TypeError, OverflowError) as exc:
                problem = str(exc)
        if problem:
            messages.set(
                "grid:capabilities",
                source="capabilities",
                severity="warning",
                code="capabilities",
                detail=problem,
                generation=generation,
            )
        else:
            messages.clear("grid:capabilities")
        current = self.outcome_epoch == self.epoch
        lockout = current and self.retry_deadline is not None
        failure = current and (
            self.status in ("rejected", "communication_failure", "unsupported")
            or self.reason in ("readback_failure", "interim_readback_failure")
        )
        if lockout or failure:
            code = (
                (
                    "reenable_lockout"
                    if self.reason == "ReenableLockout"
                    else "phase_lockout"
                )
                if lockout
                else "grid_failed"
            )
            messages.set(
                "grid:outcome",
                source="grid",
                severity="warning" if lockout else "error",
                code=code,
                detail=self.reason,
                generation=generation,
                token=self.epoch,
            )
        else:
            messages.clear("grid:outcome")
        if (
            current
            and self.status == "confirmed"
            and self.reason == "power_above_mode_maximum"
        ):
            messages.set(
                "grid:limitation",
                source="grid",
                severity="warning",
                code="power_limited",
                generation=generation,
                token=self.epoch,
            )
        else:
            messages.clear("grid:limitation")

    def transaction(self):
        s = self.state.session
        if (
            s
            and s.get("generation") == list(self.state.generation)
            and s["ended_at"] is None
            and tuple(s["scope"]) == self.state.binding
        ):
            return s["external_transaction_id"]
        return None

    def manager_ready(self):
        adapter = self.listener.adapter
        return bool(
            not self.closed
            and adapter
            and adapter.live(adapter.generation)
            and self.state.current(self.state.registered_generation)
            and self.state.value(self.listener.control.key("ControlAuthority"))
            == "remote"
        )

    def eligible(self):
        return self.manager_ready() and bool(self.transaction())

    def fingerprint(self):
        return (
            self.state.generation,
            self.state.connected,
            self.transaction(),
            self.state.value(self.listener.control.key("ControlAuthority")),
            self.state.value(self.listener.control.key("ChargingEnabled")),
            self.eligible(),
            self.settings.get("charging_profile"),
            self.settings.get("charge_release"),
            tuple(
                (k, str(v.value), v.generation)
                for k, v in self.state.observations.items()
                if k[0] == "capability"
            ),
        )

    def cancel_timers(self):
        for name in ("timer", "schedule_timer"):
            handle = getattr(self, name)
            if handle:
                handle.cancel()
                setattr(self, name, None)
        self.retry_at = self.retry_deadline = None

    def invalidate(self):
        self.epoch += 1
        self.outcome_epoch = None
        self.state.messages.clear("grid:outcome")
        self.state.messages.clear("grid:limitation")
        self.cancel_timers()

    async def persist_release_loss(self):
        async with self.settings.lock:
            if not self.settings.get("charge_release"):
                await self.settings._save(
                    {
                        **self.settings.values,
                        "charge_release": False,
                        "grid_request": None,
                        "grid_timing_edit": None,
                    }
                )

    def changed(self):
        signature = self.fingerprint()
        if signature == self.signature:
            return
        adapter = self.listener.adapter
        if not self.closed and adapter and adapter.live(adapter.generation):
            adapter.spawn(self.settings.initialize_power())
        if self.signature and self.signature[0] != self.state.generation:
            self.allow_issue = False
            self.known_transaction = None
        authority = self.state.value(self.listener.control.key("ControlAuthority"))
        authority_lost = self.last_authority == "remote" and authority == "local"
        if authority in ("remote", "local"):
            self.last_authority = authority
        if self.settings.get("charge_release") and authority_lost:
            self.settings.values["charge_release"] = False
            self.settings.values["grid_request"] = None
            self.settings.values["grid_timing_edit"] = None
            if not self.closed:
                self.settings.hass.async_create_task(self.persist_release_loss())
            signature = self.fingerprint()
        transaction = self.transaction()
        if transaction and transaction != self.known_transaction:
            self.allow_issue = bool(self.settings.get("charge_release"))
            self.known_transaction = transaction
        self.signature = signature
        self.invalidate()
        self.schedule()
        if not self.eligible():
            self.status, self.reason = "waiting", "ineligible"
            if (
                not self.state.connected
                or getattr(self, "point_generation", None) != self.state.generation
            ):
                self.confirmed = self.acknowledged = None
            self.publish()
            return
        self.launch()
        self.publish()

    def edited(self, role):
        if role.startswith("allowed_current_"):
            adapter = self.listener.adapter
            if adapter and adapter.live(adapter.generation):
                adapter.spawn(self.settings.initialize_power())
        self.allow_issue = True
        self.invalidate()
        self.requested = None
        self.schedule()
        if role == "charging_profile":
            self.signature = None
            self.changed()
        else:
            self.signature = self.fingerprint()
            if self.eligible():
                self.status, self.reason = "pending", None
                self.launch()
        self.publish()

    def intent_callback(self):
        epoch, adapter, transaction = (
            self.epoch,
            self.listener.adapter,
            self.transaction(),
        )

        def elapsed():
            if self.valid(epoch, adapter, transaction):
                self.launch()

        return elapsed

    def launch(self, *, confirm_only=False, pv_point=None):
        if (
            self.settings.get("charging_profile") != "NETZ"
            and self.settings.get("charge_release")
            and pv_point is None
            and not confirm_only
        ):
            return  # Observations never execute an additional PV regulation pass.
        if self.closed or not self.eligible():
            return
        if self.task and not self.task.done() and self.task_epoch == self.epoch:
            return
        if self.timer:
            self.timer.cancel()
            self.timer = None
        self.retry_at = self.retry_deadline = None
        adapter = self.listener.adapter
        epoch = self.task_epoch = self.epoch
        business_phase = self.phase()[0]
        self.task = adapter.spawn(
            self.reconcile(epoch, adapter, confirm_only=confirm_only, pv_point=pv_point)
        )

        def settled(task):
            if not task.cancelled() and task.exception() is None:
                # A planned start can fall while a read/zero write is in flight.
                # The deadline cannot coalesce into that older waiting decision.
                if (
                    self.task is task
                    and self.valid(epoch, adapter, self.transaction())
                    and self.phase()[0] != business_phase
                ):
                    self.launch()
                else:
                    self.reevaluate_pending()

        self.task.add_done_callback(settled)

    def reevaluate_pending(self):
        adapter = self.listener.adapter
        if not self.closed and adapter and adapter.live(adapter.generation):
            adapter.spawn(self.settings.approve_equivalent_grid())

    def current_point(self):
        """A failed read does not erase a held, correctly scoped observation."""
        point = self.confirmed
        if (
            point
            and getattr(self, "point_generation", None) == self.state.generation
            and self.confirmed_transaction == self.transaction()
            and self.state.current(self.state.registered_generation)
            and self.state.value(("station", self.station_scope, "physical_phases"))
            == point.phases
        ):
            return point
        return None

    def valid(self, epoch, adapter, transaction):
        return (
            epoch == self.epoch
            and self.eligible()
            and self.listener.adapter is adapter
            and transaction == self.transaction()
        )

    def phase(self):
        request = self.settings.values.get("grid_request")
        if not request:
            return "active", None
        now = self.wall_time()
        if request.get("end_at") is not None and now >= request["end_at"]:
            return "expired", None
        if now < request["start_at"]:
            return "waiting", request["start_at"]
        return "active", request.get("end_at")

    def schedule(self):
        """Business deadlines also expire without a connected vehicle/transaction."""
        if self.schedule_timer:
            self.schedule_timer.cancel()
            self.schedule_timer = None
        request = self.settings.values.get("grid_request")
        if (
            not request
            or not self.settings.get("charge_release")
            or self.closed
            or self.settings.get("charging_profile") != "NETZ"
        ):
            return
        phase, business_deadline = self.phase()
        if phase == "expired":
            business_deadline = self.wall_time()
        edit = self.settings.values.get("grid_timing_edit")
        edit_deadline = (
            self.settings.timing_edit_deadline()
            if edit
            and edit["schedule_revision"] == request["revision"]
            and edit["revision"] != self.attempted_edit_revision
            else None
        )
        deadlines = [d for d in (business_deadline, edit_deadline) if d is not None]
        if not deadlines:
            return
        deadline = min(deadlines)

        async def elapsed():
            if self.closed or self.schedule_timer is not handle:
                return
            self.schedule_timer = None
            if self.wall_time() < deadline:
                self.schedule()
                return
            if self.phase()[0] == "expired":
                await self.stop_release()
            else:
                if edit_deadline is not None and edit_deadline <= self.wall_time():
                    # Keep the business end armed while rollback awaits storage.
                    # This revision is attempted once, without an I/O retry loop.
                    self.attempted_edit_revision = edit["revision"]
                    self.schedule()
                    try:
                        await self.settings.expire_timing_edit(
                            edit["revision"], edit["schedule_revision"]
                        )
                    except OSError as exc:
                        self.settings.approval_error = str(exc)
                        self.settings.notify_grid()
                if (
                    business_deadline is not None
                    and business_deadline <= self.wall_time()
                ):
                    self.launch()
                self.schedule()
            self.publish()

        def fire():
            task = self.settings.hass.async_create_task(
                elapsed(), "NETZ business deadline"
            )
            self.deadline_tasks.add(task)
            task.add_done_callback(self.deadline_tasks.discard)

        handle = self.schedule_timer = self.call_later(
            max(0, deadline - self.wall_time()), fire
        )

    async def read_point(self, adapter, epoch, transaction):
        response = await adapter.request(
            call.GetCompositeSchedule(
                evse_id=int(self.state.binding[1]), duration=60, charging_rate_unit="A"
            )
        )
        if not self.valid(epoch, adapter, transaction) or response.status != "Accepted":
            return None
        schedule = getattr(response, "schedule", None)
        if not isinstance(schedule, dict):
            return None
        periods = schedule.get("charging_schedule_period")
        if (
            schedule.get("evse_id") != int(self.state.binding[1])
            or schedule.get("charging_rate_unit") != "A"
            or not isinstance(periods, list)
            or len(periods) != 1
        ):
            return None
        period = periods[0]
        amps, phases = period.get("limit"), period.get("number_phases")
        physical = self.state.value(("station", self.station_scope, "physical_phases"))
        if (
            type(amps) not in (int, float)
            or int(amps) != amps
            or amps < 0
            or type(phases) is not int
            or physical != phases
            or phases not in (1, 2, 3)
            or period.get("start_period") != 0
        ):
            return None
        # Readback is an actual offer, including a mode disabled by the user.
        voltage = phases * NOMINAL_VOLTAGE
        point = Point(int(amps), phases, amps * voltage)
        self.confirmed, self.confirmed_transaction = point, transaction
        self.point_generation = self.state.generation
        self.point_uncertain = False
        self.publish()
        return point

    async def dispatch_point(self, point, epoch, adapter, transaction):
        """Send a bounded TxProfile with the same wire-generation guard."""
        period = {"start_period": 0, "limit": point.amps}
        if point.phases is not None:
            period["number_phases"] = point.phases
        profile = {
            "id": 1,
            "stack_level": 0,
            "charging_profile_kind": "Absolute",
            "charging_profile_purpose": "TxProfile",
            "transaction_id": transaction,
            "charging_schedule": [
                {
                    "id": 1,
                    "charging_rate_unit": "A",
                    "charging_schedule_period": [period],
                }
            ],
        }
        payload = call.SetChargingProfile(
            evse_id=int(self.state.binding[1]), charging_profile=profile
        )
        # Compare the complete request at the actual wire boundary.
        expected = snake_to_camel_case(payload.__dict__)
        expected = {k: v for k, v in expected.items() if v is not None}

        uncertainty = {"previous": None, "rejected": False}

        def guard(action, wire):
            if (
                action != "SetChargingProfile"
                or wire != expected
                or not self.valid(epoch, adapter, transaction)
                or (
                    point.amps > 0
                    and (
                        not self.settings.get("charge_release")
                        or self.phase()[0] != "active"
                        or self.state.value(
                            self.listener.control.key("ChargingEnabled")
                        )
                        is not True
                    )
                )
            ):
                raise Refused("Superseded NETZ intent")
            # Preserve the held planning point, but do not present it as actual
            # while this proposal may supersede it, including after intent changes.
            uncertainty["previous"] = self.point_uncertain
            self.point_uncertain = uncertainty
            self.publish()

        token = DISPATCH.set(guard)
        try:
            response = await adapter.request(payload)
        finally:
            DISPATCH.reset(token)
        if response.status == "Accepted":
            uncertainty["previous"] = False
        elif response.status == "Rejected":
            # Atomic rejection did not apply. Unwind only rejected proposals;
            # retain prior accepted uncertainty and newer dispatch/readback state.
            uncertainty["rejected"] = True
            while self.point_uncertain and self.point_uncertain["rejected"]:
                self.point_uncertain = self.point_uncertain["previous"]
            self.publish()
        return response

    async def reconcile(self, epoch, adapter, *, confirm_only=False, pv_point=None):
        transaction = self.transaction()
        try:
            if not self.valid(epoch, adapter, transaction):
                return
            phase, _ = self.phase()
            if phase == "expired" and self.settings.get("charge_release"):
                await self.stop_release()
                return
            watts = (
                number(rounded_kw(self.settings.approved("soll_power"))) * 1000
                if phase == "active"
                and self.settings.get("charge_release")
                and self.settings.get("charging_profile") == "NETZ"
                and self.state.value(self.listener.control.key("ChargingEnabled"))
                is True
                else 0
            )
            try:
                ranges = envelope(self.state, self.settings)
            except ValueError, TypeError, OverflowError:
                if watts > 0:
                    raise
                # An authority/transaction-scoped zero pause does not need
                # positive-current capability diagnostics or a phase selection.
                ranges = None
            # Initial/reconnect read reconciles Stationary's independent recovery.
            observed = await self.read_point(adapter, epoch, transaction)
            if not self.valid(epoch, adapter, transaction):
                return
            held = observed or self.current_point()
            # Release OFF still reconciles a confirmed retained positive offer
            # to zero. allow_issue never authorizes replay of a positive target.
            # Missing supported readback on reconnect cannot authorize blind replay.
            if held is None and not self.allow_issue:
                self.status, self.reason = (
                    "accepted_unconfirmed"
                    if self.settings.get("charging_profile") != "NETZ"
                    and self.acknowledged
                    and self.point_uncertain
                    else "unconfirmed",
                    "readback_unknown",
                )
                self.publish()
                return
            ongoing = held.phases if held and held.amps > 0 else None
            desired = (
                ranges.resolve(
                    watts,
                    self.settings.get("power_approximation"),
                    ongoing,
                    selected=self.settings.approved("power_phases"),
                )
                if ranges
                else Point(0, None, 0)
            )
            if pv_point is not None:
                desired = pv_point
                watts = desired.watts
            elif self.settings.get("charging_profile") != "NETZ" and self.settings.get(
                "charge_release"
            ):
                # Asynchronous confirmation can update an existing request, but
                # never computes a fresh positive PV target.
                if not confirm_only or self.requested is None:
                    return
                desired = self.requested
                watts = desired.watts
            self.requested = desired
            limitation = (
                "power_above_mode_maximum"
                if desired.phases is not None
                and watts > ranges.ranges[desired.phases][2]
                else None
            )

            def matches(p):
                return (
                    p
                    and p.amps == desired.amps
                    and (desired.phases is None or p.phases == desired.phases)
                )

            if matches(observed) or (matches(held) and not self.point_uncertain):
                if self.timer:
                    self.timer.cancel()
                    self.timer = None
                self.retry_at = self.retry_deadline = None
                self.allow_issue = False
                self.status, self.reason = "confirmed", limitation
            elif confirm_only:
                return
            else:
                self.status, self.reason = "pending", None
                self.publish()
                response = await self.dispatch_point(
                    desired, epoch, adapter, transaction
                )
                if not self.valid(epoch, adapter, transaction):
                    return
                self.allow_issue = False
                if response.status != "Accepted":
                    info = getattr(response, "status_info", None)
                    info = camel_to_snake_case(info) if isinstance(info, dict) else info
                    delay = retry_delay(info)
                    self.status, self.reason = (
                        "rejected",
                        (info or {}).get("reason_code", response.status),
                    )
                    if delay is not None:
                        if self.reason == "PhaseSwitchLockout" and held is not None:
                            temporary = (
                                ranges.interim(
                                    watts,
                                    held.phases,
                                    self.settings.get("power_approximation"),
                                )
                                if ranges and held.phases in ranges.ranges
                                else Point(0, None, 0)
                            )
                            if pv_point is not None and desired.amps > 0:
                                # PV continuation during a real phase lockout:
                                # retain the executable positive current-series
                                # minimum, rather than importing NETZ DOWN=zero.
                                temporary = (
                                    ranges.points(held.phases)[0]
                                    if ranges and held.phases in ranges.ranges
                                    else Point(0, None, 0)
                                )
                            if temporary.amps != held.amps or (
                                temporary.phases is not None
                                and temporary.phases != held.phases
                            ):
                                fallback = await self.dispatch_point(
                                    temporary, epoch, adapter, transaction
                                )
                                if not self.valid(epoch, adapter, transaction):
                                    return
                                if fallback.status == "Accepted":
                                    self.acknowledged = temporary
                                    if pv_point is not None and self.pv:
                                        self.pv.accepted(temporary, held)
                                    # The original recognized lockout still owns
                                    # its retry even if interim confirmation fails.
                                    try:
                                        await self.read_point(
                                            adapter, epoch, transaction
                                        )
                                    except (
                                        TimeoutError,
                                        OSError,
                                        OCPPError,
                                        ConnectionClosed,
                                    ):
                                        self.reason = "interim_readback_failure"
                                else:
                                    fallback_info = getattr(
                                        fallback, "status_info", None
                                    )
                                    fallback_info = (
                                        camel_to_snake_case(fallback_info)
                                        if isinstance(fallback_info, dict)
                                        else {}
                                    )
                                    self.reason = fallback_info.get(
                                        "reason_code", fallback.status
                                    )
                                    delay = retry_delay(fallback_info)
                        if not self.valid(epoch, adapter, transaction):
                            return
                        if delay is not None:
                            self.status = (
                                "phase_lockout_pending"
                                if (info or {}).get("reason_code")
                                == "PhaseSwitchLockout"
                                else "rejected"
                            )
                            self.allow_issue = True
                            self.retry(delay)
                    self.outcome_epoch = epoch
                    self.publish()
                    return
                self.acknowledged = desired
                if pv_point is not None and self.pv:
                    self.pv.accepted(desired, held)
                self.status = "accepted_unconfirmed"
                await adapter.read(
                    adapter.generation,
                    {
                        "component": {
                            "name": "Connector",
                            "evse": {
                                "id": int(self.state.binding[1]),
                                "connector_id": int(self.state.binding[2]),
                            },
                        },
                        "variable": {"name": "PhaseRotation"},
                    },
                    current=lambda: self.valid(epoch, adapter, transaction),
                )
                applied = await self.read_point(adapter, epoch, transaction)
                if not self.valid(epoch, adapter, transaction):
                    return
                if matches(applied):
                    self.status, self.reason = "confirmed", limitation
                else:
                    self.reason = "readback_unknown_or_mismatch"
            self.outcome_epoch = epoch
            self.publish()
        except (ValueError, TypeError, OverflowError) as exc:
            if not self.valid(epoch, adapter, transaction):
                return
            self.status, self.reason = "unsupported", str(exc)
            self.outcome_epoch = epoch
            self.publish()
        except TimeoutError, OSError, OCPPError, ConnectionClosed:
            if not self.valid(epoch, adapter, transaction):
                return
            self.status, self.reason = (
                ("accepted_unconfirmed", "readback_failure")
                if self.status == "accepted_unconfirmed"
                else ("communication_failure", "transport_failure")
            )
            self.outcome_epoch = epoch
            self.publish()
        except Refused:
            pass

    async def close(self):
        self.closed = True
        self.invalidate()
        if self.action_task and not self.action_task.done():
            self.action_task.cancel()
            await asyncio.gather(self.action_task, return_exceptions=True)
        for task in self.deadline_tasks:
            task.cancel()
        if self.deadline_tasks:
            await asyncio.gather(*self.deadline_tasks, return_exceptions=True)
        for remove in self.removers:
            remove()
