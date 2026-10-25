"""Independent PV V3 review findings: reproductions and nearby recovery boundaries."""

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import eventually
from test_grid_profile import GridStation, ranges
from test_protocol import peer
from test_pv_core import config, inputs, point
from test_pv_runtime import install, release, tick

from custom_components.wallbox_manager.pv_core import Controller
from custom_components.wallbox_manager.pv_plan import Planner


async def journal(pv):
    def read():
        with open(pv.store.path, encoding="utf-8") as handle:
            return json.load(handle)["data"]

    return await pv.hass.async_add_executor_job(read)


def test_review_1_surplus_rejects_invalid_fixed_target():
    settings = config(soll_soc_speicher=50)
    now = datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Vienna"))
    with pytest.raises(ValueError):
        Planner().calculate("PV_SURPLUS", settings, 48.01, now, 0)
    assert settings["soll_soc_speicher"] == 50


async def test_review_2_invalid_forecast_clears_canonical_target(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station, profile="PV_OPTIMUM")
        await tick(pv)
        assert pv.target is not None
        pv.values["remaining_pv_energy"] = None
        count = len(station.profiles)
        await tick(pv)
        assert pv.attributes()["effective_target_soc"] is None
        assert len(station.profiles) == count


async def test_review_3_accepted_restart_checkpoints_without_another_tick(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station)
        await tick(pv)
        pv.budget.wh = 50
        await tick(pv)
        assert (await journal(pv))["economics"]["stopped"]
        pv.pending_restart = "battery_charging"
        pv.accepted(point(), point(0))
        if pv.checkpoints:
            await asyncio.gather(*pv.checkpoints)
        assert not (await journal(pv))["economics"]["stopped"]


def test_review_4_balance_bias_can_start_above_zero_ideal():
    core = Controller()
    d = core.decide(
        inputs(soc=51.5, ev=0, storage=0, surplus=True), config(), ranges(), point(0)
    )
    assert d.mode == "BALANCE"
    assert d.reason == "soc_discharge"
    assert d.point == point(6)


@pytest.mark.parametrize(
    "reserve,valid",
    [
        (0, True),
        (47.99, True),
        (48, True),
        (48.001, False),
        (49.25, False),
        (48, True),
        (47.999, True),
    ],
)
def test_fixed_target_full_precision_reserve_boundaries(reserve, valid):
    settings = config(soll_soc_speicher=50)
    now = datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Vienna"))
    planner = Planner()
    if valid:
        assert planner.calculate("PV_SURPLUS", settings, reserve, now, 0) == 50
    else:
        with pytest.raises(ValueError, match="surplus_target"):
            planner.calculate("PV_SURPLUS", settings, reserve, now, 0)
    assert settings["soll_soc_speicher"] == 50


@pytest.mark.parametrize("target,valid", [(97.999, True), (98, True), (98.001, False)])
def test_fixed_target_upper_bound_does_not_clamp(target, valid):
    settings = config(soll_soc_speicher=target)
    now = datetime(2026, 10, 25, 12, tzinfo=ZoneInfo("Europe/Vienna"))
    if valid:
        assert Planner().calculate("PV_SURPLUS", settings, 20, now, 0) == pytest.approx(
            target
        )
    else:
        with pytest.raises(ValueError):
            Planner().calculate("PV_SURPLUS", settings, 20, now, 0)
    assert settings["soll_soc_speicher"] == target


async def change_source(ha, entry, role, value):
    entity = entry.options[role]
    if value is None:
        ha.states.async_remove(entity)
    else:
        ha.states.async_set(entity, str(value), ha.states.get(entity).attributes)
    await asyncio.sleep(0)


async def test_fixed_target_reserve_changes_reject_and_recover_without_mutation(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        await tick(pv)
        for reserve, expected in [
            (48, 50),
            (48.01, None),
            (48, 50),
            (48.001, None),
            (20.5, 50),
        ]:
            count = len(station.profiles)
            await change_source(ha, entry, "min_soc_speicher", reserve)
            if expected is None:
                assert pv.target is None  # validity publication is event-fed
            await tick(pv)
            assert pv.target == expected
            if expected is None:
                assert len(station.profiles) == count
            assert g.settings.get("soll_soc_speicher") == 50
            assert g.settings.get("charge_release")
            assert (
                pv.state.value(g.listener.control.key("ControlAuthority")) == "remote"
            )


@pytest.mark.parametrize(
    "profile,role",
    [
        ("PV_SURPLUS", "min_soc_speicher"),
        ("PV_MAXIMUM", "min_soc_speicher"),
        ("PV_OPTIMUM", "min_soc_speicher"),
        ("PV_OPTIMUM", "remaining_pv_energy"),
        ("PV_OPTIMUM", "storage_capacity"),
    ],
)
@pytest.mark.parametrize("invalid", [None, "unavailable"])
async def test_target_dependency_invalidation_publication_sparsity_and_recovery(
    ha, caplog, profile, role, invalid
):
    import logging

    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station, profile=profile)
        await tick(pv)
        target = pv.target
        assert target is not None
        observed = []
        remove = pv.state.watch(
            ("grid", pv.state.binding, "charging_profile"),
            lambda: observed.append(pv.target),
        )
        entity = entry.options[role]
        original = ha.states.get(entity)
        caplog.clear()
        try:
            with caplog.at_level(
                logging.INFO, logger="custom_components.wallbox_manager.pv"
            ):
                count = len(station.profiles)
                await change_source(ha, entry, role, invalid)
                assert pv.target is None
                assert observed[-1] is None
                assert len(station.profiles) == count
                await tick(pv)
                publications, logs = len(observed), len(caplog.records)
                for _ in range(300):
                    await tick(pv)
                assert len(observed) == publications
                assert len(caplog.records) == logs
                assert len(station.profiles) == count
                assert g.settings.get("charge_release")
                ha.states.async_set(entity, original.state, original.attributes)
                await asyncio.sleep(0)
                await tick(pv)
                assert pv.target == target
        finally:
            remove()


@pytest.mark.parametrize("profile", ["PV_SURPLUS", "PV_MAXIMUM", "PV_OPTIMUM"])
async def test_soc_display_validity_is_independent_of_calculable_target(ha, profile):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station, profile=profile)
        await tick(pv)
        target, count = pv.target, len(station.profiles)
        await change_source(ha, entry, "soc_speicher_aktuell", "unknown")
        await tick(pv)
        assert pv.target == target
        assert len(station.profiles) == count
        await change_source(ha, entry, "soc_speicher_aktuell", 60)
        await tick(pv)
        assert pv.target == target


@pytest.mark.parametrize("profile", ["PV_SURPLUS", "PV_MAXIMUM", "PV_OPTIMUM"])
async def test_valid_held_target_survives_disconnect_and_invalid_ha_does_not(
    ha, profile
):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station, profile=profile)
        await tick(pv)
        target = pv.target
    await eventually(lambda: not pv.state.connected)
    await tick(pv)
    assert pv.target == target
    await change_source(ha, entry, "min_soc_speicher", "unavailable")
    assert pv.target is None
    await tick(pv)
    assert pv.target is None
    await change_source(ha, entry, "min_soc_speicher", 20)
    await tick(pv)
    assert pv.target == target


@pytest.mark.parametrize(
    "soc,previous,surplus,expected",
    [
        (48.5, "BALANCE", True, 0),
        (49, "BALANCE", True, 0),
        (50, "BALANCE", True, 0),
        (51, "BALANCE", True, 0),
        (51, "STOP", True, 0),
        (51.001, "STOP", True, 6),
        (51.5, "STOP", True, 6),
        (51.5, "STOP", False, 0),
        (51.5, "BALANCE", False, 6),
    ],
)
def test_zero_ideal_start_boundaries_and_surplus_gate(soc, previous, surplus, expected):
    core = Controller()
    core.mode = previous
    result = core.decide(
        inputs(soc=soc, ev=0, storage=0, surplus=surplus), config(), ranges(), point(0)
    )
    assert result.point.amps == expected


@pytest.mark.parametrize("current", [point(6), point(12), point(6, 3)])
def test_zero_ideal_upward_bias_from_ongoing_point_is_one_step_above_zero(current):
    core = Controller()
    core.mode = "BALANCE"
    result = core.decide(inputs(soc=51.5, ev=0, storage=0), config(), ranges(), current)
    assert result.point == point(6)
    assert result.reason == "soc_discharge"


@pytest.mark.parametrize("gate", ["pending", "startup", "economic"])
def test_zero_ideal_bias_cannot_override_common_guards(gate):
    core = Controller()
    core.mode = "BALANCE"
    current = point(12) if gate == "startup" else point(0)
    if gate == "startup":
        core.sent(current, point(0))
    result = core.decide(
        inputs(soc=51.5, ev=0, storage=0, surplus=True),
        config(),
        ranges(),
        current,
        pending=gate == "pending",
        economic_stop=gate == "economic",
    )
    assert result.point.amps == (current.amps if gate == "startup" else 0)


async def test_zero_ideal_bias_uses_existing_guarded_ocpp_path(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        g, pv = await release(entry, station)
        pv.values.update(
            soc_speicher_aktuell=51.5, storage_charge_power=0, storage_discharge_power=0
        )
        await tick(pv)
        assert pv.core.mode == "BALANCE"
        assert station.amps == 6 and station.phases == 1
        assert g.confirmed == point(6)
        assert pv.core.startup == 0


async def stopped_journal(ha, entry, station):
    _, pv = await release(entry, station)
    await tick(pv)
    pv.budget.wh = 50
    await tick(pv)
    assert pv.budget.stopped
    return pv


async def wait_checkpoints(pv):
    if pv.checkpoints:
        await asyncio.gather(*pv.checkpoints, return_exceptions=True)
    await asyncio.sleep(0)  # run completion callbacks, including cancellation


async def test_accepted_restart_persists_before_reload_preserving_wh(ha):
    from custom_components.wallbox_manager import async_setup_entry, async_unload_entry

    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        pv = await stopped_journal(ha, entry, station)
        pv.budget.wh = 2.5
        pv.pending_restart = "battery_charging"
        pv.accepted(point(), point(0))
        await wait_checkpoints(pv)
        saved = (await journal(pv))["economics"]
        assert not saved["stopped"] and saved["wh"] == 2.5
    await async_unload_entry(ha, entry)
    await async_setup_entry(ha, entry)
    restored = entry.runtime_data.pv
    assert not restored.budget.stopped and restored.budget.wh == 2.5
    assert restored.budget.previous is None
    assert not restored.settings.get("charge_release")


@pytest.mark.parametrize("failure", ["raised", "unverified"])
async def test_restart_checkpoint_failure_retains_stop_and_reports_once(
    ha, monkeypatch, caplog, failure
):
    import logging

    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        pv = await stopped_journal(ha, entry, station)

        async def fail(record):
            if failure == "raised":
                raise OSError("journal failure")
            # HA Store may swallow a write error: saved-file verification must
            # also reject a save that did not actually replace the old journal.
            return None

        with (
            monkeypatch.context() as patch,
            caplog.at_level(
                logging.INFO, logger="custom_components.wallbox_manager.pv"
            ),
        ):
            patch.setattr(pv.store, "async_save", fail)
            pv.budget.wh = 2.5
            pv.pending_restart = "battery_charging"
            pv.accepted(point(), point(0))
            await wait_checkpoints(pv)
            assert pv.budget.stopped and not pv.budget.reset
            assert pv.budget.wh == 2.5
            assert pv.input_problem == "journal_write_failed"
            assert (await journal(pv))["economics"]["stopped"]
            count = len(caplog.records)
            pv.checkpoint()
            await wait_checkpoints(pv)
            assert len(caplog.records) == count
        await pv.persist()
        saved = (await journal(pv))["economics"]
        assert saved["stopped"] and saved["wh"] == 2.5


@pytest.mark.parametrize("newer", ["economic_stop", "operator_stop", "transaction"])
async def test_queued_restart_checkpoint_cannot_overwrite_newer_authority(ha, newer):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        pv = await stopped_journal(ha, entry, station)
        await pv.journal_lock.acquire()
        pv.pending_restart = "battery_charging"
        pv.accepted(point(), point(0))
        if newer == "economic_stop":
            pv.budget.wh = 55
            pv.budget.exhaust(50)
            newer_write = asyncio.create_task(pv.persist())
        elif newer == "operator_stop":
            newer_write = asyncio.create_task(pv.grid.stop_release())
            await asyncio.sleep(0)
        else:
            pv.budget.bind([*pv.state.binding, "remote", "new-transaction"], ended=True)
            pv.budget.wh = 3
            pv.context = (*pv.context[:-1], "new-transaction")
            newer_write = asyncio.create_task(pv.persist())
        await asyncio.sleep(0)
        pv.journal_lock.release()
        await newer_write
        await wait_checkpoints(pv)
        saved = (await journal(pv))["economics"]
        if newer == "economic_stop":
            assert saved["stopped"] and saved["wh"] == 55
        elif newer == "operator_stop":
            assert saved["scope"] is None and saved["cause"] == "operator_stop"
        else:
            assert saved["scope"][-1] == "new-transaction" and saved["wh"] == 3


@pytest.mark.parametrize("newer", ["economic_stop", "operator_stop", "transaction"])
async def test_failed_old_restart_checkpoint_does_not_rollback_newer_state(
    ha, monkeypatch, newer
):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        pv = await stopped_journal(ha, entry, station)
        started, finish = asyncio.Event(), asyncio.Event()

        async def delayed_failure(record):
            started.set()
            await finish.wait()
            raise OSError("old checkpoint failed")

        with monkeypatch.context() as patch:
            patch.setattr(pv.store, "async_save", delayed_failure)
            pv.pending_restart = "battery_charging"
            pv.accepted(point(), point(0))
            await started.wait()
            if newer == "economic_stop":
                pv.budget.wh, pv.budget.stopped, pv.budget.cause = 55, True, "new_stop"
            elif newer == "operator_stop":
                from custom_components.wallbox_manager.pv_economics import Budget

                pv.budget = Budget(cause="operator_stop")
            else:
                pv.budget.bind(
                    [*pv.state.binding, "remote", "new-transaction"], ended=True
                )
                pv.budget.wh = 3
            expected = pv.budget.saved()
            finish.set()
            await wait_checkpoints(pv)
            assert pv.budget.saved() == expected
        await pv.persist()
        assert (await journal(pv))["economics"] == expected


async def test_cancelled_restart_write_drains_before_newer_stop_journal(
    ha, monkeypatch
):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        pv = await stopped_journal(ha, entry, station)
        started, finish = asyncio.Event(), asyncio.Event()
        original = pv.store.async_save

        async def delayed(record):
            if not record["economics"]["stopped"]:
                started.set()
                await finish.wait()
            await original(record)

        with monkeypatch.context() as patch:
            patch.setattr(pv.store, "async_save", delayed)
            pv.pending_restart = "battery_charging"
            pv.accepted(point(), point(0))
            await started.wait()
            checkpoint = next(iter(pv.checkpoints))
            checkpoint.cancel()
            await asyncio.sleep(0)
            assert pv.journal_lock.locked()
            pv.budget.wh = 55
            pv.budget.exhaust(50)
            newer = asyncio.create_task(pv.persist())
            finish.set()
            await wait_checkpoints(pv)
            await newer
        assert checkpoint.cancelled()
        saved = (await journal(pv))["economics"]
        assert saved["stopped"] and saved["wh"] == 55


async def test_cancelled_queued_restart_retains_conservative_stop(ha):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        pv = await stopped_journal(ha, entry, station)
        await pv.journal_lock.acquire()
        pv.pending_restart = "battery_charging"
        pv.accepted(point(), point(0))
        checkpoint = next(iter(pv.checkpoints))
        checkpoint.cancel()
        await wait_checkpoints(pv)
        assert pv.budget.stopped and pv.input_problem == "journal_write_failed"
        pv.journal_lock.release()
        await pv.persist()
        assert (await journal(pv))["economics"]["stopped"]


async def test_restart_journal_failure_does_not_fabricate_physical_off(ha, monkeypatch):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        pv = await stopped_journal(ha, entry, station)
        pv.budget.wh, pv.budget.reset = 0, True
        pv.values["storage_charge_power"] = 1
        original = pv.store.async_save

        async def fail_restart(record):
            if not record["economics"]["stopped"]:
                raise OSError("restart checkpoint failed")
            await original(record)

        with monkeypatch.context() as patch:
            patch.setattr(pv.store, "async_save", fail_restart)
            await tick(pv)
            await wait_checkpoints(pv)
            assert pv.budget.stopped
            assert pv.grid.acknowledged.amps == 6
            assert station.amps == 6  # journal failure did not fabricate physical OFF
        await tick(pv)
        assert station.amps == 0


async def test_late_restart_ack_after_operator_stop_never_clears_journal(ha):
    from ocpp.v21 import call, call_result

    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        pv = await stopped_journal(ha, entry, station)
        pv.budget.wh, pv.budget.reset = 0, True
        pv.values["storage_charge_power"] = 1
        adapter, g = pv.grid.listener.adapter, pv.grid
        original = adapter.request
        started, finish = asyncio.Event(), asyncio.Event()

        async def delayed(payload):
            if (
                isinstance(payload, call.SetChargingProfile)
                and payload.charging_profile["charging_schedule"][0][
                    "charging_schedule_period"
                ][0]["limit"]
                > 0
            ):
                started.set()
                await finish.wait()
                return call_result.SetChargingProfile(status="Accepted")
            return await original(payload)

        adapter.request = delayed
        await pv.tick()
        await started.wait()
        old = g.task
        await g.stop_release()
        finish.set()
        await old
        await wait_checkpoints(pv)
        saved = (await journal(pv))["economics"]
        assert saved["scope"] is None and saved["cause"] == "operator_stop"
        assert not g.settings.get("charge_release")


@pytest.mark.parametrize("profile", ["PV_SURPLUS", "PV_MAXIMUM"])
@pytest.mark.parametrize("role", ["remaining_pv_energy", "storage_capacity"])
async def test_unused_forecast_inputs_do_not_invalidate_other_targets(
    ha, profile, role
):
    entry = await install(ha)
    async with peer(entry, station_type=GridStation) as station:
        _, pv = await release(entry, station, profile=profile)
        await tick(pv)
        target = pv.target
        await change_source(ha, entry, role, "unavailable")
        await tick(pv)
        assert pv.target == target


def test_negative_ideal_is_not_new_zero_ideal_start_permission():
    core = Controller()
    result = core.decide(
        inputs(soc=51.5, ev=0, storage=-1, surplus=True), config(), ranges(), point(0)
    )
    assert result.point.amps == 0


@pytest.mark.parametrize("modes,expected", [((3,), point(6, 3)), ((2, 3), point(6, 2))])
def test_zero_ideal_bias_uses_actual_advertised_technical_lattice(modes, expected):
    core = Controller()
    result = core.decide(
        inputs(soc=51.5, ev=0, storage=0, surplus=True),
        config(),
        ranges(modes=modes),
        point(0),
    )
    assert result.point == expected
