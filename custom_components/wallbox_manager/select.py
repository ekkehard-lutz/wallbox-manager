"""Persistent profile and approximation preferences for guarded NETZ execution."""

from fractions import Fraction

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import SettingEntity
from .settings import NUMBERS, SELECTS


async def async_setup_entry(hass, entry, add):
    add([SettingSelect(entry, role) for role in SELECTS])


class SettingSelect(SettingEntity, SelectEntity, RestoreEntity):
    def __init__(self, entry, role):
        super().__init__(entry, role)
        self._attr_options = SELECTS[role][1]

    @property
    def current_option(self):
        value = self.settings.get(self.role)
        return value if value in self.options else None

    def surplus_target(self):
        """Read-only PV-SOC-01 validity envelope, not a planned/clamped target.

        HA owns the live reserve. The card can test it against this ceiling
        without republishing backend state on every reserve observation.
        """
        try:
            target = Fraction(str(self.settings.get("soll_soc_speicher")))
            hysteresis = Fraction(str(self.settings.get("soc_hysterese")))
            if (
                not NUMBERS["soll_soc_speicher"][2]
                <= target
                <= NUMBERS["soll_soc_speicher"][3]
                or target.denominator != 1
                or not NUMBERS["soc_hysterese"][2]
                <= hysteresis
                <= NUMBERS["soc_hysterese"][3]
                or not hysteresis <= target <= 100 - hysteresis
            ):
                return None
            return {"soc": float(target), "maximum_reserve": float(target - hysteresis)}
        except ValueError, TypeError, ZeroDivisionError, OverflowError:
            return None

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        if self.role == "charging_profile":
            # Read-only mapping and configured-target metadata. HA owns observations;
            # OCPP lifecycle and requested reserve writes do not invalidate them.
            attrs.update(
                battery_soc_entity_id=self.entry.options.get("soc_speicher_aktuell"),
                battery_reserve_entity_id=self.entry.options.get("min_soc_speicher"),
                pv_surplus_target=self.surplus_target(),
            )
        return attrs

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if self.role == "charging_profile":
            for role in ("soll_soc_speicher", "soc_hysterese"):
                self.async_on_remove(self.settings.watch(role, self.changed))

            def mappings():
                return tuple(
                    self.entry.options.get(key)
                    for key in ("soc_speicher_aktuell", "min_soc_speicher")
                )

            previous = mappings()

            async def options_updated(hass, entry):
                nonlocal previous
                current = mappings()
                if current != previous:
                    previous = current
                    self.changed()

            self.async_on_remove(self.entry.add_update_listener(options_updated))
        if (
            self.role == "power_approximation"
            and "direction" not in self.settings.profiles.get(self.settings.key, {})
        ):
            previous = await self.async_get_last_state()
            if previous and previous.state in self.options:
                self.settings.values["direction"] = previous.state

    async def async_select_option(self, option):
        await self.settings.set(self.role, option)
