"""Real HA Store round trips: schema, active identity, history and reloads."""

import json

import pytest
from homeassistant.core import HomeAssistant
from test_sessions import AT, SCOPE, Kind, event

from custom_components.wallbox_manager.session_ledger import SessionLedger
from custom_components.wallbox_manager.session_storage import (
    STORAGE_VERSION,
    SessionStorage,
)


@pytest.mark.parametrize("completed", [False, True])
async def test_store_restart_preserves_identity_and_replay(tmp_path, completed):
    hass = HomeAssistant(str(tmp_path))
    ledger = SessionLedger()
    storage = SessionStorage(hass, "entry", ledger)
    await storage.load()
    ledger.apply(event(meter_wh=1234))
    session_id = ledger.get(SCOPE).session_id
    if completed:
        ledger.apply(event(Kind.ENDED, 10, meter_wh=2234))
    await storage.close()
    assert not ledger._listeners
    await hass.async_stop()
    disk = json.loads(
        (tmp_path / ".storage/wallbox_manager.entry.sessions").read_text()
    )
    assert disk["version"] == STORAGE_VERSION == 1
    assert disk["data"]["records"][0]["session_id"] == session_id
    assert disk["data"]["records"][0]["duration_seconds"] == (10 if completed else None)

    restarted = HomeAssistant(str(tmp_path))
    loaded = SessionLedger()
    second = SessionStorage(restarted, "entry", loaded)
    try:
        await second.load()
        assert loaded.dump() == ledger.dump()
        assert loaded.get(SCOPE).session_id == session_id
        assert not loaded.apply(event(meter_wh=1234))
        if completed:
            assert not loaded.apply(event(Kind.ENDED, 10, meter_wh=2234))
            assert len(loaded.history()) == 1
        else:
            assert loaded.apply(event(Kind.UPDATED, 5))
            assert loaded.get(SCOPE).session_id == session_id
        await second.close()
    finally:
        await restarted.async_stop()


async def test_reload_keeps_all_history_and_id_allocator(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    ledger = SessionLedger()
    storage = SessionStorage(hass, "entry", ledger)
    try:
        await storage.load()
        first = ledger.start_legacy(SCOPE, AT, 100)
        ledger.apply(event(Kind.ENDED, 1, external=first, meter_wh=120))
        ledger.apply(event(seconds=2, external="second", meter_wh=120))
        ledger.apply(event(Kind.ENDED, 3, external="second", meter_wh=150))
        ledger.apply(event(seconds=4, external="third", meter_wh=150))
        await storage.close()
        loaded = SessionLedger()
        second = SessionStorage(hass, "entry", loaded)
        await second.load()
        assert loaded.dump() == ledger.dump()
        assert len(loaded.history(SCOPE)) == 2
        assert loaded.get(SCOPE).active
        assert loaded.next_transaction_id == 2
        await second.close()
    finally:
        await hass.async_stop()


def test_invalid_document_does_not_partially_replace_ledger():
    ledger = SessionLedger()
    ledger.apply(event())
    before = ledger.dump()
    broken = ledger.dump()
    broken["records"][0]["started_at"] = "2026-01-01T00:00:00"
    with pytest.raises(ValueError):
        ledger.restore(broken)
    assert ledger.dump() == before
