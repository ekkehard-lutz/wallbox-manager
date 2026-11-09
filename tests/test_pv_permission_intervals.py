"""Canonical ChargingEnabled edges split PV economic sampling intervals."""

import pytest
from conftest import eventually
from test_active_control import event
from test_protocol import peer
from test_pv_phase_lockout import PhaseStation, establish, periods
from test_pv_runtime import install, release, tick


@pytest.mark.parametrize(
    "profile,battery",
    [("PV_OPTIMUM", True), ("PV_MAXIMUM", True), ("PV_SURPLUS", False)],
)
@pytest.mark.parametrize("disabled", ["false", None])
async def test_permission_gap_between_ticks_breaks_import_interval(
    ha, profile, battery, disabled
):
    # Install the entry before opening its simulated OCPP peer.
    entry = await install(ha, battery=battery)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await release(entry, station, profile=profile)
        await establish(grid, pv, station, 1, 20)
        now = [1000]
        pv.monotonic = lambda: now[0]
        pv.values.update(grid_import_power=1800, soc_speicher_aktuell=100)
        pv.core.startup = 0
        await tick(pv)
        pv.budget.wh = 4
        pv.lifecycle()
        timer, task = pv.timer, grid.task
        count = len(station.profiles)
        first = pv.budget.previous
        assert first is not None

        now[0] = 1001
        event(entry, "ChargingEnabled", "true")
        event(entry, "ChargingEnabled", "true")
        assert pv.budget.previous == first
        now[0] = 1002
        event(entry, "ChargingEnabled", disabled)
        assert grid.hardware_enabled() is (False if disabled == "false" else None)
        assert pv.budget.previous is None and pv.budget.low_since is None
        assert pv.budget.wh == 4
        now[0] = 1004
        event(entry, "ChargingEnabled", "true")
        assert pv.timer is timer and grid.task is task
        assert len(station.profiles) == count

        now[0] = 1005
        await tick(pv)
        assert pv.budget.wh == 4
        now[0] = 1010
        await tick(pv)
        assert pv.budget.wh == 6.5


@pytest.mark.parametrize("battery", [True, False])
async def test_permission_gap_restarts_continuous_low_import_evidence(ha, battery):
    entry = await install(ha, battery=battery)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await release(
            entry, station, profile="PV_OPTIMUM" if battery else "PV_SURPLUS"
        )
        now = [1000]
        pv.monotonic = lambda: now[0]
        pv.values.update(
            grid_import_power=0,
            grid_export_power=2000,
            soc_speicher_aktuell=100,
            storage_charge_power=100,
        )
        assert pv.budget.bind([*grid.state.binding, "remote", grid.transaction()])
        pv.budget.wh, pv.budget.stopped, pv.budget.cause = 50, True, "import_budget"
        await tick(pv)
        assert pv.budget.low_since == 1000 and not pv.budget.reset
        pv.lifecycle()
        count, timer = len(station.profiles), pv.timer

        now[0] = 1004
        event(entry, "ChargingEnabled", "false")
        now[0] = 1015
        event(entry, "ChargingEnabled", "true")
        assert pv.timer is timer and len(station.profiles) == count
        for when in (1016, 1031):
            now[0] = when
            await tick(pv)
            assert pv.budget.low_since == 1016
            assert pv.budget.wh == 50 and pv.budget.stopped and not pv.budget.reset
            assert pv.budget.cause == "import_budget"
            assert len(station.profiles) == count

        now[0] = 1032
        await tick(pv)
        assert not pv.budget.stopped and pv.budget.wh == 0
        assert periods(station, count) == [(1, 6 if battery else 9)]


async def test_permission_loss_preserves_qualified_reset_and_soc_stop(ha):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await release(entry, station, profile="PV_OPTIMUM")
        await establish(grid, pv, station, 1, 20)
        await tick(pv)
        pv.budget.wh, pv.budget.stopped = 0, True
        pv.budget.reset, pv.budget.cause = True, "import_budget"
        before = pv.budget.saved()
        event(entry, "ChargingEnabled", None)
        assert pv.budget.saved() == before
        event(entry, "ChargingEnabled", "true")
        pv.values["soc_speicher_aktuell"] = 0
        count = len(station.profiles)
        await tick(pv)
        assert periods(station, count) == [(None, 0)]
        assert pv.budget.saved() == before

    await eventually(lambda: not grid.state.connected)
    assert pv.budget.previous is None and pv.budget.low_since is None
    assert pv.budget.saved() == before


async def test_disconnect_breaks_pv_economic_interval_without_erasing_wh(ha):
    entry = await install(ha)
    async with peer(entry, station_type=PhaseStation) as station:
        grid, pv = await release(entry, station, profile="PV_OPTIMUM")
        await establish(grid, pv, station, 1, 20)
        await tick(pv)
        assert pv.budget.previous is not None
        pv.budget.wh = 12
    await eventually(lambda: not grid.state.connected)
    assert pv.budget.previous is None and pv.budget.low_since is None
    assert pv.budget.wh == 12
