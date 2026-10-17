"""One explicitly bound wallbox per entry; no subentry or ownership workflow."""

import ipaddress

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import DEFAULT_HOST, DEFAULT_PORT, DOMAIN
from .migration import target


class WallboxManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 3

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return Options()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = dict(user_input)
                data["host"] = str(ipaddress.ip_address(data["host"]))
                data["port"] = int(data["port"])
                if not 1 <= data["port"] <= 65535:
                    raise ValueError("Invalid port")
                station, evse, connector = target(
                    (data["station_id"], data["evse_id"], data["connector_id"])
                )
                data.update(station_id=station, evse_id=evse, connector_id=connector)
                self._async_abort_entries_match({"port": data["port"]})
                return self.async_create_entry(title=station, data=data)
            except ValueError, TypeError:
                errors["base"] = "invalid_listener"
        return self.async_show_form(
            step_id="user",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("host", default=DEFAULT_HOST): str,
                    vol.Required("port", default=DEFAULT_PORT): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=65535)
                    ),
                    vol.Required("station_id"): str,
                    vol.Required("evse_id", default="1"): str,
                    vol.Required("connector_id", default="1"): str,
                }
            ),
        )


class Options(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        # Source mappings remain editable for later regulation; no HA source
        # subscription or polling is necessary while only storing preferences.
        keys = (
            "min_soc_speicher",
            "soc_speicher_aktuell",
            "leistung_pv",
            "leistung_verbraucher",
            "storage_discharge_power",
            "storage_capacity",
            "remaining_pv_energy",
            "grid_import_power",
            "grid_export_power",
        )
        if user_input is not None:
            options = dict(self.config_entry.options)
            for key in keys:
                options.pop(key, None)
                if user_input.get(key):
                    options[key] = user_input[key]
            return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        key,
                        description={
                            "suggested_value": self.config_entry.options.get(key, "")
                        },
                    ): str
                    for key in keys
                }
            ),
        )
