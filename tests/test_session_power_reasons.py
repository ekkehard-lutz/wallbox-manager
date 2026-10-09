"""Reproduce protective unknown transitions without weakening source arbitration."""

from datetime import UTC, datetime, timedelta

from test_ha_observations import observation
from test_session_meter_attribution import context as context
from test_session_meter_attribution import start

from custom_components.wallbox_manager.core.sessions import SessionEvent
from custom_components.wallbox_manager.core.sessions import SessionEventKind as Kind
from custom_components.wallbox_manager.core.telemetry import Quantity
from custom_components.wallbox_manager.session_entity import SessionSensor


def test_equal_timestamp_conflict_unknown_then_new_generation_recovers(context):
    runtime, token, parent, scope, at = context
    start(runtime, token, scope, at)
    sensor = SessionSensor(runtime, "test", scope, "power")
    t = at + timedelta(seconds=10)
    runtime.observe(token, (observation(parent, Quantity.POWER, 1380, t),))
    assert sensor.native_value == 1380
    runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.UPDATED, t, sequence=1),
        (observation(scope, Quantity.POWER, 1400, t),),
        live=True,
    )
    assert sensor.native_value is None
    assert (
        sensor.extra_state_attributes["power_availability_reason"]
        == "ledger_value_conflict"
    )
    # Periodic physical refresh / identical replay is not new acquisition.
    runtime.observe(token, (observation(parent, Quantity.POWER, 1380, t),))
    assert sensor.native_value is None
    runtime.observe(
        token, (observation(parent, Quantity.POWER, 1400, t + timedelta(seconds=1)),)
    )
    assert sensor.native_value == 1400
    assert sensor.extra_state_attributes["power_availability_reason"] == "valid"


def test_out_of_order_transaction_replacement_and_reconnect_guards(context):
    runtime, token, parent, scope, at = context
    start(runtime, token, scope, at)
    sensor = SessionSensor(runtime, "test", scope, "power")
    t = at + timedelta(seconds=10)
    runtime.observe(token, (observation(parent, Quantity.POWER, 1380, t),))
    runtime.session_event(
        token,
        SessionEvent(scope, "tx", Kind.UPDATED, t, sequence=1),
        (observation(scope, Quantity.POWER, 9000, t - timedelta(seconds=1)),),
        live=True,
    )
    assert sensor.native_value == 1380
    start(runtime, token, scope, t + timedelta(seconds=1), external="new")
    assert sensor.native_value is None
    assert (
        sensor.power_status(datetime.now(UTC))[0] == "measurement_precedes_transaction"
    )
    runtime.observe(
        token, (observation(parent, Quantity.POWER, 1600, t + timedelta(seconds=2)),)
    )
    assert sensor.native_value == 1600
    runtime.disconnect(token)
    assert sensor.power_status(datetime.now(UTC))[0] == "disconnected"
    runtime.connect(scope.station)
    assert sensor.power_status(datetime.now(UTC))[0] == "no_fresh_scoped_measurement"
