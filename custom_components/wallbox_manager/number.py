"""User power intent and a phase-retention preference, never device capabilities."""

from fractions import Fraction

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.entity import EntityCategory

from .control_entity import ControlEntity, setup_control_entities
from .core.values import scalar


async def async_setup_entry(hass, entry, async_add_entities):
    setup_control_entities(
        hass,
        entry,
        async_add_entities,
        lambda c, e, t: (
            [
                ControlNumber(c, e, t, key)
                for key in (
                    "desired_charging_power",
                    "phase_switch_deviation_pct",
                    "allowed_current_1p",
                    "allowed_current_2p",
                    "allowed_current_3p",
                )
            ]
            + (
                [
                    ProfileNumber(c, e, t, "soll_power"),
                    ProfileNumber(c, e, t, "min_soc"),
                    *[
                        ProfileNumber(c, e, t, key)
                        for key in (
                            "soll_soc_speicher",
                            "soc_hysterese",
                            "regulation_interval",
                        )
                    ],
                ]
                if hasattr(c, "profiles")
                else []
            )
        ),
    )


class ControlNumber(ControlEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_mode = NumberMode.BOX

    def __init__(self, control, entry_id, target, key):
        super().__init__(control, entry_id, target, key)
        power = key == "desired_charging_power"
        self.field = "target_w" if power else "phase_switch_deviation_pct"
        self._attr_native_max_value = 100000 if power else 25
        self._attr_native_step = 100 if power else 1
        self._attr_native_unit_of_measurement = "W" if power else "%"
        self._attr_entity_category = None if power else EntityCategory.CONFIG
        if key.startswith("allowed_current_"):
            self.field = key
            self._attr_native_max_value = 100000
            self._attr_native_step = 0.001
            self._attr_native_unit_of_measurement = "A"

    @property
    def native_value(self):
        if self.field.startswith("allowed_current_"):
            value = self.intent.current_limits.get(int(self.field[-2]))
            return float(value) if value is not None else None
        return float(
            self.intent.request.target_w
            if self.field == "target_w"
            else self.intent.phase_switch_deviation_pct
        )

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        if self.field.startswith("allowed_current_"):
            value = self.intent.current_limits.get(int(self.field[-2]))
            attrs["exact_current_limit_a"] = str(value) if value is not None else None
        return attrs

    def restore_state(self, previous):
        value = previous.state
        if self.field.startswith("allowed_current_"):
            value = previous.attributes.get("exact_current_limit_a") or value
        self.restore_value(value)

    def _value(self, value):
        value = scalar(value)
        if value > self.native_max_value:
            raise ValueError("value exceeds intent range")
        return value

    def restore_value(self, value):
        self.control.restore(self.target, **{self.field: self._value(Fraction(value))})

    async def async_set_native_value(self, value):
        await self.control.change(self.target, **{self.field: self._value(value)})


class ProfileNumber(ControlEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_mode = NumberMode.BOX

    def __init__(self, control, entry_id, target, key):
        super().__init__(control, entry_id, target, key)
        self.field = "power_kw" if key == "soll_power" else key
        self._attr_native_unit_of_measurement = "kW" if key == "soll_power" else "%"
        self._attr_native_step = 0.1 if key == "soll_power" else 1
        if key == "regulation_interval":
            self._attr_native_unit_of_measurement = "s"
            self._attr_native_min_value = 1

    @property
    def available(self):
        if self.field == "min_soc":
            return self.control.profiles.battery.configured
        return self.field in ("power_kw", "regulation_interval") or bool(
            self.control.profiles.references.get("soc_speicher_aktuell")
        )

    @property
    def native_value(self):
        return self.control.profiles.setting(self.target)[self.field]

    @property
    def native_max_value(self):
        if self.field == "power_kw":
            maximum = self.control.power_ceiling(self.target)
            if maximum is not None:
                return float(maximum / 1000)
        # HA requires a numeric input range; this is storage validation only.
        # Consumers must use technical_max_kw, not this fallback, as capability.
        return {"soll_soc_speicher": 99, "regulation_interval": 300}.get(
            self.field, 100
        )

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        if self.field == "power_kw":
            maximum = self.control.power_ceiling(self.target)
            attrs["technical_max_kw"] = (
                float(maximum / 1000) if maximum is not None else None
            )
        return attrs

    def restore_state(self, previous):
        pass

    async def async_set_native_value(self, value):
        await self.control.profiles.set_value(self.target, self.field, value)
