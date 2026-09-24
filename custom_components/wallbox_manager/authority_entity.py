"""Station-wide authority UI: one status and one explicit one-way action."""

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

from .core.models import StationId


@callback
def setup_authority_entities(hass, entry, add_entities, key, factory):
    runtime = entry.runtime_data.state
    seen = set()

    def add(station):
        if station not in seen:
            seen.add(station)
            add_entities([factory(station)])

    @callback
    def changed(snapshot):
        if snapshot.authority is not None:
            add(snapshot.token.station)

    entry.async_on_unload(runtime.subscribe(changed))
    prefix, suffix = f"{entry.entry_id}:", f":{key}"
    for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        if entity.unique_id.startswith(prefix) and entity.unique_id.endswith(suffix):
            try:
                add(StationId(entity.unique_id[len(prefix) : -len(suffix)]))
            except ValueError:
                continue
    for snapshot in runtime.stations:
        changed(snapshot)
