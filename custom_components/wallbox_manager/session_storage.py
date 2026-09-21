"""Versioned Home Assistant storage for the entry-owned complete session ledger."""

from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1


class SessionStorage:
    def __init__(self, hass, entry_id, ledger):
        self.ledger = ledger
        self.store = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}.sessions",
            private=True,
            atomic_writes=True,
        )
        self._unsubscribe = None

    async def load(self):
        if (data := await self.store.async_load()) is not None:
            self.ledger.restore(data)
        self._unsubscribe = self.ledger.subscribe(self.changed)

    def changed(self):
        self.store.async_delay_save(self.ledger.dump, 1)

    async def close(self):
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await self.store.async_save(self.ledger.dump())
