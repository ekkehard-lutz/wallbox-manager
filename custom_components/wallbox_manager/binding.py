"""Validate the explicit station/EVSE/connector binding before setup writes."""

import json

from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .entity import scoped_identity


def target(value):
    station, evse, connector = value
    if (
        not isinstance(station, str)
        or not station
        or len(station) > 128
        or any(ord(c) < 33 or c in "/?#" for c in station)
    ):
        raise ValueError("Invalid station identity")
    if any(
        not str(n).isascii()
        or not str(n).isdecimal()
        or int(n) <= 0
        or str(int(n)) != str(n)
        for n in (evse, connector)
    ):
        raise ValueError("EVSE and connector must be positive canonical integers")
    return station, str(evse), str(connector)


def validate_binding(hass, entry, profiles):
    if entry.subentries:
        raise ConfigEntryError("Wallbox Manager entries cannot have subentries")
    try:
        binding = target(
            tuple(entry.data[key] for key in ("station_id", "evse_id", "connector_id"))
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise ConfigEntryError(
            "A valid explicit station/EVSE/connector binding is required"
        ) from exc

    if not isinstance(profiles, dict):
        raise ConfigEntryError("Invalid scoped profiles")
    for key, profile in profiles.items():
        try:
            scope = json.loads(key)
            if (
                not isinstance(scope, list)
                or target(scope) != binding
                or not isinstance(profile, dict)
            ):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ConfigEntryError(
                "Stored profile does not match the configured binding"
            ) from exc

    registry, devices = er.async_get(hass), dr.async_get(hass)
    prefix = f"{entry.entry_id}:"
    device_identity = (DOMAIN, prefix + binding[0])
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        for domain, identifier in device.identifiers:
            if (
                domain == DOMAIN
                and identifier.startswith(prefix)
                and (
                    (domain, identifier) != device_identity
                    or device.config_subentry_id is not None
                )
            ):
                raise ConfigEntryError(
                    "Registered device does not match the configured binding"
                )

    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != DOMAIN or not entity.unique_id.startswith(prefix):
            continue
        try:
            scoped = scoped_identity(entry.entry_id, entity.unique_id)
        except ValueError as exc:
            raise ConfigEntryError("Invalid scoped registry entity identity") from exc
        if scoped is not None:
            _, scope, _ = scoped
            matches = scope[0] == binding[0] and all(
                part is None or part == expected
                for part, expected in zip(scope[1:], binding[1:], strict=True)
            )
        else:
            station, _, role = entity.unique_id[len(prefix) :].rpartition(":")
            matches = station == binding[0] and role.isidentifier()
        if not matches or entity.config_subentry_id is not None:
            raise ConfigEntryError(
                "Registered entity does not match the configured binding"
            )
        if entity.device_id is not None:
            device = devices.async_get(entity.device_id)
            if device is None or device_identity not in device.identifiers:
                raise ConfigEntryError(
                    "Registered entity is linked to a different device"
                )
    return binding
