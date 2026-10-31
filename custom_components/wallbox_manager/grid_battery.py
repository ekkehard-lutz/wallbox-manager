"""HA event subscriptions for NETZ reserve, independent of electrical dispatch."""

from datetime import UTC, datetime

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event

from .battery import BatteryReserve
from .session import view


class GridBattery:
    def __init__(self, hass, entry, grid):
        self.hass, self.entry, self.grid = hass, entry, grid
        self.reserve = BatteryReserve(hass, entry)
        self.task = None
        self.preview = None
        self.dirty = self.closed = False
        self.removers = []
        self.input_remove = lambda: None

    async def start(self):
        await self.reserve.load(preserve=True)
        state = self.grid.state
        station, evse, _ = state.binding
        self.subscribe_inputs()
        for key in (
            ("observation", (station, evse, None), "power"),
            ("observation", state.binding, "charging_state"),
            ("observation", state.binding, "connector_state"),
            ("session", state.binding, "power"),
            ("session", state.binding, "active"),
            ("station", (station, None, None), "control_authority"),
            ("control", state.binding, "charging_enabled"),
            ("grid", state.binding, "soll_power"),
        ):
            self.removers.append(state.watch(key, self.changed))
        for role in ("min_soc", "charging_profile", "charge_release"):
            self.removers.append(self.grid.settings.watch(role, self.changed))
        self.changed()

    def subscribe_inputs(self):
        self.input_remove()
        entities = {
            self.reserve.reserve,
            self.reserve.soc,
            self.entry.options.get("min_soc_speicher"),
            self.entry.options.get("soc_speicher_aktuell"),
        }
        if self.reserve.record:
            entities.add(self.reserve.record["entity"])
        self.input_remove = async_track_state_change_event(
            self.hass, list(entities - {None, ""}), self.input_changed
        )

    @callback
    def input_changed(self, event):
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if (
            self.reserve.failed_restore
            and (old is None or old.state in ("unknown", "unavailable"))
            and new
        ):
            self.reserve.failed_restore = False
        self.changed()

    def publish_preview(self):
        preview = (
            self.reserve.preview(self.grid.settings.get("min_soc"))
            if self.grid.settings.get("charging_profile") == "NETZ"
            else None
        )
        if preview != self.preview:
            self.preview = preview
            self.grid.state.publish(("battery", self.grid.state.binding, "preview"))

    def changed(self):
        if self.closed:
            return
        self.publish_preview()
        self.dirty = True
        if self.task is None or self.task.done():
            self.task = self.hass.async_create_task(
                self.update(), "NETZ battery reserve"
            )

    async def update(self):
        while self.dirty and not self.closed:
            self.dirty = False
            desired_mapping = (
                self.entry.options.get("min_soc_speicher"),
                self.entry.options.get("soc_speicher_aktuell"),
            )
            if desired_mapping != (self.reserve.reserve, self.reserve.soc):
                self.reserve.recovering = False
                await self.reserve.update(None)
                self.subscribe_inputs()
                if self.reserve.record:
                    continue
                self.reserve.reserve, self.reserve.soc = desired_mapping
                self.reserve.configured = all(desired_mapping)
                self.reserve.session = False
                self.subscribe_inputs()
            state = self.grid.state
            authority = state.value(self.grid.listener.control.key("ControlAuthority"))
            permission = state.value(self.grid.listener.control.key("ChargingEnabled"))
            power = view(state, datetime.now(UTC))["power"]
            point = self.grid.confirmed
            charging = (
                self.grid.eligible()
                and (self.grid.settings.get("charge_release") or self.reserve.record)
                and self.grid.settings.get("charging_profile") == "NETZ"
                and permission is True
                and point
                and point.amps > 0
                and getattr(self.grid, "point_generation", None) == state.generation
                and self.grid.confirmed_transaction == self.grid.transaction()
                and state.charging_value(state.binding) == "charging"
                and power is not None
                and power > 0
            )
            # Only authoritative current-generation termination/absence releases
            # startup recovery. A missing session or a charging pause proves neither.
            session = state.session
            session_over = state.current(state.registered_generation) and (
                state.value(("observation", state.binding, "connector_state"))
                == "available"
                or (
                    session is not None
                    and session.get("generation") == list(state.generation)
                    and tuple(session["scope"]) == state.binding
                    and session["ended_at"] is not None
                )
            )
            # Startup ambiguity holds the journal; it grants no new write authority.
            if self.reserve.recovering:
                if (
                    charging
                    or session_over
                    or authority == "local"
                    or permission is False
                    or self.grid.settings.get("charging_profile") != "NETZ"
                ):
                    self.reserve.resume()
                else:
                    continue
            await self.reserve.update(
                self.grid.settings.get("min_soc") if charging else None
            )
            self.publish_preview()
            messages = state.messages
            if self.reserve.status == "error" or (
                self.reserve.status == "write_unconfirmed"
                and self.reserve.diagnostics.get("reason") == "confirmation_timeout"
            ):
                messages.set(
                    "battery:reserve",
                    source="battery",
                    severity="error",
                    code="battery_reserve",
                    detail=self.reserve.last_error or "confirmation_timeout",
                )
            else:
                messages.clear("battery:reserve")

    async def close(self):
        self.closed = True
        self.input_remove()
        for remove in self.removers:
            remove()
        if self.task and not self.task.done():
            self.task.cancel()
            import asyncio

            await asyncio.gather(self.task, return_exceptions=True)
        # Deliberate HA reload/shutdown preserves the durable journal. Startup
        # reconciles held authority/session evidence before adopting or restoring.
