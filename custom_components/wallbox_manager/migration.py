"""Fail-before-write single-target binding and non-destructive registry migration."""

import json

from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .settings import OBSOLETE_PV


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


def binding_for(entry, registry, devices, profiles):
    candidates = set()
    stations = set()
    scopes = []
    if len(entry.subentries) > 1:
        raise ConfigEntryError(
            "Multiple legacy subentries require a migration decision"
        )
    if "station_id" in entry.data:
        candidates.add(
            target(
                (
                    entry.data["station_id"],
                    entry.data["evse_id"],
                    entry.data["connector_id"],
                )
            )
        )
    for subentry in entry.subentries.values():
        if subentry.subentry_type != "wallbox":
            raise ConfigEntryError("Unknown legacy subentry; migration requires review")
        candidates.add(
            target(tuple(subentry.data[k] for k in ("station", "evse", "connector")))
        )
    for station, references in entry.options.get("station_references", {}).items():
        for reference in references.values():
            candidates.add(
                target(
                    (
                        station,
                        reference["reference_evse_id"],
                        reference["reference_connector_id"],
                    )
                )
            )
    for key in profiles:
        try:
            candidates.add(target(json.loads(key)))
        except ValueError, TypeError:
            raise ConfigEntryError(
                "Unrecognized profile target; migration requires review"
            ) from None
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        prefix = f"{entry.entry_id}:"
        for family in ("control", "observation", "session", "capability"):
            start = prefix + family + ":"
            if entity.unique_id.startswith(start):
                parts = json.loads(entity.unique_id[len(start) :])
                stations.add(parts[0])
                scopes.append(parts[:3])
                if parts[1] is not None and parts[2] is not None:
                    candidates.add(target(parts[:3]))
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        for domain, identifier in device.identifiers:
            if domain == DOMAIN and identifier.startswith(f"{entry.entry_id}:"):
                stations.add(identifier[len(entry.entry_id) + 1 :])
    if (
        len(candidates) != 1
        or stations - {next(iter(candidates))[0]}
        or any(
            any(
                a is not None and a != b
                for a, b in zip(scope, next(iter(candidates)), strict=True)
            )
            for scope in scopes
        )
    ):
        raise ConfigEntryError(
            "A single explicit station/EVSE/connector binding is required; "
            "multiple or incomplete legacy targets need a migration decision"
        )
    return next(iter(candidates))


def migrate(hass, entry, profiles):
    registry, devices = er.async_get(hass), dr.async_get(hass)
    binding = binding_for(entry, registry, devices, profiles)
    data = {
        **entry.data,
        "station_id": binding[0],
        "evse_id": binding[1],
        "connector_id": binding[2],
    }
    options = {k: v for k, v in entry.options.items() if k not in OBSOLETE_PV}
    if entry.subentries:
        options.setdefault(
            "passive_v04_legacy_subentries",
            [
                {
                    "id": s.subentry_id,
                    "data": dict(s.data),
                    "title": s.title,
                    "unique_id": s.unique_id,
                }
                for s in entry.subentries.values()
            ],
        )
        # beta.18 already understands this fallback layout and recreates subentries.
        references = dict(options.get("station_references", {}))
        station_refs = dict(references.get(binding[0], {}))
        subentry = next(iter(entry.subentries.values()))
        station_refs[f"{binding[1]}:{binding[2]}"] = {
            **subentry.data.get("references", {}),
            "reference_station_id": binding[0],
            "reference_evse_id": int(binding[1]),
            "reference_connector_id": int(binding[2]),
        }
        references[binding[0]] = station_refs
        options["station_references"] = references
    # No schema version bump: beta.18 can read these data/options unchanged.
    hass.config_entries.async_update_entry(entry, data=data, options=options)
    # HA processes device moves immediately and removes entities still attached
    # to the old subentry. Move entities FIRST, then their existing device.
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        retired = False
        for family, roles in (
            ("control", {"phase_switch_deviation_pct", *OBSOLETE_PV}),
            (
                "capability",
                {"maximum_current_1", "maximum_current_2", "maximum_current_3"},
            ),
        ):
            prefix = f"{entry.entry_id}:{family}:"
            if entity.unique_id.startswith(prefix):
                parts = json.loads(entity.unique_id[len(prefix) :])
                retired = parts[3] in roles
                break
        if retired:
            registry.async_remove(entity.entity_id)
            continue
        if entity.config_subentry_id is not None:
            registry.async_update_entity(entity.entity_id, config_subentry_id=None)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if device.config_subentry_id is not None:
            devices.async_update_device(device.id, new_config_subentry_id=None)
    for subentry_id in tuple(entry.subentries):
        hass.config_entries.async_remove_subentry(entry, subentry_id)
    return binding
