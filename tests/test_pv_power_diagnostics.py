"""Five-value decision context, snapshot causality and inert logging replay."""

import json
import logging
from unittest.mock import Mock

import pytest
from test_grid_profile import GridStation
from test_protocol import peer
from test_pv_runtime import install, power, release, tick

from custom_components.wallbox_manager.pv import power_context

POWERS = {"ev_actual_w", "grid_w", "pv_w", "battery_w", "household_w"}
LOGGER = "custom_components.wallbox_manager.pv"


def records(caplog):
    def unique(pairs):
        result = dict(pairs)
        assert len(result) == len(pairs), "duplicate JSON keys"
        return result

    return [
        json.loads(r.getMessage().split("subsystem=pv ")[1], object_pairs_hook=unique)
        for r in caplog.records
        if r.name == LOGGER
    ]


@pytest.mark.parametrize(
    "imported,exported,charge,discharge,load,ev,grid,battery,house",
    [
        (294, 0, 0, 1700, 2600, 2200, 294, -1700, 400),
        (0, 430, 800, 0, 2600, 2200, -430, 800, 400),
        (0, 0, 10, 20, 100, 200, 0, 0, 0),
        (None, 0, None, 0, 400, 0, None, None, 400),
        (0, None, 0, None, None, 0, None, None, None),
        (0, 0, 0, 0, 400, None, 0, 0, None),
    ],
)
def test_canonical_power_signs_and_missing_values(
    imported, exported, charge, discharge, load, ev, grid, battery, house
):
    context = power_context(
        dict(
            grid_import_power=imported,
            grid_export_power=exported,
            storage_charge_power=charge,
            storage_discharge_power=discharge,
            leistung_verbraucher=load,
        ),
        ev,
    )
    assert context == dict(
        ev_actual_w=ev, grid_w=grid, pv_w=None, battery_w=battery, household_w=house
    )
    assert json.loads(json.dumps(context)) == context


@pytest.mark.parametrize("profile", ["PV_MAXIMUM", "PV_OPTIMUM", "PV_SURPLUS"])
async def test_target_decision_stop_restart_and_mode_context(ha, caplog, profile):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station, profile=profile)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            pv.last_target_event = None
            pv.values.update(
                soc_speicher_aktuell=99,
                grid_import_power=43,
                grid_export_power=0,
                leistung_pv=5000,
                leistung_verbraucher=400,
                storage_charge_power=0,
                storage_discharge_power=1000,
            )
            await tick(pv)
            await power(station, 1380)
            pv.core.startup = None
            pv.budget.wh = 50
            await tick(pv)
            await power(station, 0)
            pv.budget.interrupt()
            now = [0]
            pv.monotonic = lambda: now[0]
            pv.values.update(
                grid_import_power=0, storage_charge_power=1, storage_discharge_power=0
            )
            await tick(pv)
            now[0] = 16
            await tick(pv)
            pv.values["soc_speicher_aktuell"] = 0
            await tick(pv)
            # Explicit invalidation is an event outside the scheduled tick too.
            pv.values["min_soc_speicher"] = None
            pv.invalidate_unavailable_target()
        logged = records(caplog)
        assert {"pv_target", "pv_decision"} <= {r["event"] for r in logged}
        assert {
            "import_budget",
            "restart_battery_charging",
            "soc_stop",
            "target_unavailable",
        } <= {r["reason"] for r in logged}
        for record in logged:
            assert POWERS <= record.keys()
        first = next(r for r in logged if r["event"] == "pv_decision")
        assert {key: first[key] for key in POWERS} == dict(
            ev_actual_w=0, grid_w=43, pv_w=5000, battery_w=-1000, household_w=400
        )
        assert first["mode"] == "FAST" and "previous_mode" in first


async def test_snapshot_survives_persistence_await_and_no_diagnostic_io(
    ha, caplog, monkeypatch
):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        pv.values.update(
            leistung_verbraucher=1700,
            leistung_pv=None,
            storage_charge_power=None,
            grid_import_power=43,
            grid_export_power=0,
        )
        await power(station, 1380)
        original = pv.persist

        async def changed_during_await():
            await original()
            pv.values.update(leistung_verbraucher=9999, leistung_pv=9999)

        monkeypatch.setattr(pv, "persist", changed_during_await)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            await tick(pv)
        decision = next(r for r in records(caplog) if r["event"] == "pv_decision")
        assert decision["household_w"] == 320
        assert decision["pv_w"] is None and decision["battery_w"] is None
        # Logging supplied context must touch no HA/OCPP/publication surfaces.
        before = len(station.profiles)
        with monkeypatch.context() as patch:
            patch.setattr(pv, "ev_power", Mock(side_effect=AssertionError("reread EV")))
            patch.setattr(
                type(ha.states), "get", Mock(side_effect=AssertionError("poll"))
            )
            patch.setattr(g, "publish", Mock(side_effect=AssertionError("Recorder")))
            patch.setattr(
                g.listener.adapter, "request", Mock(side_effect=AssertionError("OCPP"))
            )
            with caplog.at_level(logging.INFO, logger=LOGGER):
                pv.diagnostic(
                    "pv_decision",
                    "grid_import",
                    powers={key: decision[key] for key in POWERS},
                    grid_w=999,
                    ev_actual_w=999,
                )
        assert len(station.profiles) == before
        assert records(caplog)[-1]["grid_w"] == 43
        with caplog.at_level(logging.INFO, logger=LOGGER):
            pv.diagnostic(
                "pv_inputs",
                "grid_ev_power",
                powers=power_context(
                    {"unrestricted_attributes": "secret" * 10000}, None
                ),
            )
        assert {key: records(caplog)[-1][key] for key in POWERS} == dict.fromkeys(
            POWERS
        )
        assert "secret" not in caplog.records[-1].getMessage()


@pytest.mark.parametrize("soc", [50, 60])
async def test_logging_levels_have_identical_regulation_and_publications(
    ha, caplog, monkeypatch, soc
):
    from custom_components.wallbox_manager.pv_core import Controller

    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        g = pv.grid
        await tick(pv)
        await power(station, 1380)
        current = g.current_point()
        traces = []
        for level in (0, 2):
            ha.config_entries.async_update_entry(
                entry, options={**entry.options, "diagnostic_level": level}
            )
            pv.core = Controller()
            pv.core.mode = "BALANCE" if soc == 50 else "FAST"
            pv.last_event = None
            pv.values.update(soc_speicher_aktuell=soc, grid_export_power=0)
            submitted, published = [], []
            with monkeypatch.context() as patch:
                patch.setattr(g, "current_point", lambda: current)
                patch.setattr(
                    pv,
                    "submit",
                    lambda *args, submitted=submitted, **kw: submitted.append(
                        (args, kw)
                    ),
                )
                patch.setattr(
                    g, "publish", lambda published=published: published.append(True)
                )
                with caplog.at_level(logging.INFO, logger=LOGGER):
                    for imported, storage in [
                        (0, 0),
                        (0, 800),
                        (294, 0),
                        (260, -500),
                        (0, 0),
                    ] * 4:
                        pv.values.update(
                            grid_import_power=imported,
                            storage_charge_power=max(0, storage),
                            storage_discharge_power=max(0, -storage),
                        )
                        await tick(pv)
            traces.append((submitted, published))
        assert traces[0] == traces[1]
        for r in records(caplog):
            assert POWERS <= r.keys()
            assert len(json.dumps(r)) < 1500
            assert (
                not {"transaction_id", "sources", "attributes", "token", "url"}
                & r.keys()
            )


@pytest.mark.parametrize("imported", [260, 294])
def test_fast_observed_shape_and_existing_two_cycle_delay(imported):
    from test_grid_profile import ranges
    from test_pv_core import config, inputs, point

    from custom_components.wallbox_manager.pv_core import Controller

    # Synthetic measured EV power: the task excerpt does not supply its value.
    current = point(10)
    demand = inputs(ev=2200, imported=imported)
    immediate = Controller().decide(demand, config(), ranges(), current)
    assert immediate.reason == "grid_import" and immediate.point == point(8)
    core = Controller()
    settings = config(pv_decrease_delay_cycles=2)
    for _ in range(2):
        d = core.decide(demand, settings, ranges(), current)
        assert d.reason == "decrease_delay" and d.point == current
    d = core.decide(demand, settings, ranges(), current)
    assert d.reason == "grid_import" and d.point == point(8)
    # A transient that clears before the third detecting tick is not reduced.
    core = Controller()
    core.decide(demand, settings, ranges(), current)
    core.decide(inputs(ev=2200), settings, ranges(), current)
    assert core.down_cycles is None
    assert core.decide(demand, settings, ranges(), current).reason == "decrease_delay"
