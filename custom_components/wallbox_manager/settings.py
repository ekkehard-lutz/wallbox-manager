"""Validated local preferences in the existing, losslessly preserved profile store."""

import asyncio
import json
import math
import re
from fractions import Fraction

from homeassistant.core import CoreState

# Public roles -> legacy storage field, default, minimum, maximum, step, unit.
NUMBERS = {
    "desired_charging_power": ("target_w", 0, 0, 100000, 100, "W"),
    **{
        f"allowed_current_{n}p": (f"allowed_current_{n}p", None, 0, 100000, 1, "A")
        for n in (1, 2, 3)
    },
    "soll_power": ("power_kw", 11, 0, 100, 0.1, "kW"),
    "min_soc": ("min_soc", 20, 0, 100, 1, "%"),
    "soll_soc_speicher": ("soll_soc_speicher", 95, 0, 99, 1, "%"),
    "soc_hysterese": ("soc_hysterese", 2, 0, 50, 1, "%"),
    "optimum_lower_soc": ("optimum_lower_soc", 20, 0, 100, 1, "%"),
    "optimum_upper_soc": ("optimum_upper_soc", 80, 0, 100, 1, "%"),
    "optimum_max_discharge_w": ("optimum_max_discharge_w", 0, 0, 100000, 100, "W"),
    "estimated_daily_house_consumption_kwh": (
        "estimated_daily_house_consumption_kwh",
        0,
        0,
        1000,
        0.1,
        "kWh",
    ),
    "regulation_interval": ("regulation_interval", 5, 1, 300, 1, "s"),
    "pv_start_delay": ("pv_start_delay", 0, 0, 3600, 1, "s"),
    "pv_stop_delay": ("pv_stop_delay", 90, 0, 3600, 1, "s"),
}
SELECTS = {
    "charging_profile": ("profile", ["NETZ", "PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM"]),
    "pv_approximation": ("approximation", ["up", "down"]),
    "power_approximation": ("direction", ["up", "down", "nearest"]),
}
TIMES = ("grid_start_delay", "grid_duration")
GRID_FIELDS = ("power_kw", *TIMES)
REGULATION = ("soc_hysterese", "regulation_interval", "pv_start_delay", "pv_stop_delay")


def duration_seconds(value):
    if value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2,}:[0-5][0-9]", value):
        raise ValueError("Duration must be hh:mm or empty")
    hours, minutes = map(int, value.split(":"))
    return hours * 3600 + minutes * 60


def duration_text(value):
    return "" if value is None else f"{value // 3600:02d}:{value % 3600 // 60:02d}"


class Settings:
    def __init__(self, hass, entry, binding, store, profiles):
        self.hass, self.entry, self.store = hass, entry, store
        self.profiles = profiles
        self.key = json.dumps(list(binding))
        saved = profiles.get(self.key, {})
        self.values = {spec[0]: spec[1] for spec in NUMBERS.values()}
        self.values.update({spec[0]: spec[1][0] for spec in SELECTS.values()})
        self.values.update(approximation="down", direction="nearest")
        self.values.update(dict.fromkeys(TIMES))
        self.values.update(saved)
        # Restore caps before any connection/control task can use default maxima.
        # Entities are added only after capability discovery, which is too late
        # to make their RestoreEntity callback the sole migration boundary.
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers import restore_state

        from .const import DOMAIN
        from .entity import unique_id

        registry = er.async_get(hass)
        restored = restore_state.async_get(hass).last_states
        for mode in (1, 2, 3):
            role = f"allowed_current_{mode}p"
            if role in saved:
                continue
            entity_id = registry.async_get_entity_id(
                "number", DOMAIN, unique_id(entry.entry_id, "control", binding, role)
            )
            previous = restored.get(entity_id)
            if previous and previous.state.state not in ("unknown", "unavailable"):
                self.values[role] = (
                    previous.state.attributes.get("exact_current_limit_a")
                    or previous.state.state
                )
        # Old central options are authoritative for these fields. Preserve seconds-
        # based smoothing unchanged; future regulator must migrate its units.
        for key in REGULATION:
            if key in entry.options:
                self.values[key] = entry.options[key]
        self.state = None
        self.listeners = {}
        self.lock = asyncio.Lock()
        self.values.setdefault("grid_draft_revision", 0)
        self.values.setdefault("grid_approved", {**self.grid_draft(), "revision": 0})
        self.values.setdefault(
            "grid_next_timing", {k: self.values.get(k) for k in TIMES}
        )
        self.grid_equivalent = lambda values: False
        self.approval_error = None

    async def initialize_grid(self):
        """Migrate active beta.10 settings once, before entities/transport start."""
        if "grid_approved" not in self.profiles.get(self.key, {}):
            profiles = {
                **self.profiles,
                self.key: {
                    **self.profiles.get(self.key, {}),
                    "grid_draft_revision": self.values["grid_draft_revision"],
                    "grid_approved": self.values["grid_approved"],
                    "grid_next_timing": self.values["grid_next_timing"],
                },
            }
            await self.persist(profiles)
            self.profiles = profiles

    def grid_draft(self, values=None):
        values = self.values if values is None else values
        return {key: values.get(key) for key in GRID_FIELDS}

    def approved(self, role):
        key = NUMBERS[role][0] if role in NUMBERS else role
        return self.values["grid_approved"].get(key)

    def approval_attributes(self):
        approved = self.values.get("grid_approved", self.grid_draft())
        pending = self.grid_draft() != {key: approved.get(key) for key in GRID_FIELDS}
        try:
            self.validate("soll_power", self.get("soll_power"))
            for role in TIMES:
                self.validate(role, duration_text(self.get(role)))
            ready = True
        except ValueError, TypeError, OverflowError:
            ready = False
        return {
            "grid_draft_revision": self.values["grid_draft_revision"],
            "grid_set_required": pending,
            "grid_set_ready": ready,
            "grid_approved_power_kw": approved["power_kw"],
            "grid_approved_start_delay": approved.get("grid_start_delay"),
            "grid_approved_duration": approved.get("grid_duration"),
            "grid_approved_revision": approved.get("revision", 0),
            "grid_approval_error": self.approval_error,
        }

    def equivalent_grid_draft(self, values):
        if values["power_kw"] == self.approved("soll_power") or any(
            values.get(k) != self.approved(k) for k in TIMES
        ):
            return False
        try:
            self.validate("soll_power", values["power_kw"])
            return self.grid_equivalent(values)
        except ValueError, TypeError, OverflowError:
            return False

    async def approve_equivalent_grid(self):
        """Recheck the latest draft/context after an affected decision settles."""
        async with self.lock:
            if not self.equivalent_grid_draft(self.values):
                return
            updated = dict(self.values)
            self.approve_values(updated)
            try:
                await self._save(updated)
            except (ValueError, TypeError, OSError) as exc:
                self.approval_error = str(exc)
                self.notify_grid()
                return
            self.approval_error = None
            self.notify("grid_equivalent")
            self.notify_grid()

    def approve_values(self, updated):
        previous = self.values["grid_approved"]
        updated["grid_approved"] = {
            **self.grid_draft(updated),
            "revision": previous["revision"] + 1,
        }
        pending = dict(updated["grid_next_timing"])
        for key in TIMES:
            if updated.get(key) != previous.get(key):
                pending[key] = updated.get(key)
        updated["grid_next_timing"] = pending

    async def approve_grid(self):
        async with self.lock:
            if not self.approval_attributes()["grid_set_required"]:
                return
            try:
                self.validate("soll_power", self.get("soll_power"))
                for role in TIMES:
                    self.validate(role, duration_text(self.get(role)))
                updated = dict(self.values)
                power_changed = updated["power_kw"] != self.approved("soll_power")
                equivalent = not power_changed or self.grid_equivalent(updated)
                self.approve_values(updated)
                await self._save(updated)
            except (ValueError, TypeError, OSError) as exc:
                self.approval_error = str(exc)
                self.notify_grid()
                raise
            self.approval_error = None
            self.notify(
                "grid_timing"
                if not power_changed
                else "grid_equivalent"
                if equivalent
                else "grid_approved"
            )
            self.notify_grid()

    def notify(self, role):
        for listener in tuple(self.listeners.get(role, ())):
            listener()

    def notify_grid(self):
        for role in ("soll_power", *TIMES, "grid_set"):
            self.notify(role)

    def watch(self, role, listener):
        self.listeners.setdefault(role, set()).add(listener)
        return lambda: self.listeners[role].discard(listener)

    def get(self, role):
        field = (
            NUMBERS[role][0]
            if role in NUMBERS
            else SELECTS[role][0]
            if role in SELECTS
            else role
        )
        return self.values.get(field)

    def validate(self, role, value):
        if role in NUMBERS:
            _, _, minimum, maximum, _, _ = NUMBERS[role]
            if isinstance(value, bool):
                raise ValueError("Number required")
            value = float(Fraction(value))
            if not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError("Number outside supported range")
            if role == "soll_soc_speicher" and not value.is_integer():
                raise ValueError("Target SoC must be a whole percent")
            if role.startswith("allowed_current_"):
                from .grid_point import capabilities

                if not value.is_integer():
                    raise ValueError("Allowed current must be whole amperes")
                if self.state is None:
                    raise ValueError("Station capabilities unavailable")
                caps = capabilities(self.state)
                mode = int(role[-2])
                if mode not in caps["modes"]:
                    raise ValueError("Unsupported phase mode")
                maximum = (
                    caps["maximum"]
                    if mode == 3
                    else min(caps["maximum"], caps["asymmetry"])
                )
                if value != 0 and not caps["minimum"] <= value <= maximum:
                    raise ValueError("Allowed current outside station range")
                value = int(value)
            settings = {**self.values, NUMBERS[role][0]: value}
            if role in (
                "soll_soc_speicher",
                "soc_hysterese",
                "optimum_lower_soc",
                "optimum_upper_soc",
            ):
                reserve = self.hass.states.get(
                    self.entry.options.get("min_soc_speicher", "")
                )
                raw = (
                    float(reserve.state)
                    if reserve and reserve.state not in ("unknown", "unavailable")
                    else 0
                )
                lower, upper = (
                    raw + settings["soc_hysterese"],
                    100 - settings["soc_hysterese"],
                )
                if (
                    not lower
                    <= settings["optimum_lower_soc"]
                    <= settings["optimum_upper_soc"]
                    <= upper
                    or not lower <= settings["soll_soc_speicher"] <= upper
                ):
                    raise ValueError("Inconsistent reserve, hysteresis or SoC bounds")
        elif role in SELECTS:
            if value not in SELECTS[role][1]:
                raise ValueError("Unsupported selection")
        elif role in TIMES:
            value = duration_seconds(value)
        else:
            raise ValueError("Unknown setting")
        return value

    async def set(self, role, value):
        async with self.lock:
            await self._set(role, value)

    async def _set(self, role, value):
        value = self.validate(role, value)
        field = (
            NUMBERS[role][0]
            if role in NUMBERS
            else SELECTS[role][0]
            if role in SELECTS
            else role
        )
        if self.values.get(field) == value:
            return
        updated = {**self.values, field: value}
        if role in ("soll_power", *TIMES):
            updated["grid_draft_revision"] = self.values["grid_draft_revision"] + 1
        if role == "charging_profile":
            updated["grid_request"] = None
        equivalent = role in ("soll_power", *TIMES) and self.equivalent_grid_draft(
            updated
        )
        if equivalent:
            self.approve_values(updated)
        await self._save(updated)
        if role in REGULATION:
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, role: value}
            )
        if role in ("soll_power", *TIMES):
            self.approval_error = None
            if equivalent:
                self.notify("grid_equivalent")
            self.notify_grid()
        else:
            self.notify(role)

    async def persist(self, profiles):
        # HA Store logs and swallows WriteError. Its shutdown-deferred data and
        # cached loads cannot establish that a new approval reached disk.
        if self.hass.state is CoreState.stopping:
            raise OSError("Cannot approve settings while Home Assistant is stopping")
        await self.store.async_save(profiles)

        def verify():
            with open(self.store.path, encoding="utf-8") as handle:
                stored = json.load(handle)
            if stored.get("key") != self.store.key or stored.get("data") != profiles:
                raise OSError("Profile settings were not durably stored")

        await self.hass.async_add_executor_job(verify)

    async def _save(self, updated):
        # Keep drafts, approval and foreign profile data in one atomic document.
        profiles = {
            **self.profiles,
            self.key: {
                **self.profiles.get(self.key, {}),
                **{k: v for k, v in updated.items() if k not in REGULATION},
            },
        }
        await self.persist(profiles)
        self.profiles, self.values = profiles, updated

    async def arm_grid(self, now):
        """Capture and consume approved one-shot timing under the SET/edit lock."""
        async with self.lock:
            if self.values.get("grid_request"):
                return
            delay, duration = (self.values["grid_next_timing"].get(k) for k in TIMES)
            if delay is None and duration is None:
                return
            start = now + (delay or 0)
            await self._save(
                {
                    **self.values,
                    "grid_next_timing": dict.fromkeys(TIMES),
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

    async def save_runtime(self, fields):
        """Persist scheduling state without modifying approved/draft configuration."""
        async with self.lock:
            await self._save({**self.values, **fields})
            for role in fields:
                self.notify(role)
