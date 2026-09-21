"""The existing WebSocket keepalive owns station liveness, not meter cadence."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from test_ocpp_bidirectional import paired
from test_ocpp_transaction_sessions import groups
from test_ocpp_transport import state_when

from custom_components.wallbox_manager.core.models import StationId


async def test_keepalive_failure_invalidates_runtime_observations():
    async with paired() as (server, station):
        await station.boot()
        await station.call(
            station._call.MeterValues(
                evse_id=1, meter_value=groups(datetime.now(UTC), 1000, 0)
            ),
            suppress=False,
        )
        root = StationId("station-a")
        assert server.runtime.get(root).observations
        connection = server.sessions[root].connection
        assert connection.ping_interval == connection.ping_timeout == 20
        # Exercise the library's actual timeout path without a 40-second delay.
        connection.keepalive_task.cancel()
        await asyncio.gather(connection.keepalive_task, return_exceptions=True)
        connection.ping_interval = connection.ping_timeout = 0.01
        connection.ping = AsyncMock(
            return_value=asyncio.get_running_loop().create_future()
        )
        connection.start_keepalive()
        snapshot = await state_when(server.runtime, lambda s: not s.connected)
        assert snapshot.observations == ()
        assert not server.runtime.current(server.sessions[root].adapter.token)
