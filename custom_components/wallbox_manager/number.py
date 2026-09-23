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
        lambda c, e, t: [
            ControlNumber(c, e, t, key)
            for key in ("desired_charging_power", "phase_switch_deviation_pct")
        ],
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

    @property
    def native_value(self):
        return float(
            self.intent.request.target_w
            if self.field == "target_w"
            else self.intent.phase_switch_deviation_pct
        )

    def _value(self, value):
        value = scalar(value)
        if value > self.native_max_value:
            raise ValueError("value exceeds intent range")
        return value

    def restore_value(self, value):
        self.control.restore(self.target, **{self.field: self._value(Fraction(value))})

    async def async_set_native_value(self, value):
        await self.control.change(self.target, **{self.field: self._value(value)})
