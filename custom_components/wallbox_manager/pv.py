"""One event-fed PV snapshot and one clock; existing Grid owns guarded OCPP I/O."""

import asyncio
import json
import logging
import math
import time
from datetime import UTC, datetime

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .config_flow import SOURCES, diagnostic_level
from .grid_point import envelope
from .pv_core import ZERO, Controller, Inputs, signed_storage
from .pv_economics import Budget
from .pv_plan import Planner
from .pv_soc import PV_PROFILES, soc_policy

_LOGGER = logging.getLogger(__name__)


def reading(state, role):
    """Canonical units, finite values and explicit invalidity, never a zero default."""
    if (
        state is None
        or state.attributes.get("restored")
        or not str(state.state).strip()
    ):
        raise ValueError(role)
    try:
        value = float(state.state)
    except (TypeError, ValueError) as exc:
        raise ValueError(role) from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(role)
    if role in ("min_soc_speicher", "soc_speicher_aktuell"):
        if (
            state.attributes.get("unit_of_measurement") not in (None, "%")
            or value > 100
        ):
            raise ValueError(role)
        return value
    units = (
        {"Wh": 1, "kWh": 1000, "MWh": 1000000}
        if role in ("storage_capacity", "remaining_pv_energy")
        else {"W": 1, "kW": 1000, "MW": 1000000}
    )
    factor = units.get(state.attributes.get("unit_of_measurement"))
    if factor is None:
        raise ValueError(role)
    return value * factor


class PV:
    def __init__(self, hass, entry, grid):
        self.hass, self.entry, self.grid = hass, entry, grid
        self.settings, self.state = grid.settings, grid.state
        self.store = Store(
            hass, 1, f"wallbox_manager.{entry.entry_id}.pv", atomic_writes=True
        )
        self.core, self.budget, self.planner = Controller(), Budget(), Planner()
        self.values = {}
        self.timer = self.task = None
        self.closed = False
        self.removers = []
        self.input_remove = lambda: None
        self.context = None
        self.monotonic = time.monotonic
        self.now = lambda: dt_util.as_local(datetime.now(UTC))
        self.target = None
        self.input_problem = None
        self.last_saved = None
        self.last_event = None
        self.last_target_event = None
        self.last_mode = "STOP"
        self.proposed = None
        self.next_due = None
        self.ended_transaction = None
        self.journal_lock = asyncio.Lock()
        self.checkpoints = set()
        self.pending_restart = None
        self.station_identity = None

    async def start(self):
        record = await self.store.async_load()
        if record is None:
            from .pv_migration import legacy_record

            economy = await Store(
                self.hass, 1, f"wallbox_manager.{self.entry.entry_id}.pv_economy"
            ).async_load()
            days = await Store(
                self.hass, 1, f"wallbox_manager.{self.entry.entry_id}.pv_day"
            ).async_load()
            record = legacy_record(
                economy, days, self.entry.entry_id, self.state.binding
            )
        if not isinstance(record, dict):
            record = {"economics": {"invalid": True}}
        self.station_identity = record.get("station_identity")
        self.budget = Budget.restore(record.get("economics"))
        self.planner = Planner.restore(record.get("day"))
        self.ended_transaction = record.get("ended_transaction")
        self.last_saved = record
        self.observe_inputs()
        self.removers.append(self.settings.watch("charge_release", self.lifecycle))
        self.removers.append(self.settings.watch("charging_profile", self.lifecycle))
        station, _, _ = self.state.binding
        for key in (
            ("station", (station, None, None), "control_authority"),
            ("control", self.state.binding, "charging_enabled"),
            ("session", self.state.binding, "active"),
            ("session", self.state.binding, "id"),
            ("grid", self.state.binding, "charge_action"),
        ):
            self.removers.append(self.state.watch(key, self.lifecycle))
        station, evse, _ = self.state.binding
        for key in (
            ("session", self.state.binding, "power"),
            ("observation", (station, evse, None), "power"),
        ):
            self.removers.append(self.state.watch(key, self.ev_observed))
        self.lifecycle()

    def ev_observed(self):
        if self.ev_power() is None:
            self.budget.interrupt()

    def observe_inputs(self):
        self.input_remove()
        self.mapping = {role: self.entry.options.get(role) for role in SOURCES}
        self.values = {}
        for role, entity in self.mapping.items():
            try:
                self.values[role] = reading(self.hass.states.get(entity or ""), role)
            except ValueError:
                self.values[role] = None
        entities = list({entity for entity in self.mapping.values() if entity})
        self.input_remove = async_track_state_change_event(
            self.hass, entities, self.observed
        )
        self.budget.interrupt()

    @callback
    def observed(self, event):
        for role, entity in self.mapping.items():
            if entity == event.data["entity_id"]:
                try:
                    self.values[role] = reading(event.data.get("new_state"), role)
                except ValueError:
                    self.values[role] = None
                    self.budget.interrupt()
        # No target calculation, OCPP command or new timer here.

    def options_updated(self):
        mapping = {role: self.entry.options.get(role) for role in SOURCES}
        if mapping != self.mapping:
            self.observe_inputs()

    def active(self):
        return (
            not self.closed
            and self.settings.get("charging_profile") in PV_PROFILES
            and self.settings.get("charge_release") is True
        )

    def lifecycle(self):
        context = (
            self.settings.get("charging_profile"),
            self.settings.get("charge_release"),
            self.state.generation,
            self.grid.transaction(),
        )
        if context != self.context:
            if self.context and self.context[0] in PV_PROFILES and context[0] == "NETZ":
                self.budget = Budget(cause="netz_scope_exit")
                self.station_identity = None
                self.checkpoint()
            self.context = context
            self.core = Controller()
            self.proposed = self.pending_restart = None
            self.budget.interrupt()
        session = self.state.session
        if (
            session
            and session.get("ended_at") is not None
            and self.state.current(tuple(session.get("generation") or ()))
        ):
            if self.ended_transaction != session["external_transaction_id"]:
                self.ended_transaction = session["external_transaction_id"]
                self.checkpoint()
        if not self.grid.eligible():
            self.budget.interrupt()
        if not self.active():
            if self.timer:
                self.timer.cancel()
                self.timer = None
            self.next_due = None
            return
        # A temporary outage retains the cadence but earns no eligible cycles.
        if self.timer is None:
            self.arm()

    def arm(self):
        interval = self.settings.get("regulation_interval")
        now = self.monotonic()
        self.next_due = (
            now + interval if self.next_due is None else self.next_due + interval
        )
        if self.next_due <= now:
            self.next_due = now + interval
        self.timer = self.grid.call_later(max(0, self.next_due - now), self.fire)

    def fire(self):
        self.timer = None
        if self.active():
            # Arm before an eager HA task can publish or recover readiness.
            self.arm()
            if self.task is None or self.task.done():
                self.task = self.hass.async_create_task(
                    self.tick(), "PV regulation tick"
                )

    def ev_power(self):
        from .session import view

        return view(self.state, datetime.now(UTC))["power"]

    def attributes(self):
        return dict(
            pv_mode=self.core.mode,
            effective_target_soc=self.target,
            pv_input_problem=self.input_problem,
            pv_import_energy_wh=round(self.budget.wh, 3),
            pv_economic_stopped=self.budget.stopped,
            pv_planning_phase=self.planner.phase,
            pv_proposed_current_a=self.proposed.amps if self.proposed else None,
            pv_proposed_phase_count=self.proposed.phases if self.proposed else None,
        )

    def diagnostic(self, event, reason, **fields):
        try:
            if diagnostic_level(self.entry.options) >= 2:
                _LOGGER.info(
                    "WBMGR subsystem=pv %s",
                    json.dumps(
                        dict(
                            station=self.state.binding[0],
                            evse=self.state.binding[1],
                            connector=self.state.binding[2],
                            profile=self.settings.get("charging_profile"),
                            mode=self.core.mode,
                            event=event,
                            reason=reason,
                            **fields,
                        ),
                        sort_keys=True,
                    ),
                )
        except Exception:
            pass

    def problem(self, reason):
        if reason != self.input_problem:
            self.input_problem = reason
            self.diagnostic("pv_inputs", reason or "inputs_recovered")
        if reason:
            self.budget.interrupt()

    def decision_event(self, decision):
        point = decision.point
        point_key = (point.phases, point.amps)
        # Delays and unchanged HOLD decisions produce at most one transition,
        # never measurement-digit signatures or per-tick logs.
        signature = (decision.mode, decision.reason, point_key, self.budget.stopped)
        if signature != self.last_event:
            old = self.last_event
            self.last_event = signature
            fields = dict(decision.fields)
            if old is None or old[2] != point_key:
                fields.update(
                    old_point=list(old[2]) if old else None,
                    requested_point=list(point_key),
                )
            if self.last_mode != decision.mode:
                fields.update(previous_mode=self.last_mode)
                self.last_mode = decision.mode
            self.diagnostic("pv_decision", decision.reason, **fields)

    def checkpoint(self):
        if self.closed:
            return
        task = self.hass.async_create_task(self.persist(), "PV journal checkpoint")
        self.checkpoints.add(task)
        task.add_done_callback(self.checkpoints.discard)

    async def operator_stop(self):
        self.budget = Budget(cause="operator_stop")
        self.station_identity = None
        await self.persist()

    async def persist(self):
        async with self.journal_lock:
            await self._persist()

    async def _persist(self):
        record = dict(
            version=1,
            economics=self.budget.saved(),
            day=self.planner.saved(),
            ended_transaction=self.ended_transaction,
            station_identity=self.station_identity,
        )
        if record == self.last_saved:
            return
        await self.store.async_save(record)

        def verify():
            with open(self.store.path, encoding="utf-8") as handle:
                if json.load(handle).get("data") != record:
                    raise OSError("PV journal was not durably stored")

        await self.hass.async_add_executor_job(verify)
        self.last_saved = record

    def accepted(self, point, previous):
        self.core.sent(point, previous)
        if point.amps > 0 and self.pending_restart:
            # The journal is deliberately cleared only by an accepted authorized
            # restart, never by mere observed activity or budget reset.
            self.budget.restarted()
            self.pending_restart = None

    def provisional_target(self):
        """HA-only outage planning, never a deferred operating-point request."""
        values = dict(self.values)
        profile = self.settings.get("charging_profile")
        if profile == "PV_SURPLUS" and not self.mapping.get("soc_speicher_aktuell"):
            return
        reserve = values.get("min_soc_speicher")
        if reserve is None:
            return
        try:
            self.target = float(
                self.planner.calculate(
                    profile,
                    self.settings.values,
                    reserve,
                    self.now(),
                    self.monotonic(),
                    forecast=values.get("remaining_pv_energy"),
                    capacity=values.get("storage_capacity"),
                )
            )
        except ValueError, TypeError, OverflowError:
            self.target = None
        event = (self.target, self.planner.phase)
        if self.target is not None and event != self.last_target_event:
            self.last_target_event = event
            self.diagnostic(
                "pv_target",
                "provisional_target",
                target_soc_pct=self.target,
                phase=self.planner.phase,
            )
        self.grid.publish()

    async def tick(self):
        if not self.active():
            return
        g, s = self.grid, self.settings
        if not g.eligible():
            self.problem("control_unavailable")
            if not self.state.connected and g.last_authority == "remote":
                self.provisional_target()
                try:
                    await self.persist()
                except OSError:
                    self.problem("journal_write_failed")
            return
        if self.state.value(g.listener.control.key("ChargingEnabled")) is not True:
            self.problem("hardware_disabled")
            return
        epoch, adapter, tx = g.epoch, g.listener.adapter, g.transaction()
        values = dict(self.values)  # one canonical held-value snapshot per tick
        config = {key: s.get(key) for key in s.values}
        ev = self.ev_power()
        current = None if g.point_uncertain else g.current_point()
        busy = bool(g.task and not g.task.done())
        pending = busy or bool(g.point_uncertain)
        battery = (
            bool(self.mapping.get("soc_speicher_aktuell"))
            or s.get("charging_profile") != "PV_SURPLUS"
        )
        try:
            target = soc = storage = None
            pv, load = values.get("leistung_pv"), values.get("leistung_verbraucher")
            # Fronius total-consumer power includes the selected EV, as in beta.18.
            household = (
                max(0, load - ev) if load is not None and ev is not None else None
            )
            surplus = pv is not None and household is not None and pv > household
            if battery:
                reserve, soc = (
                    values.get("min_soc_speicher"),
                    values.get("soc_speicher_aktuell"),
                )
                if reserve is None or soc is None:
                    raise ValueError("battery_soc_reserve")
                target = float(
                    self.planner.calculate(
                        s.get("charging_profile"),
                        config,
                        reserve,
                        self.now(),
                        self.monotonic(),
                        surplus=surplus,
                        forecast=values.get("remaining_pv_energy"),
                        capacity=values.get("storage_capacity"),
                    )
                )
                self.target = target
                target_event = (target, self.planner.phase)
                if target_event != self.last_target_event:
                    self.last_target_event = target_event
                    plan_fields = dict(target_soc_pct=target, phase=self.planner.phase)
                    if s.get("charging_profile") == "PV_OPTIMUM":
                        from .pv_plan import (
                            remaining_day_seconds,
                            remaining_house_energy,
                        )

                        plan_fields.update(
                            remaining_pv_wh=values.get("remaining_pv_energy"),
                            capacity_wh=values.get("storage_capacity"),
                            remaining_house_wh=float(
                                remaining_house_energy(
                                    config["estimated_daily_house_consumption_kwh"],
                                    remaining_day_seconds(self.now()),
                                )
                            ),
                        )
                    self.diagnostic("pv_target", "target_changed", **plan_fields)
                # Mandatory SoC STOP remains possible despite an unrelated invalid
                # power source; it never waits for ordinary settling or readback.
                if soc < target - config["soc_hysterese"]:
                    self.core.mode = "STOP"
                    self.proposed = ZERO
                    self.budget.interrupt()
                    try:
                        await self.persist()
                    except OSError:
                        self.problem("journal_write_failed")
                    if g.valid(epoch, adapter, tx):
                        from .pv_core import Decision

                        d = Decision(
                            ZERO,
                            "STOP",
                            "soc_stop",
                            dict(
                                soc_pct=soc,
                                target_soc_pct=target,
                                stop_pct=target - config["soc_hysterese"],
                            ),
                        )
                        self.decision_event(d)
                        self.submit(d.point, current, urgent=True)
                    return
            scope = [*self.state.binding, "remote", tx]
            ended = bool(
                self.budget.scope and self.budget.scope[-1] == self.ended_transaction
            )
            identity = self.state.identity
            if (
                self.station_identity
                and not ended
                and any(identity.get(k) != v for k, v in self.station_identity.items())
            ):
                self.problem("economic_station_identity_mismatch")
                return
            if not self.budget.bind(scope, ended=ended):
                self.problem("economic_scope_unreconciled")
                return
            self.station_identity = dict(identity)
            imported, exported = (
                values.get("grid_import_power"),
                values.get("grid_export_power"),
            )
            if imported is None or exported is None or ev is None:
                raise ValueError("grid_ev_power")
            if battery:
                try:
                    storage = signed_storage(
                        values.get("storage_charge_power"),
                        values.get("storage_discharge_power"),
                    )
                except ValueError:
                    if (
                        soc_policy(
                            target,
                            config["soc_hysterese"],
                            soc,
                            self.core.mode,
                            surplus=surplus,
                        ).mode
                        == "BALANCE"
                    ):
                        raise
            ranges = envelope(self.state, s)
            points = [p for n in ranges.ranges for p in ranges.points(n)]
            if not points:
                raise ValueError("no_positive_modes")
            minimum = min(float(p.watts) for p in points)
            if (
                g.retry_deadline is not None
                and current
                and current.phases in ranges.ranges
            ):
                minimum = float(ranges.points(current.phases)[0].watts)
            # Price M from held actual voltages where available; nominal power
            # remains the separate setpoint identity used for navigation.
            floor = min(points, key=lambda p: (p.watts, p.phases))
            phase_mode = (
                current.phases
                if g.retry_deadline is not None and current
                else floor.phases
            )
            voltage = [
                self.state.value(
                    (
                        "observation",
                        (self.state.binding[0], self.state.binding[1], None),
                        f"voltage_l{n}",
                    )
                )
                for n in range(1, phase_mode + 1)
            ]
            if all(v is not None and v > 0 for v in voltage):
                minimum = float(ranges.minimum) * sum(voltage)
            hardware = (
                self.state.value(g.listener.control.key("ChargingEnabled")) is True
            )
            self.budget.sample(
                self.monotonic(),
                imported,
                minimum,
                active=hardware and ev > 0,
                valid=hardware,
            )
            stopped = self.budget.exhaust(config["pv_grid_import_budget_wh"])
            i = Inputs(ev, imported, exported, battery, soc, target, storage, surplus)
            # SoC transitions are independent from economic inhibition.
            mode = (
                soc_policy(
                    target,
                    config["soc_hysterese"],
                    soc,
                    self.core.mode,
                    surplus=surplus,
                ).mode
                if battery
                else "SURPLUS"
            )
            restart = self.budget.restart_reason(
                off=bool(current and current.amps == 0 and ev == 0),
                mode=mode,
                battery_w=storage,
                household=household,
                house_limit=config["pv_fast_restart_household_load_limit_w"],
                surplus=exported > imported,
                battery=battery,
            )
            d = self.core.decide(
                i,
                config,
                ranges,
                current,
                pending=pending,
                economic_stop=stopped and restart is None,
            )
            if stopped and restart:
                self.pending_restart = restart
                if d.point.amps > 0:
                    from .pv_core import Decision

                    fields = (
                        dict(battery_w=storage)
                        if restart == "battery_charging"
                        else dict(
                            household_w=household,
                            household_limit_w=config[
                                "pv_fast_restart_household_load_limit_w"
                            ],
                        )
                        if restart == "fast_low_household"
                        else dict(grid_w=imported - exported)
                    )
                    d = Decision(d.point, d.mode, "restart_" + restart, fields)
            elif stopped:
                from .pv_core import Decision

                d = Decision(
                    ZERO,
                    mode,
                    "import_budget",
                    dict(
                        grid_import_w=imported,
                        threshold_w=minimum / 2,
                        estimated_wh=self.budget.wh,
                        budget_wh=config["pv_grid_import_budget_wh"],
                    ),
                )
            if (
                d.mode == "BALANCE"
                and values.get("storage_charge_power") is not None
                and values.get("storage_discharge_power") is not None
                and values["storage_charge_power"] > 0
                and values["storage_discharge_power"] > 0
            ):
                d.fields["storage_pair_normalized"] = True
            self.proposed = d.point
            try:
                await (
                    self.persist()
                )  # Preserve provenance before the wire when possible.
            except OSError:
                self.problem("journal_write_failed")
                if d.point.amps > 0:
                    g.publish()
                    return  # Failed storage inhibits positives, never required OFF.
            else:
                self.problem(None)
            if not g.valid(epoch, adapter, tx) or not self.active():
                return
            self.decision_event(d)
            self.submit(
                d.point,
                current,
                urgent=d.reason
                in ("import_budget", "soc_stop", "balance_minimum_discharge"),
            )
            g.publish()
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            self.problem(str(exc))
            g.publish()

    def submit(self, point, current, *, urgent=False):
        g = self.grid
        if urgent:
            self.pending_restart = None
            if g.requested and g.requested.amps > 0 and g.task and not g.task.done():
                g.invalidate()  # supersede an in-flight positive proposal
        if g.task and not g.task.done() and g.task_epoch == g.epoch:
            return
        if (
            current
            and not g.point_uncertain
            and current.amps == point.amps
            and (point.phases is None or current.phases == point.phases)
        ):
            return
        if (
            g.retry_deadline is not None
            and g.retry_deadline > g.wall_time()
            and not urgent
        ):
            return
        if g.status == "rejected" and g.retry_deadline is None and g.requested == point:
            return  # generic rejection is not a timed retry authorization
        if g.point_uncertain and (not urgent or g.requested == point):
            g.launch(confirm_only=True)
            return
        g.allow_issue = True
        g.launch(pv_point=point)

    async def close(self):
        self.closed = True
        if self.timer:
            self.timer.cancel()
        self.input_remove()
        for remove in self.removers:
            remove()
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.checkpoints:
            await asyncio.gather(*self.checkpoints, return_exceptions=True)
        self.budget.interrupt()
        await self.persist()
