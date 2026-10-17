"""Primitive charging profiles sharing persistence and the control boundary."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from fractions import Fraction

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .control.commands import CommandReason, CommandResult, CommandStatus, ControlArea
from .core.authority import ControlAuthority
from .core.events import control_view
from .core.telemetry import Channel, Quantity, State
from .core.values import scalar
from .diagnostics import (
    diagnostic_recovery,
    profile_event,
    recovery_record,
    recovery_snapshot,
    reset_diagnostics,
)
from .grid_timing import GridTiming, duration_seconds
from .power_history import PowerHistory
from .pv_diagnostics import diagnostic_permission
from .pv_economy_control import PVEconomicControl
from .pv_optimum import (
    OPTIMUM_DEFAULTS,
    OPTIMUM_REFERENCES,
    PVOptimum,
)
from .pv_soc import TARGET_FIELDS, clamp_settings, target_bounds
from .pv_surplus import PV_DEFAULTS, PVSurplus
from .regulation import DEFAULTS, migrate_regulation

_LOGGER = logging.getLogger(__name__)


def target_key(target):
    return json.dumps([target.station.value, target.evse.value, target.value])


class GridProfiles(GridTiming, PVSurplus, PVOptimum, PVEconomicControl):
    """Own profile settings and bounded detection tasks, never acquire authority."""

    def __init__(self, hass, entry, control, battery):
        self.hass, self.control, self.battery = hass, control, battery
        self.store = Store(hass, 1, f"wallbox_manager.{entry.entry_id}.profiles")
        self.entry = entry
        self.entry_id = entry.entry_id
        self.references = dict(entry.options)
        self.power_history = {
            key: PowerHistory(self.references.get("power_smoothing_window", 5))
            for key in ("leistung_pv", "leistung_verbraucher")
        }
        self.optimum_days = {}
        self.optimum_saved_days = {}
        self.optimum_forecasts = {}
        self.optimum_modes = {}
        self.optimum_initializations = set()
        self.optimum_connections = {}
        self.optimum_regulators = {}
        self.optimum_targets = {}
        self.optimum_plans = {}
        self.optimum_wakes = {}
        self.optimum_unsubscribe = None
        self.optimum_day_store = Store(
            hass, 1, f"wallbox_manager.{entry.entry_id}.pv_day"
        )
        self.pv_ongoing = {}
        self.pv_pause_reasons = {}
        self.pv_pause_transactions = {}
        self.pv_restart_reasons = {}
        self.pv_soc_protection = set()
        self.pv_measurement_status = {}
        self.pv_authority_contexts = {}
        self.pv_battery = {}
        self.pv_sessions = {}
        self.pv_start_since = {}
        self.pv_stop_since = {}
        self.pv_expiry = {}
        self.pv_retry_until = {}
        self.pv_retry_request = {}
        self.pv_phase_retry = {}
        self.pv_startups = {}
        self.enable_requests = {}
        self.enable_sessions = {}
        self.monotonic = time.monotonic
        self.wall_time = time.time
        self.timer_wait = asyncio.sleep
        self.grid_timers = {}
        self.settings = {}
        for snapshot in control.runtime.stations:
            for target in snapshot.connectors:
                self.initialize_settings(target)
        self.tasks = {}
        self.recoveries = {}
        self.recovery_ready = True
        self.debounce_tasks = {}
        self.debounce_wait = asyncio.sleep
        self.epochs = {}
        self.status = {}
        self.closed = False
        self.wait = asyncio.sleep
        self.battery_task = None
        self.battery_dirty = False
        self.suppressed = set()
        self.economic_initialize()
        self.unsubscribe = control.runtime.subscribe(self.changed, select=control_view)
        self.session_unsubscribe = control.runtime.sessions.subscribe(
            self.sessions_changed
        )
        self.unsubscribe_battery = lambda: None
        self.subscribe_inputs()

    def subscribe_inputs(self):
        """Replace the indexed subscription after configuration/history is loaded."""
        self.unsubscribe_battery()
        entities = {
            self.references.get(key)
            for key in (*OPTIMUM_REFERENCES, "min_soc_speicher")
        } | {self.battery.reserve, self.battery.soc}
        if self.battery.record:
            entities.add(self.battery.record["entity"])
        entities.discard(None)
        entities.discard("")
        self.economic_subscribe_inputs()
        self.unsubscribe_battery = async_track_state_change_event(
            self.hass, entities, self.battery_changed
        )

    async def load(self):
        await self.economic_load()
        self.optimum_plans.clear()
        self.optimum_modes.clear()
        stored = await self.store.async_load() or {}
        migrated = migrate_regulation(self.entry.options, stored)
        self.references.update(migrated)
        self.subscribe_inputs()
        for key, value in stored.items():
            try:
                if value.get("profile") not in (
                    "NETZ",
                    "PV_SURPLUS",
                    "PV_OPTIMUM",
                    "PV_MAXIMUM",
                ):
                    continue
                power, reserve = scalar(value["power_kw"]), scalar(value["min_soc"])
                if power <= 100 and reserve <= 100:
                    settings = {
                        **PV_DEFAULTS,
                        **OPTIMUM_DEFAULTS,
                        **value,
                        **{
                            k: self.entry.options[k]
                            for k in DEFAULTS
                            if k in self.entry.options
                        },
                        "soc_hysterese": migrated["soc_hysterese"],
                    }
                    # Preserve legacy values for audit; clamp deterministically even
                    # before reserve evidence arrives, then again against live R.
                    settings.setdefault(
                        "legacy_soc_targets", {k: settings[k] for k in TARGET_FIELDS}
                    )
                    try:
                        settings = clamp_settings(settings, 0)
                    except ValueError:
                        pass  # Impossible legacy H remains visible and blocks policy.
                    if scalar(settings["soc_hysterese"]) > 99:
                        raise ValueError("invalid legacy hysteresis")
                    validation = clamp_settings({**settings, "soc_hysterese": 0}, 0)
                    self.validate_pv(validation)
                    self.settings[key] = settings
                    # beta.15's activation belonged to a recurring schedule.
                    # Keep its configured values pending; never resume that clock.
                    self.settings[key].pop("grid_activated_at", None)
            except ValueError, TypeError, KeyError:
                continue

        self.normalize_settings()

        if migrated != dict(self.entry.options) and hasattr(
            self.entry, "async_on_unload"
        ):
            self.hass.config_entries.async_update_entry(self.entry, options=migrated)
        self.seed_power_history()
        if self.optimum_unsubscribe:
            self.optimum_unsubscribe()
            self.optimum_unsubscribe = None
        if self.references.get("soc_speicher_aktuell"):
            saved = await self.optimum_day_store.async_load() or {}
            # beta.5 active/ended did not prove surplus or forecast exhaustion.
            self.optimum_days.clear()
            self.optimum_saved_days = (
                saved["days"]
                if saved.get("version") == 2 and isinstance(saved.get("days"), dict)
                else {}
            )
            self.optimum_save_day()
            self.optimum_plan(datetime.now(UTC))

    def seed_power_history(self):
        from .pv_surplus import power_valid_for, reading

        now = datetime.now(UTC)
        for key in ("leistung_pv", "leistung_verbraucher"):
            history = PowerHistory(self.references.get("power_smoothing_window", 5))
            self.power_history[key] = history
            try:
                state = self.hass.states.get(self.references.get(key, ""))
                value = reading(state, now)
                valid_for = power_valid_for(state, now)
            except ValueError, TypeError, ZeroDivisionError, OverflowError:
                value, valid_for = None, 0
            history.add(self.monotonic(), value, valid_for=valid_for)

    def setting(self, target):
        """Ordinary reads never project or mutate persisted configuration."""
        settings = self.settings.get(target_key(target))
        return settings if settings is not None else self.default_settings()

    def default_settings(self):
        return {
            "profile": "NETZ",
            "power_kw": 11,
            "min_soc": 20,
            **PV_DEFAULTS,
            **OPTIMUM_DEFAULTS,
            **{k: self.entry.options[k] for k in DEFAULTS if k in self.entry.options},
        }

    def initialize_settings(self, target):
        key = target_key(target)
        if key not in self.settings:
            self.settings[key] = self.default_settings()
            self.normalize_settings(target)

    def normalize_settings(self, target=None):
        """Project at input/policy boundaries, never during HA property reads."""
        try:
            reserve = self.pv_reserve()
        except ValueError, TypeError, OverflowError:
            return  # Missing reserve still blocks execution through its live read.
        items = (
            self.settings.items()
            if target is None
            else ((target_key(target), self.settings[target_key(target)]),)
        )
        for key, settings in items:
            try:
                bounded = clamp_settings(settings, reserve)
            except ValueError, TypeError, OverflowError:
                continue  # Impossible legacy H stays visible and blocks policy.
            if any(settings[k] != bounded[k] for k in TARGET_FIELDS):
                bounded.setdefault(
                    "legacy_soc_targets", {k: settings[k] for k in TARGET_FIELDS}
                )
                self.settings[key] = bounded

    def available_profiles(self, target):
        # Entity options are scoped to this connector's owning integration entry.
        profiles = ["NETZ"]
        if any(
            all(self.references.get(key) for key in keys)
            for keys in (
                ("leistung_pv", "leistung_verbraucher"),
                ("grid_import_power", "grid_export_power"),
            )
        ):
            profiles.append("PV_SURPLUS")

        if self.references.get("min_soc_speicher") and all(
            self.references.get(key) for key in OPTIMUM_REFERENCES
        ):
            profiles.append("PV_OPTIMUM")
        if all(
            self.references.get(key)
            for key in (
                "min_soc_speicher",
                "soc_speicher_aktuell",
                "leistung_pv",
                "leistung_verbraucher",
                "storage_discharge_power",
                "grid_import_power",
                "grid_export_power",
            )
        ):
            profiles.append("PV_MAXIMUM")
        return profiles

    def can_control(self, target):
        return (
            not self.closed
            and self.control.profile_permitted(target)
            and self.control.runtime.authority(target.station)
            == ControlAuthority.REMOTE
        )

    async def reconcile_availability(self):
        for target in tuple(self.control.intents):
            if self.setting(target)["profile"] in self.available_profiles(target):
                continue
            if self.status.get(target) != "profile_unavailable":
                self.invalidate(target)
                self.suppressed.add(target)
                self.control._edit(target, {"target_w": Fraction(0)})
                self.status[target] = "profile_unavailable"
            epoch = self.epochs[target]
            if (
                self.can_control(target)
                and self.control.runtime.enabled(target) is True
            ):
                await self.control.request_enabled(
                    target,
                    False,
                    fence=lambda target=target, epoch=epoch: (
                        not self.closed and self.epochs[target] == epoch
                    ),
                )
            if (
                not self.closed
                and self.epochs[target] == epoch
                and self.control.runtime.enabled(target) is False
            ):
                self.setting(target)["profile"] = "NETZ"
                await self.save()
            self.control.publish(target)

    def attributes(self, target):
        return {
            **self.grid_attributes(target),
            **self.economic_attributes(target),
            "optimum_target_soc": float(self.optimum_targets[target])
            if target in self.optimum_targets
            else None,
            "enable_pending": target in self.enable_requests
            and self.control.runtime.enabled(target) is not True,
            "profile_start_pending": target in self.pv_startups,
            "retained_current_accounting": "unsupported"
            if self.setting(target)["profile"] != "NETZ"
            else "not_applicable",
            "enable_wait_reason": self.control.intent(target).status
            if target in self.enable_requests
            and self.control.runtime.enabled(target) is not True
            else None,
            "optimum_mode": self.optimum_modes.get(target),
            "soc_mode": self.optimum_modes.get(target, "STOP"),
            "target_soc": float(self.optimum_targets[target])
            if target in self.optimum_targets
            else None,
            "pv_day_state": self.optimum_day_for(target).state,
            "profile_status": self.pv_display_status(target),
            "pv_pause_reason": self.pv_pause_reasons.get(target),
            "pv_restart_reason": self.pv_restart_reasons.get(target),
            "pv_measurement_status": self.pv_measurement_status.get(target),
            "available_profiles": self.available_profiles(target),
            "battery_configured": bool(self.references.get("soc_speicher_aktuell"))
            if self.setting(target)["profile"]
            in ("PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM")
            else self.battery.configured,
            "profile_actively_charging": self.pv_ongoing.get(target, False),
            "battery_reserve_configured": self.battery.configured
            and self.setting(target)["profile"] == "NETZ",
            "battery_status": self.battery.status,
            "battery_reserve": dict(self.battery.diagnostics),
            "actual_charging": self.active(target),
            "profile_control_ready": self.control.profile_permitted(target),
        }

    def pv_display_status(self, target):
        """Control readiness precedes subordinate passive measurement failures."""
        if self.settings.get(target_key(target), {}).get("profile", "NETZ") in (
            "PV_SURPLUS",
            "PV_OPTIMUM",
            "PV_MAXIMUM",
        ):
            state = self.control.runtime.get(target.station)
            if state is None or not state.connected:
                return "disconnected"
            authority = self.control.runtime.authority(target.station)
            if authority == ControlAuthority.UNKNOWN:
                return "authority_unknown"
            if authority != ControlAuthority.REMOTE:
                return "no_authority"
            if not self.control.profile_permitted(target):
                return "inactive_wallbox"
        return self.status.get(target, "idle")

    @callback
    def battery_changed(self, event):
        if event.data.get("entity_id") == self.references.get("min_soc_speicher"):
            self.normalize_settings()
            for target in tuple(self.control.intents):
                self.control.publish(target)
        self.pv_measurement_changed(event)
        entity = event.data.get("entity_id")
        recovery = self.battery.record.get("entity") if self.battery.record else None
        if entity not in (self.battery.reserve, self.battery.soc, recovery):
            return
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if (
            new
            and new.state not in ("unknown", "unavailable")
            and (old is None or old.state in ("unknown", "unavailable"))
        ):
            self.battery.failed_restore = False
        self.sessions_changed()

    def active(self, target):
        state = self.control.runtime.get(target.station)
        if not state or not state.connected:
            return False
        if not any(
            s.active and s.scope == target for s in self.control.runtime.sessions.latest
        ):
            return False
        from .protocols.ocpp.v21.control_runtime import actively_charging

        sample = self.control.runtime.sessions.measurement(
            target, Quantity.POWER, datetime.now(UTC)
        )
        if sample is not None:
            scopes = [target]
            if [t for t in state.connectors if t.evse == target.evse] == [target]:
                scopes.append(target.evse)
            charging = max(
                (
                    o
                    for scope in scopes
                    if (o := state.observation(Channel(scope, Quantity.CHARGING_STATE)))
                    is not None
                ),
                key=lambda o: o.observed_at,
                default=None,
            )
            return bool(
                charging and charging.value == State.CHARGING and sample.value > 0
            )
        return actively_charging(state, target)

    def invalidate(
        self, target, *, preserve_phase_retry=False, preserve_economic_evidence=False
    ):
        self.economic_invalidate(target, preserve_evidence=preserve_economic_evidence)
        phase_retry = None
        if (
            preserve_phase_retry
            and self.pv_phase_retry.get(target, False)
            and self.control.phase_restricted(target)
            and self.can_control(target)
        ):
            phase_retry = (
                self.pv_retry_until.get(target),
                self.pv_retry_request.get(target),
            )
        if target in self.enable_requests:
            profile_event(
                self,
                target,
                "profile_cancelled"
                if self.control.runtime.enabled(target) is True
                else "enable_cancelled",
                reason="lifecycle_invalidated",
            )
        self.enable_requests.pop(target, None)
        self.enable_sessions.pop(target, None)
        self.optimum_regulators.pop(target, None)
        self.epochs[target] = self.epochs.get(target, 0) + 1
        for tasks in (
            self.tasks,
            self.debounce_tasks,
            self.recoveries,
            self.grid_timers,
        ):
            task = tasks.pop(target, None)
            if task and task is not asyncio.current_task():
                task.cancel()
        self.control.intent(target).generation += 1
        self.status[target] = "idle"
        self.pv_ongoing[target] = False
        self.pv_battery.pop(target, None)
        self.pv_startups.pop(target, None)
        self.pv_retry_until.pop(target, None)
        self.pv_retry_request.pop(target, None)
        self.pv_phase_retry.pop(target, None)
        self.pv_sessions.pop(target, None)
        self.pv_start_since.pop(target, None)
        self.pv_stop_since.pop(target, None)
        self.pv_expiry.pop(target, None)
        if phase_retry is not None:
            deadline, request = phase_retry
            if deadline is not None:
                self.pv_retry_until[target] = deadline
            if request is not None:
                self.pv_retry_request[target] = request
            self.pv_phase_retry[target] = True

    async def select(self, target, profile):
        if profile not in self.available_profiles(target):
            raise ValueError("unsupported profile")
        # Reselecting is an explicit safe stop too; future profiles use this boundary.
        self.invalidate(target)
        if profile == "NETZ":
            await self.economic_end_scope(target)
        self.pv_pause_reasons.pop(target, None)
        self.pv_pause_transactions.pop(target, None)
        self.suppressed.add(target)
        epoch = self.epochs[target]
        self.grid_consume(target, "cancelled")
        await self.save()
        if self.epochs[target] != epoch:
            return
        if hasattr(self.control, "ownership"):
            await self.control.ownership.permission_intent(self.control, target, False)
        if self.can_control(target):
            result = await self.control.request_enabled(target, False)
            if not result or result.status != CommandStatus.APPLIED:
                return
        self.control._edit(target, {"target_w": Fraction(0)})
        self.control.intent(target).reachable_only = profile in (
            "PV_OPTIMUM",
            "PV_MAXIMUM",
        )
        if self.epochs[target] != epoch:
            return
        self.setting(target)["profile"] = profile
        profile_event(self, target, "profile_selected", profile_name=profile)
        self.optimum_plans.pop(target, None)
        if profile in ("PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM"):
            self.optimum_plan(datetime.now(UTC))
        await self.save()
        await self.reconcile_battery(exclude=target)
        self.control.publish(target)

    @staticmethod
    def validate_pv(settings, reserve=0):
        from .control.requests import Direction

        if settings["approximation"] not in (Direction.UP, Direction.DOWN):
            raise ValueError("invalid PV approximation")
        optimum = {**OPTIMUM_DEFAULTS, **settings}
        minimum, maximum = target_bounds(reserve, settings["soc_hysterese"])
        if (
            not minimum
            <= scalar(optimum["optimum_lower_soc"])
            <= scalar(optimum["optimum_upper_soc"])
            <= maximum
        ):
            raise ValueError("invalid Optimum SoC limits")
        for key, limit in (
            ("optimum_max_discharge_w", 100000),
            ("estimated_daily_house_consumption_kwh", 1000),
        ):
            if not 0 <= scalar(optimum[key]) <= limit:
                raise ValueError("invalid Optimum power or energy limit")
        target = scalar(settings["soll_soc_speicher"])
        hysteresis = scalar(settings["soc_hysterese"])
        interval = scalar(settings["regulation_interval"])
        if (
            not minimum <= target <= maximum
            or hysteresis > 99
            or not 1 <= interval <= 300
            or any(
                scalar(settings[key]) > 3600
                for key in ("pv_start_delay", "pv_stop_delay")
            )
        ):
            raise ValueError("invalid PV thresholds or interval")

    async def set_value(self, target, field, value):
        self.initialize_settings(target)
        self.normalize_settings(target)
        if field in ("grid_start_delay", "grid_duration"):
            value = duration_seconds(value)
            if self.setting(target).get("grid_request"):
                raise ValueError(
                    "NETZ timing is armed; disable permission before editing"
                )
            if self.setting(target).get(field) == value:
                return
            self.setting(target)[field] = value
            await self.save()
            self.control.publish(target)
            return
        if field in DEFAULTS:
            settings = {**self.setting(target), field: float(scalar(value))}
            if field == "soc_hysterese":
                settings = clamp_settings(
                    settings,
                    self.pv_reserve() if self.references.get("min_soc_speicher") else 0,
                )
            self.validate_pv(
                settings,
                self.pv_reserve() if self.references.get("min_soc_speicher") else 0,
            )
            if field == "power_smoothing_window" and not 0 <= settings[field] <= 300:
                raise ValueError("invalid smoothing window")
            options = {**self.entry.options, field: settings[field]}
            if hasattr(self.entry, "async_on_unload"):
                self.hass.config_entries.async_update_entry(self.entry, options=options)
                await self.hass.config_entries.async_reload(self.entry_id)
                return
            self.entry.options = options
            self.references.update(options)
            for existing in self.settings.values():
                existing[field] = settings[field]
            self.normalize_settings()
            if field == "soc_hysterese":
                self.settings[target_key(target)] = settings
            if field == "power_smoothing_window":
                self.seed_power_history()
                return
        if field in PV_DEFAULTS or field in OPTIMUM_DEFAULTS:
            value = str(value) if field == "approximation" else float(scalar(value))
            settings = {**self.setting(target), field: value}
            if field == "soc_hysterese":
                settings = clamp_settings(
                    settings,
                    self.pv_reserve() if self.references.get("min_soc_speicher") else 0,
                )
            self.validate_pv(
                settings,
                self.pv_reserve() if self.references.get("min_soc_speicher") else 0,
            )
            self.settings[target_key(target)] = settings
            profile = self.setting(target)["profile"]
            relevant = (
                (profile == "PV_SURPLUS" and field in PV_DEFAULTS)
                or (
                    profile == "PV_OPTIMUM"
                    and (field in OPTIMUM_DEFAULTS or field in DEFAULTS)
                )
                or (
                    profile in ("PV_SURPLUS", "PV_MAXIMUM")
                    and (field == "optimum_max_discharge_w" or field in DEFAULTS)
                )
            )
            if relevant:
                ongoing = self.pv_ongoing.get(target, False)
                session = self.pv_sessions.get(target)
                self.invalidate(target, preserve_phase_retry=True)
                self.pv_ongoing[target] = ongoing
                self.pv_sessions[target] = session
                if self.valid(target, self.epochs[target]):
                    self.launch(target)
            self.control.publish(target)
            await self.save()
            return
        value = scalar(value)
        if field not in ("power_kw", "min_soc") or value > 100:
            raise ValueError("invalid profile setting")
        maximum = self.control.power_ceiling(target) if field == "power_kw" else None
        if maximum is not None and value * 1000 > maximum:
            raise ValueError(
                "Requested power exceeds available limit "
                f"({float(maximum / 1000):g} kW)"
            )
        if field == "min_soc" and value.denominator != 1:
            raise ValueError("discharge reserve must be a whole percent")
        self.setting(target)[field] = float(value)
        if self.setting(target)["profile"] in (
            "PV_SURPLUS",
            "PV_OPTIMUM",
            "PV_MAXIMUM",
        ):
            self.sessions_changed()
            await self.save()
            self.control.publish(target)
            return
        if field == "power_kw":
            self.invalidate(target)
            self.control._edit(target, {"target_w": self.grid_target(target)})
        epoch = self.epochs.get(target, 0)
        if (
            field == "power_kw"
            and self.epochs.get(target, 0) == epoch
            and target not in self.suppressed
            and self.can_control(target)
            and self.control.runtime.enabled(target) is True
        ):
            # Schedule before persistence: the deadline is measured from the edit,
            # not from disk latency. Every edit advances both command and task fences.
            self.debounce_tasks[target] = self.hass.async_create_background_task(
                self.debounced_start(
                    target,
                    epoch,
                    self.control.intent(target).generation,
                    delay=value != 0,
                ),
                "Grid power debounce",
            )
        self.sessions_changed()
        self.control.publish(target)
        await self.save()

    async def debounced_start(self, target, epoch, generation, *, delay=True):
        try:
            if delay:
                await self.debounce_wait(1)
            if (
                self.valid(target, epoch)
                and self.control.intent(target).generation == generation
            ):
                await self.start(target)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.status[target] = "error"
            _LOGGER.exception("Grid power application failed")
        finally:
            if self.debounce_tasks.get(target) is asyncio.current_task():
                self.debounce_tasks.pop(target, None)
                self.control.publish(target)

    @diagnostic_permission
    async def permission(self, target, enabled, *, _grid_expiry=False, _resume=False):
        """Confirm user permission independently of profile preparation."""
        if not enabled:
            # Revoke queued work before any persistence await can yield to ACK.
            self.invalidate(target)
            await self.economic_end_scope(target)
        pv = self.setting(target)["profile"] in (
            "PV_SURPLUS",
            "PV_OPTIMUM",
            "PV_MAXIMUM",
        )
        if (
            enabled
            and self.can_control(target)
            and (self.setting(target)["profile"] in self.available_profiles(target))
        ):
            owner = getattr(self.control, "ownership", None)
            if (
                self.control.runtime.enabled(target) is True
                and not _resume
                and (owner is None or owner.record["enabled_intent"] is True)
                and not (
                    not pv
                    and any(
                        self.setting(target).get(field) is not None
                        for field in ("grid_start_delay", "grid_duration")
                    )
                )
            ):
                epoch = self.epochs.get(target, 0)
                generation = self.control.intent(target).generation
                context = self.control.runtime.get(target.station)
                if hasattr(self.control, "ownership"):
                    await self.control.ownership.permission_intent(
                        self.control, target, True
                    )
                if (
                    self.epochs.get(target, 0) != epoch
                    or self.control.intent(target).generation != generation
                    or not self.control.runtime.current(context.token)
                    or self.control.runtime.get(target.station).authority_revision
                    != context.authority_revision
                    or self.control.runtime.enabled(target) is not True
                ):
                    return CommandResult(
                        CommandStatus.TEMPORARILY_REJECTED,
                        ControlArea.CHARGING_PERMISSION,
                        CommandReason.STALE,
                    )
                return await self.control.request_enabled(target, True)
            if pv and target in self.enable_requests:
                return CommandResult(
                    CommandStatus.TEMPORARILY_REJECTED,
                    ControlArea.CHARGING_PERMISSION,
                    CommandReason.BUSY,
                    "Enable already pending.",
                )
        try:
            return await self._permission_request(
                target, enabled, _grid_expiry=_grid_expiry, _resume=_resume
            )
        finally:
            if self.enable_requests.get(target) is asyncio.current_task():
                if target not in self.pv_startups:
                    self.enable_requests.pop(target, None)
                    self.enable_sessions.pop(target, None)
                    self.control.publish(target)

    async def _permission_request(
        self, target, enabled, *, _grid_expiry=False, _resume=False
    ):
        if enabled and (
            not self.can_control(target)
            or self.setting(target)["profile"] not in self.available_profiles(target)
        ):
            status = (
                "profile_unavailable"
                if self.setting(target)["profile"]
                not in self.available_profiles(target)
                else (
                    "inactive_wallbox"
                    if not self.control.profile_permitted(target)
                    else "no_authority"
                )
            )
            self.control.intent(target).status = status
            self.control.publish(target)
            return CommandResult(
                CommandStatus.TEMPORARILY_REJECTED,
                ControlArea.CHARGING_PERMISSION,
                CommandReason.NO_AUTHORITY,
                status,
            )
        self.invalidate(target)
        epoch = self.epochs[target]
        profile_event(
            self,
            target,
            "enable_requested" if enabled else "disable_requested",
            epoch=epoch,
        )
        if enabled and self.setting(target)["profile"] in (
            "PV_SURPLUS",
            "PV_OPTIMUM",
            "PV_MAXIMUM",
        ):
            self.enable_requests[target] = asyncio.current_task()
            session = self.control.runtime.sessions.get(target)
            self.enable_sessions[target] = (
                session.session_id if session and session.active else None
            )
        if hasattr(self.control, "ownership"):
            await self.control.ownership.permission_intent(
                self.control, target, enabled
            )
        if self.epochs[target] != epoch:
            return None
        if enabled:
            self.suppressed.discard(target)
        else:
            self.suppressed.add(target)
        if self.setting(target)["profile"] == "NETZ":
            if enabled and not _resume:
                self.grid_arm(target)
            elif not enabled:
                if _grid_expiry and self.setting(target).get("grid_request"):
                    self.setting(target)["grid_request"]["stopping"] = True
                else:
                    self.grid_consume(target, "cancelled")
            await self.save()
            if self.epochs[target] != epoch:
                return None
        if (
            enabled
            and self.setting(target)["profile"] == "NETZ"
            and self.grid_phase(target)[0] == "expired"
        ):
            return await self.permission(target, False, _grid_expiry=True)
        self.control.intent(target).profile_modes = None
        if not self.shared_pv_execution(target):
            self.control._edit(
                target,
                {"target_w": self.grid_target(target) if enabled else Fraction(0)},
            )
        generation = self.control.intent(target).generation + 1
        start_context = self.control.runtime.get(target.station)
        result = await self.control.request_enabled(
            target, enabled, fence=lambda: self.epochs[target] == epoch
        )
        if self.epochs[target] != epoch:
            return result
        if enabled and result and result.status == CommandStatus.APPLIED:
            profile_event(self, target, "enable_confirmed", epoch=epoch)
            if self.setting(target)["profile"] in (
                "PV_SURPLUS",
                "PV_OPTIMUM",
                "PV_MAXIMUM",
            ):
                await self.pv_start_after_permission(target, epoch, resume=_resume)
            else:
                await self.start(target)
        elif (
            enabled
            and self.setting(target)["profile"]
            in ("PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM")
            and result
            and result.status == CommandStatus.TEMPORARILY_REJECTED
            and self.can_control(target)
            and self.control.intent(target).generation == generation
            and start_context is not None
            and self.control.runtime.current(start_context.token)
            and self.control.runtime.get(target.station).authority_revision
            == start_context.authority_revision
        ):
            self.economic_activation_pending[target] = _resume
            self.pv_schedule_startup(target, epoch)
        if (
            enabled
            and self.setting(target)["profile"] == "NETZ"
            and self.setting(target).get("grid_request")
            and self.can_control(target)
        ):
            # A rejected initial point must not discard the armed end deadline.
            # The timer never grants permission; normal apply fences still apply.
            self.grid_launch_timer(target)
        if _grid_expiry:
            if result and result.status == CommandStatus.APPLIED:
                self.grid_consume(target, "consumed")
                await self.save()
                if self.epochs[target] != epoch:
                    return result
                self.status[target] = "grid_expired"
                self.control.publish(target)
            else:
                self.grid_launch_timer(target, retry=True)
        if result and result.status in (
            CommandStatus.FAILED,
            CommandStatus.UNSUPPORTED,
        ):
            profile_event(
                self,
                target,
                "enable_failed" if enabled else "disable_failed",
                reason=result.reason,
                detail=result.detail,
            )
        if not enabled:
            await self.reconcile_battery(exclude=target)
        return result

    async def pv_start_after_permission(self, target, epoch, *, resume):
        """Prepare regulation after ON; readiness never changes permission success."""
        prepared = await self.economic_prepare(target, resume=resume)
        if self.epochs.get(target, 0) != epoch:
            return
        if not prepared:
            reason = self.economic_reason(target)
            self.control.preparation_wait(target, reason)
            if reason == "economic_recovery_transaction":
                self.economic_activation_pending[target] = resume
                self.pv_schedule_startup(target, epoch)
            return
        await self.control.wait_for_pending_point(target)
        if self.epochs.get(target, 0) != epoch:
            return
        if self.common_pv(target):
            self.optimum_initialize(target)
        plan = self.pv_edit(target)
        if plan is None:
            self.control.preparation_wait(
                target,
                self.control.blocker(target)
                or self.status.get(target, "policy_unavailable"),
            )
            self.pv_schedule_startup(target, epoch)
            return
        generation = self.control.intent(target).generation
        context = self.control.runtime.get(target.station)
        result = await self.pv_apply_startup(target, epoch)
        if self.epochs.get(target, 0) != epoch:
            return
        if result and result.status == CommandStatus.APPLIED:
            self.pv_confirm(target)
            self.launch(target)
        elif (
            result
            and result.status == CommandStatus.TEMPORARILY_REJECTED
            and self.can_control(target)
            and self.control.intent(target).generation == generation
            and context
            and self.control.runtime.current(context.token)
            and self.control.runtime.get(target.station).authority_revision
            == context.authority_revision
        ):
            self.pv_schedule_startup(target, epoch)

    async def start(self, target):
        if self.setting(target)["profile"] in (
            "PV_SURPLUS",
            "PV_OPTIMUM",
            "PV_MAXIMUM",
        ):
            self.launch(target)
            return
        epoch = self.epochs.get(target, 0)
        self.control.intent(target).profile_modes = None
        self.control._edit(target, {"target_w": self.grid_target(target)})
        await self.control.apply_stored(target)
        if self.epochs.get(target, 0) == epoch:
            self.launch(target)

    def launch(self, target, *, stop_first=False):
        if self.setting(target)["profile"] in (
            "PV_SURPLUS",
            "PV_OPTIMUM",
            "PV_MAXIMUM",
        ):
            if target not in self.tasks or self.tasks[target].done():
                self.tasks[target] = self.hass.async_create_background_task(
                    self.pv_sequence(
                        target, self.epochs.get(target, 0), stop_first=stop_first
                    ),
                    "PV regulation",
                )
            return
        self.grid_launch_timer(target)
        if self.grid_phase(target)[0] != "active":
            self.control.publish(target)
            return
        epoch = self.epochs.get(target, 0)
        self.tasks[target] = self.hass.async_create_background_task(
            self.sequence(target, epoch), "Grid observation"
        )

    def valid(self, target, epoch):
        return (
            not self.closed
            and self.epochs.get(target, 0) == epoch
            and self.control.profile_permitted(target)
            and self.control.runtime.enabled(target) is True
            and self.control.runtime.authority(target.station)
            == ControlAuthority.REMOTE
            and self.setting(target)["profile"] in self.available_profiles(target)
        )

    async def sequence(self, target, epoch):
        tried = set()
        try:
            while self.valid(target, epoch):
                intent = self.control.intent(target)
                self.status[target] = (
                    "phase_lockout" if intent.phase_retry else "observing"
                )
                self.control.publish(target)
                applied_at = datetime.now(UTC)
                generation = intent.generation
                await self.wait(60)
                if not self.valid(target, epoch) or intent.generation != generation:
                    return
                if intent.phase_retry:
                    await self.control.apply_stored(target)
                    if (
                        not intent.command_result
                        or intent.command_result.status != CommandStatus.APPLIED
                    ):
                        return
                    continue
                point = intent.solver_result.point if intent.solver_result else None
                if (
                    not point
                    or not intent.command_result
                    or intent.command_result.status != CommandStatus.APPLIED
                ):
                    # Permission can precede point evidence or confirmation.
                    # Never use an unconfirmed point for phase observation.
                    await self.control.apply_stored(target)
                    continue
                if not point.charging:
                    break
                count = point.mode.count
                tried.add(count)
                state = self.control.runtime.get(target.station)

                def current(phase, state=state, applied_at=applied_at):
                    sample = state.observation(
                        Channel(target, Quantity(f"current_l{phase}"))
                    )
                    return (
                        sample.value
                        if (
                            sample
                            and sample.fresh(datetime.now(UTC))
                            and applied_at <= sample.observed_at <= datetime.now(UTC)
                        )
                        else None
                    )

                if count > 1:
                    values = [current(p) for p in range(2, count + 1)]
                    if any(v is None or v >= 3 for v in values):
                        break
                    intent.profile_modes = (1,)
                else:
                    measured = current(1)
                    if (
                        measured is None
                        or abs(point.current_a - measured) <= 2
                        or any(n > 1 for n in tried)
                    ):
                        break
                    intent.profile_modes = (2, 3)
                    _, candidate, blocked = self.control.resolve(target)
                    if (
                        blocked
                        or not candidate
                        or not candidate.point
                        or abs(
                            candidate.point.offered_power_w - intent.request.target_w
                        )
                        >= abs(
                            measured * point.phase_voltages_v[0]
                            - intent.request.target_w
                        )
                    ):
                        intent.profile_modes = (1,)
                        break
                await self.control.apply_stored(target)
                if (
                    not intent.command_result
                    or intent.command_result.status != CommandStatus.APPLIED
                ):
                    return
            self.status[target] = "complete"
        except asyncio.CancelledError:
            raise
        except Exception:
            self.status[target] = "error"
            _LOGGER.exception("Grid profile observation failed")
        finally:
            self.control.publish(target)

    def changed(self, snapshot):
        for target in snapshot.connectors:
            self.initialize_settings(target)
        for target in (
            self.tasks.keys()
            | self.debounce_tasks.keys()
            | self.grid_timers.keys()
            | self.enable_requests.keys()
        ):
            if target.station == snapshot.token.station and (
                not snapshot.connected
                or (
                    self.control.runtime.enabled(target) is not True
                    and not self.pv_startup_valid(target, self.epochs.get(target, 0))
                    and target not in self.enable_requests
                )
                or self.control.runtime.authority(target.station)
                != ControlAuthority.REMOTE
            ):
                self.invalidate(target)
        for target in tuple(self.control.intents):
            if target.station != snapshot.token.station:
                continue
            self.economic_refresh(target)
            context = (snapshot.token, snapshot.authority_revision, snapshot.connected)
            if self.pv_authority_contexts.get(target) != context:
                self.pv_authority_contexts[target] = context
                self.control.publish(target)
        self.sessions_changed()

    def sessions_changed(self):
        economic_before = {
            target: self.economic_attributes(target)
            for target in self.economic_policies.keys()
            | self.economic_activation_pending.keys()
        }
        self.economic_sessions_changed()
        for target in (
            self.economic_policies.keys() | self.economic_activation_pending.keys()
        ):
            if self.setting(target)["profile"] not in (
                "PV_SURPLUS",
                "PV_OPTIMUM",
                "PV_MAXIMUM",
            ):
                continue
            for observation in self.selected_power_observations(target):
                self.economic_first_acceptance(observation)
            self.economic_observe(target, activity_event=True)
        for target in tuple(self.control.intents):
            self.economic_wake_startup(target)
            if self.common_pv(target):
                self.optimum_connection(target)
            session = self.control.runtime.sessions.get(target)
            if (
                target in self.pv_startups
                and self.control.runtime.enabled(target) is True
                and session
                and self.enable_sessions.get(target) is not None
                and not session.active
                and session.end_reason != "superseded"
            ):
                self.invalidate(target)
            elif target in self.enable_requests and session and session.active:
                self.enable_sessions[target] = session.session_id
        for target in tuple(self.pv_sessions):
            session = self.control.runtime.sessions.get(target)
            if session and not session.active:
                self.pv_sync_session(target)
        for target, before in economic_before.items():
            if self.economic_attributes(target) != before:
                self.control.publish(target)
        self.battery_dirty = True
        if not self.closed and (self.battery_task is None or self.battery_task.done()):
            self.battery_task = self.hass.async_create_background_task(
                self.battery_events(), "Grid battery reserve"
            )

    async def battery_events(self):
        while self.battery_dirty and not self.closed:
            self.battery_dirty = False
            await self.reconcile_availability()
            if not self.closed:
                await self.reconcile_battery()

    async def reconcile_battery(self, exclude=None):
        before = self.battery.status, dict(self.battery.diagnostics)
        requests = [
            self.setting(t)["min_soc"]
            for s in self.control.runtime.stations
            for t in s.connectors
            if t != exclude
            and self.setting(t)["profile"] == "NETZ"
            and t not in self.suppressed
            and self.control.profile_permitted(t)
            and self.active(t)
            and self.control.runtime.enabled(t) is True
            and self.control.runtime.authority(t.station) == ControlAuthority.REMOTE
        ]
        owner = getattr(self.control, "ownership", None)
        if self.battery.recovering and owner and owner.record:
            owned_control, target = owner.resolve(owner.active_wallbox)
            if (
                owned_control is self.control
                and owner.record["enabled_intent"]
                and self.setting(target)["profile"] == "NETZ"
            ):
                if not requests:
                    # Await evidence without restoring/reasserting during startup.
                    if not owner.ready or self.control.runtime.enabled(target) is None:
                        self.battery.status = "waiting_ownership_evidence"
                        return
                    session = self.control.runtime.sessions.get(target)
                    if session is None or (
                        session.active
                        and (
                            self.control.confirmed_point(target) is None
                            or self.control.runtime.sessions.measurement(
                                target, Quantity.POWER, datetime.now(UTC)
                            )
                            is None
                        )
                    ):
                        self.battery.status = "waiting_charging_evidence"
                        return
                else:
                    self.battery.resume()
        await self.battery.update(max(requests) if requests else None)
        if before != (self.battery.status, self.battery.diagnostics):
            for target in self.control.intents:
                self.control.publish(target)

    @diagnostic_recovery
    async def recover(self, target, record):
        """Resume persisted intent only after ownership and live evidence agree."""
        epoch = self.epochs.get(target, 0)
        generation = self.control.intent(target).generation
        runtime = self.control.runtime

        recovery_snapshot(
            "start",
            lambda: dict(
                profile=self.settings.get(target_key(target), {}).get("profile"),
                enabled_intent=record["enabled_intent"],
                enabled_actual=runtime.enabled(target),
                ownership=getattr(
                    getattr(self.control, "ownership", None), "status", None
                ),
                matching_active_transaction=bool(
                    runtime.sessions.get(target)
                    and runtime.sessions.get(target).active
                    and record.get("transaction")
                    == runtime.sessions.get(target).external_transaction_id
                ),
                generation=generation,
                profile_epoch=epoch,
            ),
        )
        last_wait = None

        def waiting(reason, seconds):
            nonlocal last_wait
            if reason != last_wait:
                recovery_record(
                    "waiting", reason=reason, pending=True, retry_seconds=seconds
                )
                last_wait = reason

        def current():
            state = runtime.get(target.station)
            return bool(
                not self.closed
                and self.epochs.get(target, 0) == epoch
                and self.control.intent(target).generation == generation
                and self.can_control(target)
                and state
                and state.connected
                and target in state.connectors
            )

        while current():
            enabled = runtime.enabled(target)
            persisted = self.settings.get(target_key(target), {})
            if (
                persisted.get("profile") == "NETZ"
                and self.grid_phase(target)[0] == "expired"
            ):
                await self.permission(target, False, _grid_expiry=True)
                return
            if not record["enabled_intent"]:
                if persisted.get("profile") == "NETZ" and persisted.get("grid_request"):
                    self.grid_consume(target, "cancelled")
                    await self.save()
                    if not current():
                        return
                self.suppressed.add(target)
                if enabled is True:
                    generation += 1  # The permission operation owns this edit.
                    await self.control.request_enabled(target, False, fence=current)
                recovery_record("complete", reason="restored_permission_off")
                self.status[target] = "restored_permission_off"
                return
            self.suppressed.discard(target)
            if target_key(target) not in self.settings:
                recovery_record("rejected", reason="recovery_missing_profile")
                self.status[target] = "recovery_missing_profile"
                return
            if enabled is False:
                recovery_record("resume", reason="persisted_permission_on")
                await self.permission(target, True, _resume=True)
                return
            inputs = self.control.inputs(target)
            if inputs and self.control.blocker(target) is None and enabled is not None:
                if self.setting(target)["profile"] in (
                    "PV_SURPLUS",
                    "PV_OPTIMUM",
                    "PV_MAXIMUM",
                ):
                    try:
                        self.pv_measurements(target)
                    except ValueError, TypeError, ZeroDivisionError, OverflowError:
                        self.status[target] = "recovery_waiting_measurements"
                        waiting(
                            "recovery_waiting_measurements",
                            self.setting(target)["regulation_interval"],
                        )
                        await self.wait(self.setting(target)["regulation_interval"])
                        continue
                    prepared = await self.economic_prepare(target, resume=True)
                    if not current():
                        recovery_record(
                            "fence", reason="profile_recovery_context_changed"
                        )
                        return
                    if not prepared:
                        reason = self.economic_reason(target)
                        self.status[target] = reason
                        self.control.preparation_wait(target, reason)
                        recovery_record("waiting", reason=reason, pending=True)
                        return
                point = await self.control.reconcile_applied(target, fence=current)
                if not current():
                    recovery_record("fence", reason="profile_recovery_context_changed")
                    return
                if point is not None:
                    session = runtime.sessions.get(target)
                    if (
                        not point.charging
                        and record.get("pause_reason") == "optimum_deliberate_pause"
                        and session
                        and session.active
                        and record.get("transaction") == session.external_transaction_id
                    ):
                        self.pv_pause_reasons[target] = record["pause_reason"]
                        self.pv_pause_transactions[target] = (
                            session.external_transaction_id
                        )
                    continuing = bool(
                        point.charging
                        and session
                        and session.active
                        and record.get("continuation") is True
                        and record.get("transaction") == session.external_transaction_id
                    )
                    self.pv_sessions[target] = session.session_id if session else None
                    self.pv_ongoing[target] = continuing
                    self.pv_battery[target] = (
                        continuing and record.get("battery_allowed") is True
                    )
                    saved_mode = record.get("soc_mode", "PV_BALANCE")
                    self.optimum_modes[target] = (
                        saved_mode
                        if continuing and saved_mode in ("PV_BALANCE", "FAST_DISCHARGE")
                        else "STOP"
                    )
                    self.optimum_initializations.discard(target)
                    if self.setting(target)["profile"] in (
                        "PV_SURPLUS",
                        "PV_OPTIMUM",
                        "PV_MAXIMUM",
                    ):
                        self.pv_edit(target)
                        self.launch(target)
                    else:
                        self.control._edit(
                            target,
                            {"target_w": self.grid_target(target)},
                        )
                        generation = self.control.intent(target).generation
                        await self.control.apply_stored(
                            target, reuse_applied=True, fence=current
                        )
                        self.launch(target)
                    self.control.publish(target)
                    return
                self.status[target] = "recovery_waiting_electrical"
            else:
                self.status[target] = "recovery_waiting_runtime"
            self.control.publish(target)
            waiting(
                self.status[target],
                60
                if self.status[target] == "recovery_waiting_electrical"
                else self.setting(target)["regulation_interval"],
            )
            await self.wait(
                60
                if self.status[target] == "recovery_waiting_electrical"
                else self.setting(target)["regulation_interval"]
            )

        recovery_record("fence", reason="profile_recovery_context_changed")

    async def save(self):
        await self.store.async_save(
            {
                key: {
                    field: value
                    for field, value in settings.items()
                    if field not in DEFAULTS
                }
                for key, settings in self.settings.items()
            }
        )

    async def close(self):
        if self.closed:
            return
        self.closed = True
        reset_diagnostics(self.entry)
        if hasattr(self.control, "ownership"):
            await self.control.ownership.suspend(self.control)
        if self.optimum_unsubscribe:
            self.optimum_unsubscribe()
        self.unsubscribe_battery()
        self.unsubscribe()
        self.session_unsubscribe()
        tasks = [
            *self.tasks.values(),
            *self.debounce_tasks.values(),
            *self.recoveries.values(),
            *self.grid_timers.values(),
        ]
        for target in (
            self.tasks.keys()
            | self.debounce_tasks.keys()
            | self.recoveries.keys()
            | self.grid_timers.keys()
        ):
            self.invalidate(target)
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.battery_task:
            await self.battery_task
        await self.economic_close()
        owner = getattr(self.control, "ownership", None)
        if not (
            owner
            and owner.record
            and owner.record["enabled_intent"]
            and owner.resolve(owner.active_wallbox)[0] is self.control
        ):
            await self.battery.update(None)
        await self.save()
        reset_diagnostics(self.entry)
