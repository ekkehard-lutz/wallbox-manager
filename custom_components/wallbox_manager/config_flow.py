"""Minimal CSMS listener configuration.

Host/port schema and duplicate-listener checks adapted from lbbrhzn/ocpp
config_flow.py at 848407c11ff659ce59779a99ce69984bbb0e3ce1.
Copyright (c) 2021 lbbrhzn, MIT; see THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import ipaddress
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import DEFAULT_HOST, DEFAULT_PORT, DOMAIN
from .core.values import scalar


def bind_address(value: str) -> str:
    """Use an explicit local IP address, including IPv6 or a wildcard."""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise vol.Invalid("invalid bind address") from exc


LISTENER_SCHEMA = vol.Schema(
    {
        # Keep arbitrary Python validators out of the frontend form schema.
        vol.Required("host", default=DEFAULT_HOST): str,
        vol.Required("port", default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
    }
)


class WallboxManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ReferenceOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors = {}
        if user_input is not None:
            try:
                data = LISTENER_SCHEMA(user_input)
                data["host"] = bind_address(data["host"])
            except vol.Invalid:
                errors["base"] = "invalid_listener"
            else:
                self._async_abort_entries_match({"port": data["port"]})
                return self.async_create_entry(title="Wallbox Manager", data=data)
        return self.async_show_form(
            step_id="user", data_schema=LISTENER_SCHEMA, errors=errors
        )


REFERENCE_FIELDS = {
    "reference_verified": bool,
    "reference_station_id": str,
    "reference_firmware": str,
    "reference_vendor": str,
    "reference_model": str,
    "reference_serial": str,
    "reference_min_a": vol.Coerce(float),
    "reference_max_1a": vol.Coerce(float),
    "reference_max_3a": vol.Coerce(float),
    "limit_1a": vol.Coerce(float),
    "limit_3a": vol.Coerce(float),
}


def validate_reference_options(data):
    if data.get("reference_verified") is not True:
        return {}
    if any(key not in data for key in REFERENCE_FIELDS if key != "reference_serial"):
        raise ValueError("complete reference verification required")
    for key in (
        "reference_station_id",
        "reference_firmware",
        "reference_vendor",
        "reference_model",
        *(["reference_serial"] if "reference_serial" in data else []),
    ):
        if not data[key].strip() or data[key] != data[key].strip():
            raise ValueError("nonempty reference identity required")
    from .core.models import StationId

    StationId(data["reference_station_id"])
    for key in ("reference_min_a", "reference_max_1a", "reference_max_3a"):
        value = scalar(data[key], positive=True)
        if value.denominator != 1:
            raise ValueError("reference device has a whole ampere grid")
    for count in (1, 3):
        if data[f"reference_max_{count}a"] < data["reference_min_a"]:
            raise ValueError("invalid reference interval")
        scalar(data[f"limit_{count}a"])
    return data


class ReferenceOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input=None):
        errors = {}
        schema = vol.Schema(
            {
                vol.Optional(
                    key,
                    description={"suggested_value": self.config_entry.options.get(key)},
                ): value
                for key, value in REFERENCE_FIELDS.items()
            }
        )
        if user_input is not None:
            try:
                data = validate_reference_options(schema(user_input))
            except ValueError, vol.Invalid:
                errors["base"] = "invalid_reference"
            else:
                return self.async_create_entry(title="", data=data)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
