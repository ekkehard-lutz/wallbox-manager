"""Numeric preferences; eligible NETZ changes reconcile a fixed offered point."""

import math
from fractions import Fraction

import voluptuous as vol
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import SettingEntity
from .grid_point import capabilities, envelope, rounded_kw
from .settings import NUMBERS


async def async_setup_entry(hass, entry, add):
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "step_power",
        {
            vol.Required("direction"): vol.In([-1, 1]),
            vol.Required("revision"): vol.All(int, vol.Range(min=0)),
            vol.Required("incarnation"): str,
        },
        "async_step_power",
    )
    state = entry.runtime_data.state
    seen = set()

    def discover():
        try:
            modes = capabilities(state)["modes"]
        except ValueError, TypeError:
            modes = ()
        roles = [
            role
            for role in NUMBERS
            if not role.startswith("allowed_current_") or int(role[-2]) in modes
        ]
        new = [role for role in roles if role not in seen]
        seen.update(new)
        add([SettingNumber(entry, role) for role in new])

    discover()
    station, evse, _ = state.binding
    entry.async_on_unload(
        state.watch(("capability", (station, evse, None), "supported_phases"), discover)
    )


class SettingNumber(SettingEntity, NumberEntity, RestoreEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, entry, role):
        super().__init__(entry, role)
        _, _, minimum, maximum, step, unit = NUMBERS[role]
        self._attr_native_min_value, self._attr_native_max_value = minimum, maximum
        self._attr_native_step, self._attr_native_unit_of_measurement = step, unit
        if role.startswith("allowed_current_") or role in (
            "soc_hysterese",
            "regulation_interval",
            "pv_start_delay",
            "pv_stop_delay",
        ):
            self._attr_entity_category = EntityCategory.CONFIG
        if role in (
            "soc_hysterese",
            "regulation_interval",
            "pv_start_delay",
            "pv_stop_delay",
        ):
            self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self):
        value = self.settings.get(self.role)
        if self.role == "soll_power":
            try:
                bounds = self.power_bounds()
                value = rounded_kw(value)
                return value if bounds and bounds[0] <= value <= bounds[1] else None
            except ValueError, TypeError, OverflowError:
                return None
        if self.role.startswith("allowed_current_"):
            try:
                ranges = envelope(self.observed, self.settings)
                mode = int(self.role[-2])
                return int(ranges.ranges[mode][3]) if mode in ranges.ranges else 0
            except ValueError, TypeError:
                return None
        try:
            number = float(Fraction(value)) if value is not None else None
            return number if number is not None and math.isfinite(number) else None
        except ValueError, TypeError, ZeroDivisionError, OverflowError:
            return None

    @property
    def available(self):
        if self.role == "soll_power":
            return self.power_bounds() is not None and self.native_value is not None
        if self.role.startswith("allowed_current_"):
            try:
                return int(self.role[-2]) in capabilities(self.observed)["modes"]
            except ValueError, TypeError:
                return False
        return True

    def power_bounds(self):
        try:
            return envelope(self.observed, self.settings).power_bounds()
        except ValueError, TypeError:
            return None

    @property
    def native_min_value(self):
        if self.role == "soll_power":
            bounds = self.power_bounds()
            return bounds[0] if bounds else 0
        return self._attr_native_min_value

    @property
    def native_max_value(self):
        if self.role == "soll_power":
            bounds = self.power_bounds()
            return bounds[1] if bounds else 0

        if self.role.startswith("allowed_current_"):
            try:
                caps = capabilities(self.observed)
                return int(
                    caps["maximum"]
                    if int(self.role[-2]) == 3
                    else min(caps["maximum"], caps["asymmetry"])
                )
            except ValueError, TypeError:
                return 0
        return self._attr_native_max_value

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        if self.role.startswith("allowed_current_"):
            try:
                attrs["legacy_limit_migration"] = envelope(
                    self.observed, self.settings
                ).migration.get(int(self.role[-2]))
            except ValueError, TypeError:
                attrs["legacy_limit_migration"] = None
            attrs["exact_current_limit_a"] = (
                str(self.settings.get(self.role))
                if self.native_value is not None
                else None
            )
        if self.role == "soll_power":
            bounds = self.power_bounds()
            attrs.update(self.settings.power_attributes())
            attrs.update(
                technical_min_kw=bounds[0] if bounds else None,
                technical_max_kw=bounds[1] if bounds else None,
            )
        return attrs

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if self.role == "soll_power":
            for mode in (1, 2, 3):
                self.async_on_remove(
                    self.settings.watch(f"allowed_current_{mode}p", self.changed)
                )
        if self.role.startswith("allowed_current_") or self.role == "soll_power":
            station, evse, _ = self.observed.binding
            for scope, roles in (
                (
                    (station, evse, None),
                    ("supported_phases", "minimum_current", "current_step"),
                ),
                (
                    self.observed.binding,
                    ("maximum_current", "maximum_asymmetry_current"),
                ),
            ):
                for role in roles:
                    self.async_on_remove(
                        self.observed.watch(("capability", scope, role), self.changed)
                    )
        if self.role in (
            "desired_charging_power",
            "allowed_current_1p",
            "allowed_current_2p",
            "allowed_current_3p",
        ):
            previous = await self.async_get_last_state()
            if (
                previous
                and previous.state not in ("unknown", "unavailable")
                and NUMBERS[self.role][0]
                not in self.settings.profiles.get(self.settings.key, {})
            ):
                try:
                    if self.role.startswith("allowed_current_"):
                        self.settings.values[NUMBERS[self.role][0]] = (
                            previous.attributes.get("exact_current_limit_a")
                            or previous.state
                        )
                        return
                    value = self.settings.validate(
                        self.role,
                        previous.attributes.get("exact_current_limit_a")
                        or previous.state,
                    )
                    self.settings.values[NUMBERS[self.role][0]] = (
                        str(Fraction(previous.attributes["exact_current_limit_a"]))
                        if self.role.startswith("allowed_current_")
                        and previous.attributes.get("exact_current_limit_a")
                        else value
                    )
                except ValueError, TypeError:
                    pass

    async def async_set_native_value(self, value):
        await self.settings.set(self.role, value)

    async def async_step_power(self, direction, revision, incarnation):
        if incarnation != self.observed.runtime_id:
            raise HomeAssistantError("Superseded NETZ runtime")
        if self.role != "soll_power":
            raise HomeAssistantError(
                "Technical steps apply only to NETZ requested power"
            )
        try:
            await self.settings.step_power(direction, revision)
        except (ValueError, TypeError, OSError) as exc:
            raise HomeAssistantError(str(exc)) from exc
