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
    VERSION = 3

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ReferenceOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry):
        return {"wallbox": WallboxCapabilityFlow}

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


def migrate_options(options):
    """Preserve explicit legacy topology; quarantine ambiguous references."""
    data = dict(options)
    legacy = {key: data.pop(key) for key in REFERENCE_FIELDS if key in data}
    if legacy:
        try:
            validate_reference_options(legacy)
        except ValueError, TypeError, ZeroDivisionError, OverflowError:
            data["unassigned_references"] = legacy
        else:
            stations = {
                k: dict(v) for k, v in data.get("station_references", {}).items()
            }
            station = stations.setdefault(legacy["reference_station_id"], {})
            key = f"{legacy['reference_evse_id']}:{legacy['reference_connector_id']}"
            station.setdefault(key, legacy)
            data["station_references"] = stations
    return data


class ReferenceOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input=None):
        from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

        self.data = migrate_options(self.config_entry.options)
        fields = {
            vol.Optional(
                "min_soc_speicher",
                description={"suggested_value": self.data.get("min_soc_speicher")},
            ): EntitySelector(EntitySelectorConfig(domain=["number", "input_number"])),
            vol.Optional(
                "soc_speicher_aktuell",
                description={"suggested_value": self.data.get("soc_speicher_aktuell")},
            ): EntitySelector(EntitySelectorConfig()),
        }
        if user_input is not None:
            for key in ("min_soc_speicher", "soc_speicher_aktuell"):
                self.data.pop(key, None)
                if user_input.get(key):
                    self.data[key] = user_input[key]
            return self.async_create_entry(title="", data=self.data)
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))


class WallboxCapabilityFlow(config_entries.ConfigSubentryFlow):
    """Reconfigure the already scoped wallbox, without a central station picker."""

    async def async_step_user(self, user_input=None):
        return self.async_abort(reason="discovered_automatically")

    async def async_step_reconfigure(self, user_input=None):
        from .core.models import ConnectorId, EvseId, StationId

        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        identity = subentry.data
        target = ConnectorId(
            EvseId(StationId(identity["station"]), identity["evse"]),
            identity["connector"],
        )
        from .control.reference import FIELDS
        from .core.capabilities import EvidenceState

        runtime = getattr(entry, "runtime_data", None)
        state = runtime.state.get(target.station) if runtime else None
        known = (
            {
                c.key
                for c in state.electrical
                if c.scope in (target, target.evse)
                and c.evidence.state
                in (
                    EvidenceState.VERIFIED,
                    EvidenceState.UNSUPPORTED,
                    EvidenceState.DEGRADED,
                )
            }
            if state and state.connected
            else set()
        )
        fields = {
            key: value
            for key, value in REFERENCE_FIELDS.items()
            if key in FIELDS and FIELDS[key] not in known
        }
        # Maxima for an explicitly unsupported phase count are irrelevant.
        counts = (
            next(
                (
                    c.value
                    for c in state.electrical
                    if c.scope == target.evse
                    and c.key == "supported_phases"
                    and c.evidence.state == EvidenceState.VERIFIED
                ),
                None,
            )
            if state
            else None
        )
        if counts:
            fields = {
                k: v
                for k, v in fields.items()
                if not k.startswith("reference_max_") or int(k[-2]) in counts
            }
        saved = subentry.data.get("references", {})
        errors = {}
        if user_input is not None:
            try:
                data = validate_reference_options(
                    {
                        **user_input,
                        "reference_station_id": target.station.value,
                        "reference_evse_id": int(target.evse.value),
                        "reference_connector_id": int(target.value),
                    }
                )
                if set(user_input) - set(fields):
                    raise ValueError("OCPP capability is authoritative")
            except ValueError, TypeError, ZeroDivisionError, OverflowError:
                errors["base"] = "invalid_reference"
            else:
                return self.async_update_and_abort(
                    entry, subentry, data={**identity, "references": data}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Optional(k, description={"suggested_value": saved.get(k)}): v
                    for k, v in fields.items()
                }
            ),
            errors=errors,
        )
