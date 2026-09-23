"""Explicit, station-scoped authority takeover; never a return-control action."""

from homeassistant.components.button import ButtonEntity

from .authority_entity import setup_authority_entities
from .entity import StationEntity


async def async_setup_entry(hass, entry, async_add_entities):
    setup_authority_entities(
        hass,
        entry,
        async_add_entities,
        "take_control",
        lambda station: TakeControlButton(
            entry.runtime_data.control, entry.entry_id, station
        ),
    )


class TakeControlButton(StationEntity, ButtonEntity):
    _attr_entity_category = None

    def __init__(self, control, entry_id, station):
        super().__init__(control.runtime, entry_id, station, "take_control")
        self.control = control

    @property
    def available(self):
        adapter = self.control.authority_adapter(self.station)
        return bool(
            self.snapshot
            and self.snapshot.connected
            and adapter
            and adapter.can_take_control()
        )

    @property
    def extra_state_attributes(self):
        result = self.control.takeover_results.get(self.station)
        return {
            **super().extra_state_attributes,
            "takeover_status": result.status.value if result else None,
            "takeover_reason": result.reason.value
            if result and result.reason
            else None,
        }

    async def async_press(self):
        await self.control.take_control(self.station)
        self.async_write_ha_state()
