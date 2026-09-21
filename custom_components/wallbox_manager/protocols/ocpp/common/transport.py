"""Async CSMS listener with explicit subprotocol and per-socket ownership.

Adapted from lbbrhzn/ocpp api.py create/select_subprotocol/on_connect at
848407c11ff659ce59779a99ce69984bbb0e3ce1. Copyright (c) 2021 lbbrhzn, MIT.
See ../../../THIRD_PARTY_NOTICES.md. No implicit 1.6 fallback or control services.
"""

import asyncio
import logging
from urllib.parse import unquote, urlsplit

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, NegotiationError

from ....core.models import StationId
from ....runtime import Runtime
from ..v16.adapter import Adapter as Adapter16
from ..v21.adapter import Adapter as Adapter21
from ..v201.adapter import Adapter as Adapter201
from .sessions import Session

_LOGGER = logging.getLogger(__name__)
ADAPTERS = {"ocpp2.1": Adapter21, "ocpp2.0.1": Adapter201, "ocpp1.6": Adapter16}


def station_from_path(path: str) -> StationId:
    parts = urlsplit(path)
    if parts.query or parts.fragment or not parts.path.startswith("/"):
        raise ValueError("expected /station-id")
    value = unquote(parts.path[1:], errors="strict")
    if not value or "/" in value or len(value) > 128 or any(ord(c) < 33 for c in value):
        raise ValueError("invalid station path")
    return StationId(value)


def select_subprotocol(connection, subprotocols):
    offered = set(subprotocols)
    for protocol in ADAPTERS:
        if protocol in offered:
            return protocol
    raise NegotiationError("an explicit supported OCPP subprotocol is required")


class CentralSystem:
    """One listener, multiple stations; replaced sessions cannot publish late data."""

    def __init__(self, runtime: Runtime, host: str, port: int, *, response_timeout=10):
        self.runtime = runtime
        self.host, self.port = host, port
        self.response_timeout = response_timeout
        self.sessions: dict[StationId, Session] = {}
        self._admissions = {}
        self._server = None
        self._closing = False
        self._stop_task = None

    async def start(self):
        self._server = await serve(
            self.on_connect,
            self.host,
            self.port,
            subprotocols=list(ADAPTERS),
            select_subprotocol=select_subprotocol,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=1048576,
            max_queue=16,
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def on_connect(self, connection):
        session = Session(connection)
        installed = False
        token = None
        try:
            station = station_from_path(connection.request.path)
            if self._closing:
                return
            ticket = self._admissions[station] = object()
            old = self.sessions.get(station)
            if old is not None:
                self.runtime.disconnect(old.adapter.token)
                await old.stop()
                if any(not t.done() for t in old.retirement_tasks):
                    raise TimeoutError("old session still retiring")
            if self._closing or self._admissions.get(station) is not ticket:
                return
            token = self.runtime.connect(
                station,
                protocol="ocpp",
                protocol_version=connection.subprotocol.removeprefix("ocpp"),
            )
            adapter = ADAPTERS[connection.subprotocol](
                station.value,
                connection,
                self.runtime,
                session,
                token,
                self.response_timeout,
            )
            session.adapter = adapter
            self.sessions[station] = session
            installed = True
            reader = session.spawn(adapter.start())
            # Known reconnects need read-only rediscovery even without another boot.
            if token.boot_generation:
                adapter.start_discovery()
            await reader
        except ConnectionClosed, asyncio.CancelledError:
            pass
        except ValueError, TimeoutError:
            _LOGGER.warning(
                "OCPP session rejected or discovery transport timed out", exc_info=True
            )
        except Exception:
            _LOGGER.exception("OCPP session failed")
        finally:
            if installed:
                self.runtime.disconnect(session.adapter.token)
            elif token is not None:
                self.runtime.disconnect(token)
            await session.stop()
            # Retain the latest retired owner until replacement, so counters and
            # pending teardown cannot be bypassed by another handshake.

    async def _stop(self):
        self._closing = True
        self._admissions.clear()
        if self._server is not None:
            self._server.close()
        await asyncio.gather(
            *(session.stop() for session in tuple(self.sessions.values()))
        )
        if self._server is not None:
            await self._server.wait_closed()

    async def stop(self):
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop())
        cancelled = False
        while not self._stop_task.done():
            try:
                await asyncio.wait({self._stop_task})
            except asyncio.CancelledError:
                cancelled = True
        self._stop_task.result()
        if cancelled:
            raise asyncio.CancelledError
