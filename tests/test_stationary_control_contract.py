"""Combined real Stationary Domain/DeviceModel and WBM adapter over OCPP frames.

Set WB_SOURCE_ROOT for a separate checkout. CI without the companion checkout
skips this coordinated test; each repository still maintains its own wire tests.
"""

import os
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from conftest import eventually
from ocpp.routing import on
from ocpp.v21 import call_result
from test_active_control import observed, ready
from test_grid_profile import GridStation, start_grid
from test_protocol import peer

SOURCE = Path(
    os.environ.get(
        "WB_SOURCE_ROOT", Path(__file__).resolve().parents[2] / "wallbox-stationary"
    )
)


async def test_combined_stationary_permission_release_and_removed_variable(ha):
    if not (SOURCE / "domain/wallbox.py").exists():
        pytest.skip("Companion Stationary source checkout is required")
    sys.path.insert(0, str(SOURCE))
    try:
        from config.constants import N_FAST, WB_REM
        from config.model import RuntimeConfig
        from domain.wallbox import WallboxDomain
        from ocpp_runtime.device_model import DeviceModel
        from state.evse import EVSEState
    finally:
        sys.path.remove(str(SOURCE))

    physical = EVSEState(
        wb_op_mode=N_FAST,
        wb_phase_mode=3,
        wb_enabled=True,
        wb_bits=0,
        amps_set=0,
        vehicle_connected=True,
        ev_state=2,
    )
    registers = {2005: 0, 1000: 0}
    bus = Mock()

    def write(_device, register, value, **_):
        registers[register] = value
        return value

    def bit(_device, register, bit, enabled):
        value = registers[register]
        return write(
            _device, register, value | (1 << bit) if enabled else value & ~(1 << bit)
        )

    bus.write_register.side_effect = write
    bus.get_register.side_effect = lambda _device, register: registers[register]
    bus.set_bit.side_effect = lambda d, r, b: bit(d, r, b, True)
    bus.clear_bit.side_effect = lambda d, r, b: bit(d, r, b, False)
    ready_event = threading.Event()
    ready_event.set()
    cfg = RuntimeConfig(200, False, 6, 27, 20, 10, 16, 6, 6, 20, 80, 60, 5)
    domain = WallboxDomain(
        evse_state=physical,
        gpio=Mock(),
        modbus=bus,
        meter=Mock(),
        state_lock=threading.Lock(),
        ready_event=ready_event,
        read_runtime_config=lambda: cfg,
        auto_wakeup_enabled=True,
    )
    model = DeviceModel(
        domain.read_state,
        domain.request_control_authority,
        domain.set_ocpp_enabled,
        domain.read_capabilities,
        domain.read_confirmed_enabled,
        lambda: physical.wb_phase_mode,
    )

    class CombinedStation(GridStation):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.rows = model.report(1).report_data

        @on("GetVariables")
        def read(self, get_variable_data):
            self.reads.extend(item["variable"]["name"] for item in get_variable_data)
            return model.get_variables(get_variable_data)

        @on("SetVariables")
        def write(self, set_variable_data):
            self.writes.extend(set_variable_data)
            return model.set_variables(set_variable_data)

        @on("SetChargingProfile")
        def profile(self, evse_id, charging_profile):
            period = charging_profile["charging_schedule"][0][
                "charging_schedule_period"
            ][0]
            domain.apply_ocpp_profile(period["limit"], period.get("number_phases"))
            self.profiles.append((evse_id, charging_profile))
            return call_result.SetChargingProfile(status="Accepted")

        @on("GetCompositeSchedule")
        def composite(self, evse_id, duration, charging_rate_unit=None):
            self.amps, self.phases = physical.amps_set, physical.wb_phase_mode
            return super().composite(evse_id, duration, charging_rate_unit)

    entry = await ha.install()
    async with peer(entry, station_type=CombinedStation) as station:
        control = await ready(entry, station)
        assert "AutoWakeUpEnabled" not in station.reads
        assert "AutoWakeUpEnabled" not in entry.runtime_data.server.adapter.writable
        assert observed(entry, "ControlAuthority") == "local"
        for value in ("false", "true"):
            assert await control.request("ChargingEnabled", value) == "confirmed"
            assert physical.wb_op_mode == N_FAST
            assert physical.amps_set == 0 and physical.wb_phase_mode == 3
        with pytest.raises(Exception, match="authority"):
            domain.apply_ocpp_profile(6, 3)
    # Reconnect through the explicit operator start helper, including a real
    # Domain authority takeover and a valid WBM transaction.
    async with peer(entry, station_type=CombinedStation) as station:
        grid = await start_grid(entry, station)
        assert physical.wb_op_mode == WB_REM and physical.amps_set == 16
        await grid.action()
        await eventually(lambda: physical.amps_set == 0)
        assert physical.wb_enabled is True
        assert observed(entry, "ChargingEnabled") is True
        for value in ("false", "true"):
            assert await control.request("ChargingEnabled", value) == "confirmed"
            assert physical.wb_op_mode == WB_REM and physical.amps_set == 0
