"""Captured pause context and sparse configuration, without control changes."""

import logging
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from test_grid_profile import GridStation
from test_protocol import peer
from test_pv_planning_release import selected_pv
from test_pv_power_diagnostics import LOGGER, POWERS, SAMPLE_KEYS, records
from test_pv_runtime import install, power, release, tick

PAUSE = {"soc_pct", "target_soc_pct", "pv_surplus_proven", "economic_stopped"}
FORBIDDEN = {"soc_stop_pct", "balance_restart_pct", "fast_restart_pct", "soc_hysterese"}


@pytest.mark.parametrize("level", [0, 1, 2, 3])
async def test_300_stop_cycles_bounded_records_and_sensor_writes(
    ha, caplog, monkeypatch, level
):
    from test_pv_target_sensor import sensor

    entry = await install(ha)
    ha.config_entries.async_update_entry(
        entry, options={**entry.options, "diagnostic_level": level}
    )
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        entity = sensor(ha, entry)
        pv.values["soc_speicher_aktuell"] = 47
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            await tick(pv)
            count = len(station.profiles)
            write = Mock(wraps=entity.async_write_ha_state)
            monkeypatch.setattr(entity, "async_write_ha_state", write)
            for n in range(300):
                pv.values["soc_speicher_aktuell"] = 47 + n / 1000
                await tick(pv)
        assert len(station.profiles) == count
        assert write.call_count == 0
        logged = records(caplog)
        if level < 2:
            assert not logged
            return
        assert [r["event"] for r in logged].count("pv_config") == 1
        assert (
            next(r for r in logged if r["event"] == "pv_config")["soc_hysterese"] == 2
        )
        decisions = [r for r in logged if r["event"] in ("pv_decision", "pv_sample")]
        assert len(decisions) == 301 and decisions[0]["event"] == "pv_decision"
        assert decisions[0]["reason"] == "soc_stop" and "stop_pct" in decisions[0]
        for n, r in enumerate(decisions[1:]):
            assert r.keys() == SAMPLE_KEYS | PAUSE
            assert not FORBIDDEN & r.keys()
            assert r["mode"] == "STOP" and r["soc_pct"] == 47 + n / 1000
            assert r["target_soc_pct"] == 50 and r["pv_surplus_proven"] is True
            assert r["economic_stopped"] is False
            assert {k: r[k] for k in POWERS} == dict(
                ev_actual_w=0, grid_w=-2000, pv_w=5000, battery_w=0, household_w=300
            )


async def test_moving_target_soc_and_h_survive_persistence_mutation(
    ha, caplog, monkeypatch
):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station, profile="PV_OPTIMUM")
        pv.now = lambda: datetime(2026, 10, 27, 12, tzinfo=ZoneInfo("Europe/Vienna"))
        pv.values.update(soc_speicher_aktuell=0, remaining_pv_energy=8000)
        await tick(pv)
        expected = pv.target
        original = pv.persist

        async def mutate():
            await original()
            pv.values.update(
                soc_speicher_aktuell=99, remaining_pv_energy=99999, leistung_pv=0
            )
            pv.settings.values["soc_hysterese"] = 9

        monkeypatch.setattr(pv, "persist", mutate)
        pv.values.update(soc_speicher_aktuell=1, leistung_pv=5000)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            await tick(pv)
        r = next(r for r in records(caplog) if r["event"] == "pv_sample")
        assert r["soc_pct"] == 1 and r["target_soc_pct"] == expected
        assert r["pv_surplus_proven"] is True and r["pv_w"] == 5000
        assert not [r for r in records(caplog) if r["event"] == "pv_config"]
        monkeypatch.setattr(pv, "persist", original)
        pv.values.update(soc_speicher_aktuell=1, remaining_pv_energy=7000)
        with caplog.at_level(logging.INFO, logger=LOGGER):
            await tick(pv)
        logged = records(caplog)
        assert [r for r in logged if r["event"] == "pv_config"][0]["soc_hysterese"] == 9
        latest = [r for r in logged if r["event"] == "pv_sample"][-1]
        assert latest["target_soc_pct"] == pv.target
        assert latest["target_soc_pct"] != expected
        assert any(r["event"] == "pv_target" for r in logged)
        assert not any(r["event"] == "pv_decision" for r in logged)


@pytest.mark.parametrize(
    "battery,mode", [(True, "FAST"), (True, "BALANCE"), (False, "SURPLUS")]
)
async def test_economic_zero_point_is_pause_without_soc_stop(ha, caplog, battery, mode):
    entry = await install(ha, battery=battery)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        pv.values.update(leistung_verbraucher=900, storage_charge_power=0)
        await tick(pv)
        await power(station, 1380)
        if mode == "BALANCE":
            pv.core.mode = "BALANCE"
            pv.values["soc_speicher_aktuell"] = 50
        pv.budget.wh = 51
        await tick(pv)
        await power(station, 0)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            await tick(pv)
        r = next(r for r in records(caplog) if r["event"] == "pv_sample")
        assert r.keys() == SAMPLE_KEYS | PAUSE and r["economic_stopped"] is True
        assert r["mode"] == mode
        assert r["soc_pct"] == (50 if mode == "BALANCE" else 60 if battery else None)
        assert r["target_soc_pct"] == (50 if battery else None)
        assert r["pv_surplus_proven"] is (True if battery else None)


@pytest.mark.parametrize(
    "production,load,proven",
    [(None, 300, False), (0, None, False), (300, 300, False), (301, 300, True)],
)
async def test_stop_missing_values_and_surplus_evidence(
    ha, caplog, production, load, proven
):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        pv.values.update(
            soc_speicher_aktuell=47,
            leistung_pv=production,
            leistung_verbraucher=load,
            grid_import_power=None,
            storage_charge_power=None,
        )
        await tick(pv)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            await tick(pv)
        r = next(r for r in records(caplog) if r["event"] == "pv_sample")
        assert r["pv_surplus_proven"] is proven
        assert r["grid_w"] is None and r["battery_w"] is None
        assert r["pv_w"] == production and r["household_w"] == load


async def test_minimum_balance_stop_and_zero_hold_use_existing_decisions(ha, caplog):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        await tick(pv)
        await power(station, 1380)
        pv.core.startup = None
        pv.core.mode = "BALANCE"
        pv.values.update(soc_speicher_aktuell=48.5, storage_discharge_power=1)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            await tick(pv)
            await power(station, 0)
            await tick(pv)
            await tick(pv)
        logged = records(caplog)
        assert any(r.get("reason") == "balance_minimum_discharge" for r in logged)
        sample = [r for r in logged if r["event"] == "pv_sample"][-1]
        assert sample["mode"] == "STOP" and sample["economic_stopped"] is False
        assert sample.keys() == SAMPLE_KEYS | PAUSE


async def test_config_context_is_sparse_inactive_silent_and_reestablished(ha, caplog):
    entry, pv, _ = await selected_pv(ha, "PV_MAXIMUM")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(5):
            await tick(pv)
    assert not any(r["event"] in ("pv_sample", "pv_config") for r in records(caplog))
    # The context event is emitted only by the existing eligible decision path.
    from test_pv_core import point

    from custom_components.wallbox_manager.pv_core import Decision

    d = Decision(point(), "FAST", "startup_hold", {})
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for h in (2, 2, 3, 3):
            pv.decision_event(d, powers=power_context_empty(), hysteresis=h)
    assert [
        r["soc_hysterese"] for r in records(caplog) if r["event"] == "pv_config"
    ] == [2, 3]
    await pv.settings.set("charging_profile", "PV_SURPLUS")
    with caplog.at_level(logging.INFO, logger=LOGGER):
        pv.decision_event(d, powers=power_context_empty(), hysteresis=3)
    assert len([r for r in records(caplog) if r["event"] == "pv_config"]) == 3


def power_context_empty():
    return dict.fromkeys(POWERS)


@pytest.mark.parametrize("kind", ["soc", "economic"])
async def test_logging_failure_cannot_block_required_zero_command(
    ha, monkeypatch, kind
):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        await tick(pv)
        assert station.amps > 0
        await power(station, 1380)
        if kind == "soc":
            pv.values["soc_speicher_aktuell"] = 47
        else:
            pv.budget.wh = 51
        monkeypatch.setattr(
            "custom_components.wallbox_manager.pv._LOGGER.info",
            Mock(side_effect=ValueError("broken log handler")),
        )
        pv.last_config_event = None
        await tick(pv)
        assert station.amps == 0
        before = len(station.profiles)
        await tick(pv)
        assert len(station.profiles) == before


async def test_h_context_after_diagnostics_disabled_and_enabled(ha, caplog):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        pv.values["soc_speicher_aktuell"] = 47
        await tick(pv)
        for level in (0, 2):
            ha.config_entries.async_update_entry(
                entry, options={**entry.options, "diagnostic_level": level}
            )
            await ha.async_block_till_done()
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            await tick(pv)
            await tick(pv)
        assert len([r for r in records(caplog) if r["event"] == "pv_config"]) == 1
