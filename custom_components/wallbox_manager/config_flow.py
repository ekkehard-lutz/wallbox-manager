"""One explicitly bound wallbox per entry; no subentry or ownership workflow."""

import ipaddress

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector
from homeassistant.helpers.storage import Store

from .const import DEFAULT_HOST, DEFAULT_PORT, DOMAIN
from .migration import target
from .settings import NUMBERS, REGULATION


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


SOURCES = (
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


REGULATION_FIELDS = {key: NUMBERS[key][1:] for key in REGULATION} | {
    "power_smoothing_window": (5, 0, 300, 1, "s"),
}


def diagnostic_level(options):
    """Explicit 0–3 wins; preserve beta.18's legacy boolean fallback."""
    try:
        return max(
            0,
            min(
                3,
                int(
                    options.get(
                        "diagnostic_level",
                        3 if options.get("pv_diagnostic_logging") else 0,
                    )
                ),
            ),
        )
    except ValueError, TypeError:
        return 0


class Options(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        options = dict(self.config_entry.options)
        runtime = getattr(self.config_entry, "runtime_data", None)
        # Central options retain precedence over the existing profile settings.
        if runtime:
            values = runtime.settings.values
        else:
            profiles = (
                await Store(
                    self.hass,
                    1,
                    f"wallbox_manager.{self.config_entry.entry_id}.profiles",
                ).async_load()
                or {}
            )
            values = {}
            for profile in reversed(sorted(profiles)):
                values.update(profiles[profile])
        general = {
            vol.Optional(
                "diagnostic_level", default=str(diagnostic_level(options))
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["0", "1", "2", "3"],
                    mode="dropdown",
                    translation_key="diagnostic_level",
                )
            ),
        }
        for key in SOURCES:
            domains = (
                ["number", "input_number"]
                if key == "min_soc_speicher"
                else None
                if key == "soc_speicher_aktuell"
                else ["sensor"]
            )
            general[
                vol.Optional(key, description={"suggested_value": options.get(key)})
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    **({"domain": domains} if domains else {})
                )
            )
        regulation = {}
        for key, (default, minimum, maximum, step, unit) in REGULATION_FIELDS.items():
            regulation[
                vol.Required(key, default=options.get(key, values.get(key, default)))
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=minimum,
                    max=maximum,
                    step=step,
                    mode="box",
                    unit_of_measurement=unit,
                )
            )
        errors = {}
        if user_input is not None:
            data = {**user_input.get("general", {}), **user_input.get("regulation", {})}
            # Support callers of the previous flat form as well.
            data.update(
                {
                    k: v
                    for k, v in user_input.items()
                    if k not in ("general", "regulation")
                }
            )
            try:
                if "diagnostic_level" in data:
                    level = int(data["diagnostic_level"])
                    if str(level) not in ("0", "1", "2", "3"):
                        raise ValueError("Invalid level")
                    options["diagnostic_level"] = level
                # Absent fields are preserved; an explicit empty selection clears.
                for key in SOURCES:
                    if key in data:
                        if data[key]:
                            options[key] = data[key]
                        else:
                            options.pop(key, None)
                for key in REGULATION_FIELDS:
                    if key in data:
                        if runtime and key in NUMBERS:
                            runtime.settings.validate(key, data[key])
                        options[key] = regulation[
                            next(
                                marker for marker in regulation if marker.schema == key
                            )
                        ](data[key])
                return self.async_create_entry(title="", data=options)
            except ValueError, TypeError, vol.Invalid:
                errors["base"] = "invalid_settings"
        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("general"): section(vol.Schema(general)),
                    vol.Required("regulation"): section(vol.Schema(regulation)),
                }
            ),
        )
