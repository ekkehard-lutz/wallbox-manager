"""Event-driven NETZ intent, serialized OCPP dispatch and one-shot deadlines."""

import asyncio
import re
import time

from ocpp.charge_point import camel_to_snake_case, snake_to_camel_case
from ocpp.exceptions import OCPPError
from ocpp.v21 import call
from websockets.exceptions import ConnectionClosed

from .control import DISPATCH, Refused
from .grid_point import Point, envelope


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
        self.epoch = 0
        self.closed = False
        self.task = self.timer = self.debounce = self.schedule_timer = None
        self.task_epoch = None
        self.operator_off = False
        self.allow_issue = False
        self.known_transaction = None
        self.signature = None
        self.status, self.reason = "waiting", None
        self.requested = self.confirmed = self.acknowledged = None
        self.confirmed_transaction = None
        self.point_uncertain = False
        self.retry_at = None
        self.wall_time = time.time
        self.call_later = lambda delay, callback: asyncio.get_running_loop().call_later(
            delay, callback
        )
        self._published = None
        self.removers = []
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
                        else self.changed,
                    )
                )
        for role in (
            "soll_power",
            "charging_profile",
            "power_approximation",
            "allowed_current_1p",
            "allowed_current_2p",
            "allowed_current_3p",
        ):
            self.removers.append(
                settings.watch(role, lambda role=role: self.edited(role))
            )

    def attributes(self):
        point = None if self.point_uncertain else self.current_point()
        physical = self.state.value(("station", self.station_scope, "physical_phases"))
        return {
            "execution_ready": self.eligible(),
            "execution_blocked_reason": self.reason,
            "profile_status": self.status,
            "control_status": self.status,
            "profile_control_ready": self.eligible(),
            "command_status": self.status,
            "command_reason": self.reason,
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
            "grid_timing_state": self.phase()[0],
            "retry_in_seconds": self.retry_at,
        }

    def phase_changed(self):
        self.publish()
        if self.status == "accepted_unconfirmed" and self.eligible():
            self.launch(confirm_only=True)

    def publish(self):
        attributes = self.attributes()
        if attributes == self._published:
            return
        self._published = attributes
        for role in ("soll_power", "charging_profile", "power_approximation"):
            self.state.publish(("grid", self.state.binding, role))

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

    def eligible(self):
        adapter = self.listener.adapter
        return bool(
            not self.closed
            and not self.operator_off
            and adapter
            and adapter.live(adapter.generation)
            and self.state.current(self.state.registered_generation)
            and self.settings.get("charging_profile") == "NETZ"
            and self.state.value(self.listener.control.key("ControlAuthority"))
            == "remote"
            and self.state.value(self.listener.control.key("ChargingEnabled")) is True
            and self.transaction()
        )

    def fingerprint(self):
        return (
            self.state.generation,
            self.state.connected,
            self.transaction(),
            self.state.value(self.listener.control.key("ControlAuthority")),
            self.state.value(self.listener.control.key("ChargingEnabled")),
            self.eligible(),
            self.settings.get("charging_profile"),
            tuple(
                (k, str(v.value), v.generation)
                for k, v in self.state.observations.items()
                if k[0] == "capability"
            ),
        )

    def cancel_timers(self):
        for name in ("timer", "debounce", "schedule_timer"):
            handle = getattr(self, name)
            if handle:
                handle.cancel()
                setattr(self, name, None)
        self.retry_at = None

    def invalidate(self):
        self.epoch += 1
        self.cancel_timers()

    def changed(self):
        signature = self.fingerprint()
        if signature == self.signature:
            return
        if self.signature and self.signature[0] != self.state.generation:
            self.allow_issue = False
        transaction = self.transaction()
        if transaction and transaction != self.known_transaction:
            self.allow_issue = True
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

    def edited(self, role):
        self.allow_issue = True
        self.invalidate()
        self.requested = None
        self.schedule()
        if role == "charging_profile":
            self.signature = None
            self.changed()
        elif self.eligible():
            if role == "soll_power":
                self.status = "pending"
                self.debounce = self.call_later(1, self.intent_callback())
            else:
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

    def launch(self, *, confirm_only=False):
        if self.closed or not self.eligible():
            return
        if self.task and not self.task.done() and self.task_epoch == self.epoch:
            return
        for name in ("debounce", "timer"):
            handle = getattr(self, name)
            if handle:
                handle.cancel()
                setattr(self, name, None)
        self.retry_at = None
        adapter = self.listener.adapter
        epoch = self.task_epoch = self.epoch
        self.task = adapter.spawn(
            self.reconcile(epoch, adapter, confirm_only=confirm_only)
        )

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
        request = self.settings.values.get("grid_request")
        if (
            not request
            or self.closed
            or not self.state.current(self.state.registered_generation)
            or self.settings.get("charging_profile") != "NETZ"
            or self.state.value(self.listener.control.key("ControlAuthority"))
            != "remote"
            or self.state.value(self.listener.control.key("ChargingEnabled"))
            is not True
        ):
            return
        phase, deadline = self.phase()
        if phase == "expired":
            deadline = self.wall_time()
        if deadline is None:
            return
        epoch = self.epoch
        adapter = self.listener.adapter

        async def elapsed():
            if (
                epoch != self.epoch
                or not adapter
                or not adapter.live(adapter.generation)
            ):
                return
            if self.phase()[0] == "expired":
                await self.listener.control.request("ChargingEnabled", "false")
            else:
                self.launch()
                self.schedule()
            self.publish()

        self.schedule_timer = self.call_later(
            max(0, deadline - self.wall_time()), lambda: adapter.spawn(elapsed())
        )

    async def permission_requested(self, enabled):
        self.operator_off = not enabled
        self.allow_issue = enabled
        self.invalidate()
        if not enabled:
            if self.settings.values.get("grid_request"):
                await self.settings.save_runtime({"grid_request": None})
            return
        if self.settings.get("charging_profile") != "NETZ":
            return
        delay, duration = (
            self.settings.get(k) for k in ("grid_start_delay", "grid_duration")
        )
        if delay is None and duration is None:
            return
        now = self.wall_time()
        start = now + (delay or 0)
        await self.settings.save_runtime(
            {
                "grid_start_delay": None,
                "grid_duration": None,
                "grid_request": {
                    "start_at": start,
                    "end_at": now
                    if duration == 0
                    else start + duration
                    if duration is not None
                    else None,
                },
            }
        )

    async def read_point(self, adapter, epoch, transaction, ranges):
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
        voltage = sum(ranges.voltages[:phases])
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

    async def reconcile(self, epoch, adapter, *, confirm_only=False):
        transaction = self.transaction()
        try:
            if not self.valid(epoch, adapter, transaction):
                return
            ranges = envelope(self.state, self.settings)
            phase, _ = self.phase()
            if phase == "expired":
                await self.listener.control.request("ChargingEnabled", "false")
                return
            # Initial/reconnect read reconciles Stationary's independent recovery.
            observed = await self.read_point(adapter, epoch, transaction, ranges)
            if not self.valid(epoch, adapter, transaction):
                return
            held = observed or self.current_point()
            # Missing supported readback on reconnect cannot authorize blind replay.
            if held is None and not self.allow_issue:
                self.status, self.reason = "unconfirmed", "readback_unknown"
                self.publish()
                return
            watts = self.settings.get("soll_power") * 1000 if phase == "active" else 0
            ongoing = held.phases if held and held.amps > 0 else None
            desired = ranges.resolve(
                watts, self.settings.get("power_approximation"), ongoing
            )
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
                self.retry_at = None
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
                                ranges.on_phase(
                                    watts,
                                    held.phases,
                                    self.settings.get("power_approximation"),
                                )
                                if held.phases in ranges.ranges
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
                                    # The original recognized lockout still owns
                                    # its retry even if interim confirmation fails.
                                    try:
                                        await self.read_point(
                                            adapter, epoch, transaction, ranges
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
                            self.retry_at = delay
                            self.timer = self.call_later(delay, self.intent_callback())
                    self.publish()
                    return
                self.acknowledged = desired
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
                applied = await self.read_point(adapter, epoch, transaction, ranges)
                if not self.valid(epoch, adapter, transaction):
                    return
                if matches(applied):
                    self.status, self.reason = "confirmed", limitation
                else:
                    self.reason = "readback_unknown_or_mismatch"
            self.publish()
        except (ValueError, TypeError, OverflowError) as exc:
            if not self.valid(epoch, adapter, transaction):
                return
            self.status, self.reason = "unsupported", str(exc)
            self.publish()
        except TimeoutError, OSError, OCPPError, ConnectionClosed:
            if not self.valid(epoch, adapter, transaction):
                return
            self.status, self.reason = (
                ("accepted_unconfirmed", "readback_failure")
                if self.status == "accepted_unconfirmed"
                else ("communication_failure", "transport_failure")
            )
            self.publish()
        except Refused:
            pass

    async def close(self):
        self.closed = True
        self.invalidate()
        for remove in self.removers:
            remove()
