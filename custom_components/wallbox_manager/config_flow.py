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
from homeassistant.data_entry_flow import FlowResult

from .const import DEFAULT_HOST, DEFAULT_PORT, DOMAIN


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
