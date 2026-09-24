"""Auto-discovered HA subentries hold real station/connector capability fallbacks."""

import json
from types import MappingProxyType

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import callback

from .config_flow import migrate_options, validate_reference_options
from .control.reference import ConfiguredReference


def subentry_key(station, evse, connector):
    return json.dumps([station, str(evse), str(connector)], separators=(",", ":"))


@callback
def ensure_subentry(hass, entry, station, evse, connector, references=None):
    key = subentry_key(station, evse, connector)
    existing = next(
        (
            s
            for s in entry.subentries.values()
            if s.subentry_type == "wallbox" and s.unique_id == key
        ),
        None,
    )
    if existing:
        return existing
    subentry = ConfigSubentry(
        data=MappingProxyType(
            {
                "station": station,
                "evse": str(evse),
                "connector": str(connector),
                "references": references or {},
            }
        ),
        subentry_type="wallbox",
        title=f"{station} / {evse} / {connector}",
        unique_id=key,
    )
    hass.config_entries.async_add_subentry(entry, subentry)
    return subentry


@callback
def setup_station_configuration(hass, entry, runtime):
    options = migrate_options(entry.options)
    for station, connectors in options.get("station_references", {}).items():
        for connector_key, references in connectors.items():
            try:
                validate_reference_options(references)
                if references["reference_station_id"] != station or connector_key != (
                    f"{references['reference_evse_id']}:"
                    f"{references['reference_connector_id']}"
                ):
                    raise ValueError("station association mismatch")
            except ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError:
                options["unassigned_references"] = {
                    **options.get("unassigned_references", {}),
                    f"{station}/{connector_key}": references,
                }
                continue
            ensure_subentry(
                hass,
                entry,
                station,
                references["reference_evse_id"],
                references["reference_connector_id"],
                references,
            )
    options.pop("station_references", None)
    if options != entry.options:
        hass.config_entries.async_update_entry(entry, options=options)

    @callback
    def changed(snapshot):
        if snapshot.protocol_version != "2.1":
            return
        for target in snapshot.connectors:
            ensure_subentry(
                hass, entry, target.station.value, target.evse.value, target.value
            )

    entry.async_on_unload(runtime.subscribe(changed))
    for snapshot in runtime.stations:
        changed(snapshot)


class EntryReference(ConfiguredReference):
    """Live subentry reads preserve primitive revalidation after options edits."""

    def __init__(self, entry):
        super().__init__({})
        self.entry = entry

    def capabilities(self, target, at):
        values = []
        for subentry in self.entry.subentries.values():
            if subentry.subentry_type != "wallbox":
                continue
            data = subentry.data
            if data["station"] != target.station.value:
                continue
            reference = ConfiguredReference(data.get("references", {}))
            values.extend(reference.capabilities(target, at))
        return tuple(values)
