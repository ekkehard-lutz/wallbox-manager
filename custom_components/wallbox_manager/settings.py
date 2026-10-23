"""Validated local preferences in the existing, losslessly preserved profile store."""

import asyncio
import json
import math
import re
from fractions import Fraction

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
        if role == "charging_profile":
            updated["grid_request"] = None
        # Preserve foreign targets/fields verbatim; beta.18's own loader remains usable.
        profiles = {
            **self.profiles,
            self.key: {
                **self.profiles.get(self.key, {}),
                **{k: v for k, v in updated.items() if k not in REGULATION},
            },
        }
        await self.store.async_save(profiles)
        if role in REGULATION:
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, role: value}
            )
        self.profiles, self.values = profiles, updated
        for listener in tuple(self.listeners.get(role, ())):
            listener()

    async def save_runtime(self, fields):
        """Persist scheduling state in the canonical profile document."""
        async with self.lock:
            updated = {**self.values, **fields}
            profiles = {
                **self.profiles,
                self.key: {
                    **self.profiles.get(self.key, {}),
                    **{k: v for k, v in updated.items() if k not in REGULATION},
                },
            }
            await self.store.async_save(profiles)
            self.profiles, self.values = profiles, updated
            for role in fields:
                for listener in tuple(self.listeners.get(role, ())):
                    listener()
