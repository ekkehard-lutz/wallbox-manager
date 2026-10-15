"""Versioned wire schemas -> scoped, generation-fenced runtime observations."""

from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta

import pytest
from test_ocpp_bidirectional import paired
from test_ocpp_reporting_ack import reporting_call
from test_ocpp_transport import peer, state_when
from test_ocpp_transport import server as server

from custom_components.wallbox_manager.core.capabilities import EvidenceState
from custom_components.wallbox_manager.core.models import ConnectorId, EvseId, StationId
from custom_components.wallbox_manager.core.telemetry import Channel, Quantity, State


@pytest.mark.parametrize("failure", ["new_transaction", "decreasing_sequence"])
def test_departure_cannot_override_transaction_evidence(monkeypatch, now, failure):
    from types import SimpleNamespace

    from custom_components.wallbox_manager.core.sessions import (
        SessionEvent,
        SessionEventKind,
    )
    from custom_components.wallbox_manager.core.telemetry import Observation
    from custom_components.wallbox_manager.runtime import Runtime

    clock = [now]
    monkeypatch.setattr(
        "custom_components.wallbox_manager.runtime.datetime",
        SimpleNamespace(now=lambda tz: clock[0]),
    )
    runtime = Runtime()
    token = runtime.connect(StationId("departure"))
    target = ConnectorId(EvseId(token.station, "1"), "1")

    def event(kind, transaction, sequence):
        clock[0] += timedelta(seconds=1)
        return runtime.session_event(
            token,
            SessionEvent(
                target,
                transaction,
                kind,
                clock[0],
                end_reason="EVDeparted",
                sequence=sequence,
            ),
            live=True,
        )

    assert event(SessionEventKind.STARTED, "T1", 10)
    clock[0] += timedelta(seconds=1)
    runtime.observe_zero_point(token, target)
    assert event(
        SessionEventKind.ENDED, "T1", 9 if failure == "decreasing_sequence" else 11
    )
    if failure == "new_transaction":
        assert event(SessionEventKind.STARTED, "T2", 0)
    clock[0] += timedelta(seconds=1)
    channel = Channel(target, Quantity.CONNECTOR_STATE)
    runtime.observe(
        token,
        (
            Observation(
                channel, State.AVAILABLE, clock[0], clock[0], None, "test:Available"
            ),
        ),
    )
    assert runtime.physical_limitation(target) == "pause_cp_unverified"
    assert runtime.get(token.station).observation(channel) is None


def assert_generic(value):
    assert not type(value).__module__.startswith(
        ("ocpp", "homeassistant", "websockets")
    )
    if is_dataclass(value):
        for field in fields(value):
            assert_generic(getattr(value, field.name))
    elif isinstance(value, tuple):
        for item in value:
            assert_generic(item)


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_meter_scope_units_and_old_generation(protocol):
    async with paired(protocol) as (server, station):
        await station.boot()
        await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        now = datetime.now(UTC)

        def groups(value, at=now):
            return [
                {
                    "timestamp": at.isoformat(),
                    "sampled_value": [
                        {
                            "measurand": "Power.Active.Import",
                            "value": value,
                            "unit_of_measure": {"unit": "kW", "multiplier": -1},
                        }
                    ],
                }
            ]

        for evse_id, value in [(0, 100), (1, 30), (2, 70)]:
            result = await station.call(
                station._call.MeterValues(evse_id=evse_id, meter_value=groups(value)),
                suppress=False,
            )
            assert isinstance(result, station._call_result.MeterValues)
        root = StationId("station-a")
        snapshot = server.runtime.get(root)
        assert snapshot.observation(Channel(root, Quantity.POWER)).value == 10000
        assert (
            snapshot.observation(Channel(EvseId(root, "1"), Quantity.POWER)).value
            == 3000
        )
        assert (
            snapshot.observation(Channel(EvseId(root, "2"), Quantity.POWER)).value
            == 7000
        )
        assert_generic(snapshot)
        await station.call(
            station._call.MeterValues(
                evse_id=1, meter_value=groups(999, now - timedelta(seconds=1))
            ),
            suppress=False,
        )
        assert server.runtime.get(root) == snapshot
        adapter = server.sessions[root].adapter
        old_token = adapter.token
        await station.boot()
        assert not server.runtime.observe(old_token, snapshot.observations)
        assert server.runtime.get(root).observations == ()
        assert set(server.runtime.get(root).supported_channels) == set(
            snapshot.supported_channels
        )


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_occupied_is_not_charging_and_transaction_scope(protocol):
    async with paired(protocol) as (server, station):
        await station.boot()
        await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        root = StationId("station-a")
        scope = ConnectorId(EvseId(root, "1"), "1")
        at = datetime.now(UTC)
        await station.call(
            station._call.StatusNotification(
                timestamp=at.isoformat(),
                connector_status="Occupied",
                evse_id=1,
                connector_id=1,
            ),
            suppress=False,
        )
        snapshot = server.runtime.get(root)
        assert (
            snapshot.observation(Channel(scope, Quantity.CONNECTOR_STATE)).value
            == State.OCCUPIED
        )
        assert snapshot.observation(Channel(scope, Quantity.CHARGING_STATE)) is None
        event = reporting_call(station, "Updated")
        event.timestamp = at.isoformat()
        event.meter_value[0]["timestamp"] = at.isoformat()
        event.transaction_info["charging_state"] = "Charging"
        await station.call(event, suppress=False)
        snapshot = server.runtime.get(root)
        assert (
            snapshot.observation(Channel(scope, Quantity.CHARGING_STATE)).value
            == State.CHARGING
        )
        assert snapshot.observation(Channel(scope, Quantity.VOLTAGE_L1)) is None
        assert Channel(scope, Quantity.VOLTAGE_L1) not in snapshot.supported_channels
        assert (
            snapshot.observation(Channel(EvseId(root, "1"), Quantity.VOLTAGE_L1))
            is None
        )
        # Offline replay must not masquerade as current runtime state.
        event.offline = True
        event.timestamp = (at + timedelta(seconds=1)).isoformat()
        event.transaction_info["charging_state"] = "Idle"
        await station.call(event, suppress=False)
        assert server.runtime.get(root) == snapshot
        # Older online state also cannot roll back the live channel.
        event.offline = False
        event.timestamp = (at - timedelta(seconds=1)).isoformat()
        await station.call(event, suppress=False)
        assert server.runtime.get(root) == snapshot
        # EVSE-only event remains distinct; no connector 1 assumption.
        event.evse = {"id": 2}
        event.transaction_info["transaction_id"] = "other-evse"
        event.timestamp = at.isoformat()
        await station.call(event, suppress=False)
        assert (
            server.runtime.get(root)
            .observation(Channel(EvseId(root, "2"), Quantity.CHARGING_STATE))
            .value
            == State.IDLE
        )
        assert_generic(server.runtime.get(root))


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
@pytest.mark.parametrize(
    "status,expected",
    [
        ("Available", State.AVAILABLE),
        ("Occupied", State.OCCUPIED),
        ("Reserved", State.RESERVED),
        ("Unavailable", State.UNAVAILABLE),
        ("Faulted", State.FAULTED),
    ],
)
async def test_2x_status_mapping(protocol, status, expected):
    async with paired(protocol) as (server, station):
        await station.boot()
        await station.call(
            station._call.StatusNotification(
                timestamp=datetime.now(UTC).isoformat(),
                connector_status=status,
                evse_id=4,
                connector_id=7,
            ),
            suppress=False,
        )
        scope = ConnectorId(EvseId(StationId("station-a"), "4"), "7")
        assert (
            server.runtime.get(scope.evse.station)
            .observation(Channel(scope, Quantity.CONNECTOR_STATE))
            .value
            == expected
        )


@pytest.mark.parametrize(
    "status,expected",
    [
        ("Available", State.IDLE),
        ("Preparing", State.PREPARING),
        ("Charging", State.CHARGING),
        ("SuspendedEV", State.SUSPENDED_VEHICLE),
        ("SuspendedEVSE", State.SUSPENDED_STATION),
        ("Finishing", State.FINISHING),
        ("Faulted", State.UNKNOWN),
    ],
)
async def test_16_status_and_meter_values(server, status, expected):
    async with peer(server, "ocpp1.6") as station:
        await station.boot()
        await station.call(
            "StatusNotification",
            {"connectorId": 2, "errorCode": "NoError", "status": status},
        )
        scope = ConnectorId(EvseId(StationId("station-a"), "connector-2"), "2")
        snapshot = server.runtime.get(scope.evse.station)
        assert (
            snapshot.observation(Channel(scope, Quantity.CHARGING_STATE)).value
            == expected
        )
        at = datetime.now(UTC).isoformat()
        for connector_id in (0, 2):
            response = await station.call(
                "MeterValues",
                {
                    "connectorId": connector_id,
                    "meterValue": [
                        {
                            "timestamp": at,
                            "sampledValue": [
                                {
                                    "measurand": "Energy.Active.Import.Register",
                                    "unit": "kWh",
                                    "value": "2.5",
                                }
                            ],
                        }
                    ],
                },
            )
            assert response[0] == 3
        snapshot = server.runtime.get(scope.evse.station)
        assert snapshot.observation(Channel(scope, Quantity.ENERGY)).value == 2500
        assert (
            snapshot.observation(Channel(scope.evse.station, Quantity.ENERGY)).value
            == 2500
        )


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_fresh_transaction_state_without_repeated_evse_identity(protocol):
    async with paired(protocol) as (server, station):
        await station.boot()
        await state_when(
            server.runtime, lambda s: s.discovery.state == EvidenceState.VERIFIED
        )
        root = StationId("station-a")
        scope = ConnectorId(EvseId(root, "2"), "7")
        channel = Channel(scope, Quantity.CHARGING_STATE)
        for index, state in enumerate(("Charging", "EVConnected")):
            await station.call(
                station._call.TransactionEvent(
                    event_type="Started" if index == 0 else "Updated",
                    seq_no=index,
                    timestamp=(event_at := datetime.now(UTC)).isoformat(),
                    trigger_reason="ChargingStateChanged",
                    transaction_info={
                        "transaction_id": "known",
                        "charging_state": state,
                    },
                    evse={"id": 2, "connector_id": 7} if index == 0 else None,
                ),
                suppress=False,
            )
        observation = server.runtime.get(root).observation(channel)
        assert observation.value == State.CONNECTED
        assert observation.observed_at == event_at
        assert server.runtime.physical_state_fresh(observation)
        await station.call(
            station._call.TransactionEvent(
                event_type="Updated",
                seq_no=2,
                timestamp=datetime.now(UTC).isoformat(),
                trigger_reason="ChargingStateChanged",
                transaction_info={
                    "transaction_id": "unknown",
                    "charging_state": "Charging",
                },
            ),
            suppress=False,
        )
        assert server.runtime.get(root).observation(channel) == observation


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_pause_cached_events_and_live_departure_use_distinct_evidence(protocol):
    async with paired(protocol) as (server, station):
        await station.boot()
        root = StationId("station-a")
        target = ConnectorId(EvseId(root, "1"), "1")
        runtime = server.runtime

        async def event(kind, trigger, sequence, state, **kwargs):
            payload = station._call.TransactionEvent(
                event_type=kind,
                trigger_reason=trigger,
                seq_no=sequence,
                timestamp=datetime.now(UTC).isoformat(),
                transaction_info={
                    "transaction_id": "physical",
                    "charging_state": state,
                    **kwargs.pop("info", {}),
                },
                evse={"id": 1, "connector_id": 1},
                **kwargs,
            )
            await station.call(payload, suppress=False)

        async def status(value):
            await station.call(
                station._call.StatusNotification(
                    evse_id=1,
                    connector_id=1,
                    connector_status=value,
                    timestamp=datetime.now(UTC).isoformat(),
                ),
                suppress=False,
            )

        await status("Occupied")
        await event("Started", "CablePluggedIn", 0, "Charging")
        channel = Channel(target, Quantity.CHARGING_STATE)
        original = runtime.get(root).observation(channel)
        assert runtime.physical_state_fresh(original)
        token = server.sessions[root].adapter.token
        runtime.observe_zero_point(token, target)
        for sequence, trigger in (
            (1, "TxResumed" if protocol == "ocpp2.1" else "Trigger"),
            (2, "MeterValuePeriodic"),
            (3, "ChargingStateChanged"),
        ):
            await event("Updated", trigger, sequence, "EVConnected")
            await status("Available")
            assert runtime.get(root).observation(channel) == original
            assert not runtime.physical_state_fresh(original)
            assert runtime.sessions.get(target).active
        await event(
            "Ended", "EVDeparted", 4, "Idle", info={"stopped_reason": "EVDisconnected"}
        )
        await status("Available")
        state = runtime.get(root)
        assert state.observation(channel).value == State.IDLE
        assert runtime.physical_state_fresh(state.observation(channel))
        assert not runtime.sessions.get(target).active


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
async def test_boot_old_source_resumed_and_conflicting_scope_are_not_live(protocol):
    async with paired(protocol) as (server, station):
        await station.boot()
        root = StationId("station-a")
        target = ConnectorId(EvseId(root, "1"), "1")
        old_source = datetime.now(UTC).isoformat()
        await station.boot()
        await station.call(
            station._call.StatusNotification(
                evse_id=1,
                connector_id=1,
                connector_status="Available",
                timestamp=old_source,
            ),
            suppress=False,
        )
        assert server.runtime.get(root).observations == ()
        assert target in server.runtime.get(root).connectors
        event = station._call.TransactionEvent(
            event_type="Updated",
            trigger_reason="TxResumed" if protocol == "ocpp2.1" else "Trigger",
            offline=protocol == "ocpp2.0.1",
            seq_no=1,
            timestamp=datetime.now(UTC).isoformat(),
            transaction_info={"transaction_id": "resume", "charging_state": "Charging"},
            evse={"id": 1, "connector_id": 1},
        )
        await station.call(event, suppress=False)
        assert server.runtime.sessions.get(target).active
        assert server.runtime.get(root).observations == ()
        event.trigger_reason = "MeterValuePeriodic"
        event.offline = False
        event.seq_no = 2
        event.timestamp = datetime.now(UTC).isoformat()
        await station.call(event, suppress=False)
        channel = Channel(target, Quantity.CHARGING_STATE)
        physical = server.runtime.get(root).observation(channel)
        assert physical.value == State.CHARGING
        assert physical.source.endswith(":Updated:MeterValuePeriodic")
        for scope, sequence in (
            ({"id": 2, "connector_id": 1}, 3),
            ({"id": 1, "connector_id": 1}, 2),
        ):
            event.evse, event.seq_no = scope, sequence
            event.timestamp = datetime.now(UTC).isoformat()
            event.transaction_info["charging_state"] = "Idle"
            await station.call(event, suppress=False)
            assert server.runtime.get(root).observation(channel) == physical
        assert (
            server.runtime.get(root).observation(
                Channel(EvseId(root, "2"), Quantity.CHARGING_STATE)
            )
            is None
        )


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "duplicate",
        "invalid_duplicate",
        "decreasing_update",
        "equal_sequence",
        "missing_sequence",
        "offline",
        "historical",
        "wrong_connector",
        "wrong_transaction",
        "boot",
        "socket",
        "new_zero",
        "restart",
    ],
)
def test_departure_recovery_requires_current_exact_session(monkeypatch, now, case):
    from types import SimpleNamespace

    from custom_components.wallbox_manager.core.events import StationIdentity
    from custom_components.wallbox_manager.core.sessions import (
        SessionEvent,
        SessionEventKind,
    )
    from custom_components.wallbox_manager.core.telemetry import Observation
    from custom_components.wallbox_manager.runtime import Runtime

    clock = [now]
    monkeypatch.setattr(
        "custom_components.wallbox_manager.runtime.datetime",
        SimpleNamespace(now=lambda tz: clock[0]),
    )
    runtime = Runtime()
    token = runtime.connect(StationId("departure"))
    target = ConnectorId(EvseId(token.station, "1"), "1")

    def tick():
        clock[0] += timedelta(seconds=1)
        return clock[0]

    def event(kind, sequence, *, transaction="T1", scope=target, live=True, at=None):
        return runtime.session_event(
            token,
            SessionEvent(
                scope,
                transaction,
                kind,
                tick() if at is None else at,
                end_reason="EVDeparted",
                sequence=sequence,
            ),
            live=live,
        )

    def report(quantity, value):
        at = tick()
        observation = Observation(
            Channel(target, quantity), value, at, at, None, "test:physical"
        )
        runtime.observe(token, (observation,))
        return observation

    assert event(SessionEventKind.STARTED, 10)
    original = report(Quantity.CONNECTOR_STATE, State.OCCUPIED)
    report(Quantity.CHARGING_STATE, State.CHARGING)
    tick()
    runtime.observe_zero_point(token, target)
    if case == "decreasing_update":
        # Ledger ordering remains timestamp-first; physical evidence cannot
        # reuse sequence 10 even after this accepted update lowers it to 9.
        assert event(SessionEventKind.UPDATED, 9)
    sequence = {
        "equal_sequence": 10,
        "missing_sequence": None,
        "invalid_duplicate": 9,
        "decreasing_update": 10,
    }.get(case, 11)
    kwargs = {"live": case != "offline"}
    if case == "historical":
        kwargs["at"] = now - timedelta(seconds=1)
    elif case == "wrong_connector":
        kwargs["scope"] = ConnectorId(target.evse, "2")
    elif case == "wrong_transaction":
        kwargs["transaction"] = "unknown"
    accepted = event(SessionEventKind.ENDED, sequence, **kwargs)
    assert accepted is (case != "historical")
    departure = runtime._departures.get(target)
    if case in ("duplicate", "invalid_duplicate"):
        assert not event(SessionEventKind.ENDED, 12)
        assert runtime._departures.get(target) == departure
    elif case in ("boot", "socket"):
        old_token = token
        tick()
        token = (
            runtime.boot(token, StationIdentity())
            if case == "boot"
            else runtime.connect(token.station)
        )
        assert not runtime.session_event(
            old_token,
            SessionEvent(target, "T2", SessionEventKind.STARTED, tick()),
            live=True,
        )
    elif case == "new_zero":
        tick()
        runtime.observe_zero_point(token, target)
    available = report(Quantity.CONNECTOR_STATE, State.AVAILABLE)
    recovered = case in ("valid", "duplicate", "restart")
    assert runtime.physical_limitation(target) == (
        None if recovered else "pause_cp_unverified"
    )
    current = runtime.get(token.station).observation(available.channel)
    if recovered:
        assert current == available
        idle = runtime.get(token.station).observation(
            Channel(target, Quantity.CHARGING_STATE)
        )
        assert idle.value == State.IDLE
        assert runtime.physical_state_fresh(idle)
        assert runtime.physical_state_fresh(current)
    else:
        assert current is None if case in ("boot", "socket") else current == original
        assert not runtime.physical_state_fresh(current)
    if case == "restart":
        assert event(SessionEventKind.STARTED, 0, transaction="T2")
        report(Quantity.CONNECTOR_STATE, State.OCCUPIED)
        charging = report(Quantity.CHARGING_STATE, State.CHARGING)
        assert runtime.sessions.get(target).active
        assert runtime.sessions.get(target).external_transaction_id == "T2"
        assert runtime.physical_state_fresh(charging)


@pytest.mark.parametrize("protocol", ["ocpp2.0.1", "ocpp2.1"])
@pytest.mark.parametrize(
    "case",
    [
        "new_transaction",
        "decreasing_sequence",
        "decreasing_update",
        "duplicate",
        "invalid_duplicate",
        "offline",
        "historical",
        "wrong_connector",
        "wrong_transaction",
        "boot",
        "socket",
        "valid_restart",
    ],
)
async def test_wire_departure_evidence_is_causal_and_ordered(protocol, case):
    async with paired(protocol) as (server, station):
        await station.boot()
        runtime = server.runtime
        root = StationId("station-a")
        target = ConnectorId(EvseId(root, "1"), "1")

        async def event(
            kind, sequence, *, transaction="T1", connector=1, offline=False, at=None
        ):
            result = await station.call(
                station._call.TransactionEvent(
                    event_type=kind,
                    seq_no=sequence,
                    timestamp=(at or datetime.now(UTC)).isoformat(),
                    trigger_reason="EVDeparted"
                    if kind == "Ended"
                    else "ChargingStateChanged",
                    transaction_info={
                        "transaction_id": transaction,
                        "charging_state": "Idle" if kind == "Ended" else "Charging",
                    },
                    evse={"id": 1, "connector_id": connector},
                    offline=offline,
                ),
                suppress=False,
            )
            assert isinstance(result, station._call_result.TransactionEvent)

        async def status(value):
            await station.call(
                station._call.StatusNotification(
                    evse_id=1,
                    connector_id=1,
                    connector_status=value,
                    timestamp=datetime.now(UTC).isoformat(),
                ),
                suppress=False,
            )

        await status("Occupied")
        await event("Started", 10)
        channel = Channel(target, Quantity.CHARGING_STATE)
        original = runtime.get(root).observation(channel)
        assert runtime.physical_state_fresh(original)
        runtime.observe_zero_point(server.sessions[root].adapter.token, target)
        if case == "decreasing_update":
            await event("Updated", 9)
        kwargs = {}
        if case == "offline":
            kwargs["offline"] = True
        elif case == "historical":
            kwargs["at"] = datetime.now(UTC) - timedelta(days=1)
        elif case == "wrong_connector":
            kwargs["connector"] = 2
        elif case == "wrong_transaction":
            kwargs["transaction"] = "unknown"
        await event(
            "Ended",
            9
            if case in ("decreasing_sequence", "invalid_duplicate")
            else 10
            if case == "decreasing_update"
            else 11,
            **kwargs,
        )
        if case in ("duplicate", "invalid_duplicate"):
            await event("Ended", 12)
        if case == "new_transaction":
            await event("Started", 0, transaction="T2")
            assert runtime.sessions.get(target).active
        elif case in ("boot", "socket"):
            if case == "socket":
                # Renew the manager socket generation on the simulated transport.
                adapter = server.sessions[root].adapter
                adapter.token = runtime.connect(root)
            else:
                await station.boot()
        await status("Available")
        recovered = case in ("duplicate", "valid_restart")
        assert runtime.physical_limitation(target) == (
            None if recovered else "pause_cp_unverified"
        )
        current = runtime.get(root).observation(channel)
        if recovered:
            assert current.value == State.IDLE
            assert runtime.physical_state_fresh(current)
        else:
            assert (
                current is None if case in ("boot", "socket") else current == original
            )
            assert not runtime.physical_state_fresh(current)
        if case in ("decreasing_sequence", "decreasing_update", "invalid_duplicate"):
            # Physical sequence rejection does not silently change the existing
            # timestamp-first lifecycle/accounting contract.
            assert not runtime.sessions.get(target).active
        if case == "valid_restart":
            await status("Occupied")
            await event("Started", 0, transaction="T2")
            charging = runtime.get(root).observation(channel)
            assert charging.value == State.CHARGING
            assert runtime.physical_state_fresh(charging)
            assert runtime.sessions.get(target).external_transaction_id == "T2"
