"""Minimal CSMS listener configuration.

Host/port schema and duplicate-listener checks adapted from lbbrhzn/ocpp
config_flow.py at 848407c11ff659ce59779a99ce69984bbb0e3ce1.
Copyright (c) 2021 lbbrhzn, MIT; see THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import ipaddress
from fractions import Fraction
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
    "reference_station_id": str,
    "reference_evse_id": vol.Coerce(int),
    "reference_connector_id": vol.Coerce(int),
    "reference_phases": str,
    "reference_modes": str,
    "reference_min_a": str,
    "reference_step_a": str,
    "reference_max_1a": str,
    "reference_max_2a": str,
    "reference_max_3a": str,
    "reference_phase_switching": bool,
    "reference_enable_disable": bool,
}


def validate_reference_options(data):
    from .control.reference import ConfiguredReference
    from .core.electrical import phase_modes, validate_value

    if not data:
        return {}
    if set(data) - set(REFERENCE_FIELDS):
        raise ValueError("unknown reference option")
    station = data.get("reference_station_id")
    if (
        not isinstance(station, str)
        or not station.strip()
        or station != station.strip()
    ):
        raise ValueError("explicit station association required")
    for key in ("reference_evse_id", "reference_connector_id"):
        if type(data.get(key)) is not int or data[key] <= 0:
            raise ValueError("explicit positive topology identity required")
    counts = (
        validate_value(
            "supported_phases",
            tuple(int(n.strip()) for n in data["reference_phases"].split(",")),
        )
        if "reference_phases" in data
        else None
    )
    minimum = (
        scalar(Fraction(data["reference_min_a"]), positive=True)
        if "reference_min_a" in data
        else None
    )
    if "reference_step_a" in data:
        scalar(Fraction(data["reference_step_a"]), positive=True)
    for count in (1, 2, 3):
        key = f"reference_max_{count}a"
        if key in data:
            maximum = scalar(Fraction(str(data[key])), positive=True)
            if minimum is not None and maximum < minimum:
                raise ValueError("maximum below minimum")
            if counts is not None and count not in counts:
                raise ValueError("maximum for explicitly unsupported mode")
    if "reference_modes" in data:
        modes = phase_modes(data["reference_modes"])
        if counts is not None and any(mode.count not in counts for mode in modes):
            raise ValueError("mapping contradicts phase counts")
        if len({m.count for m in modes}) != len(modes):
            raise ValueError("count-only execution needs an unambiguous mapping")
    ConfiguredReference(data)
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
            except ValueError, TypeError, ZeroDivisionError, OverflowError, vol.Invalid:
                errors["base"] = "invalid_reference"
            else:
                return self.async_create_entry(title="", data=data)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
