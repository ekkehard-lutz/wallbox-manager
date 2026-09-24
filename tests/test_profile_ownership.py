"""Two real OCPP peers exercise the installation-wide authority/permission guard."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from test_control_authority import authority
from test_control_runtime import manual
from test_ocpp21_control import connected

from custom_components.wallbox_manager.battery import BatteryReserve
from custom_components.wallbox_manager.control.commands import CommandStatus
from custom_components.wallbox_manager.core.authority import (
    AuthorityObservation,
    ControlAuthority,
)
from custom_components.wallbox_manager.ownership import ProfileOwnership, identity
from custom_components.wallbox_manager.profiles import GridProfiles


@pytest.fixture
async def site(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    owner = ProfileOwnership(hass)
    await owner.load()
    peers = []
    async with AsyncExitStack() as stack:
        for entry_id in ("A", "B"):
            connection = await stack.enter_async_context(
                asynccontextmanager(connected.__wrapped__)()
            )
            primitive = await manual.__wrapped__(connection)
            control, bound, peer, wire = await stack.enter_async_context(
                asynccontextmanager(authority.__wrapped__)(primitive)
            )
            entry = SimpleNamespace(entry_id=entry_id, options={}, subentries={})
            remove = owner.register(entry, control)
            stack.callback(remove)
            battery = BatteryReserve(hass, entry)
            profiles = GridProfiles(hass, entry, control, battery)
            control.profiles = profiles
            await profiles.load()
            stack.push_async_callback(profiles.close)
            control.restore(bound.target, target_w=2300)
            peers.append(
                (control, bound, peer, profiles, identity(entry_id, bound.target))
            )
        yield owner, peers, hass
    await hass.async_stop()


async def test_single_takeover_off_before_second_user_action(site):
    owner, peers, _ = site
    control, bound, peer, profile, key = peers[0]
    peer.enabled = True
    assert owner.active_wallbox is None
    assert (
        await control.request_enabled(bound.target, True)
    ).status != CommandStatus.APPLIED
    assert not peer.operations
    assert (
        await control.take_control(bound.target.station)
    ).status == CommandStatus.APPLIED
    assert owner.active_wallbox == key and owner.ready
    assert peer.operations == [
        "authority_set",
        "authority_get",
        "enabled_get",
        "permission",
        "enabled_get",
    ]
    assert not peer.requests and peer.enabled is False
    await profile.permission(bound.target, True)
    assert peer.enabled is True


async def test_switch_orders_offs_and_preserves_settings(site):
    owner, peers, _ = site
    a, b = peers
    ac, ab, ap, ag, ak = a
    bc, bb, bp, bg, bk = b
    await ag.set_value(ab.target, "power_kw", 4)
    await bg.set_value(bb.target, "power_kw", 7)
    await owner.activate(ak)
    await ag.permission(ab.target, True)
    events = []
    old_off, new_take = ac.request_enabled, bc._take_control

    async def stop(target, enabled, **kwargs):
        result = await old_off(target, enabled, **kwargs)
        assert ac.runtime.enabled(target) is False
        events.append("A_off_confirmed")
        return result

    async def take(station):
        assert events == ["A_off_confirmed"]
        assert not owner.ready and owner.transition
        result = await new_take(station)
        assert bc.runtime.enabled(bb.target) is False
        events.append("B_off_confirmed")
        return result

    ac.request_enabled, bc._take_control = stop, take
    assert (await owner.activate(bk)).status == CommandStatus.APPLIED
    assert events == ["A_off_confirmed", "B_off_confirmed"]
    assert owner.active_wallbox == bk and owner.ready
    assert not ap.enabled and not bp.enabled
    assert ag.setting(ab.target)["power_kw"] == 4
    assert bg.setting(bb.target)["power_kw"] == 7
    count = len(ap.operations)
    for call in (
        ag.permission(ab.target, True),
        ac.change(ab.target, allowed=True),
        ac.request_enabled(ab.target, True),
    ):
        assert (await call).status != CommandStatus.APPLIED
    assert len(ap.operations) == count
    assert not ag.tasks


@pytest.mark.parametrize(
    "failure", ["old_off", "new_authority", "new_off", "old_unknown"]
)
async def test_failed_switch_never_commits_new_owner(site, failure):
    owner, (a, b), _ = site
    ac, ab, ap, ag, ak = a
    bc, bb, bp, _, bk = b
    await owner.activate(ak)
    await ag.permission(ab.target, True)
    if failure == "old_off":
        ap.enabled_read_value = "true"
    elif failure == "new_authority":
        bp.authority_status = "Rejected"
    elif failure == "new_off":
        bp.enabled_read_value = "true"
    else:
        ac.runtime.disconnect(ab.token)
    assert (await owner.activate(bk)).status != CommandStatus.APPLIED
    assert owner.active_wallbox == ak and not owner.ready
    assert not bc.profile_permitted(bb.target)
    assert (await bc.request_enabled(bb.target, True)).status != CommandStatus.APPLIED
    assert not (ap.enabled and bp.enabled)


async def test_local_old_wallbox_is_never_reacquired_or_stopped(site):
    owner, (a, b), _ = site
    ac, ab, ap, _, ak = a
    await owner.activate(ak)
    ac.runtime.observe_authority(
        ab.token,
        AuthorityObservation(
            ab.target.station, ControlAuthority.LOCAL, datetime.now(UTC), "physical"
        ),
    )
    ap.enabled = True
    await ab.read_enabled()
    before = list(ap.operations)
    await owner.activate(b[4])
    assert ap.operations == before and ap.enabled
    assert owner.active_wallbox == b[4]


async def test_reload_restores_identity_not_takeover_or_permission(site):
    owner, (a, _), hass = site
    await owner.activate(a[4])
    before = list(a[2].operations)
    restored = ProfileOwnership(hass)
    await restored.load()
    assert restored.active_wallbox == a[4] and not restored.ready
    assert a[2].operations == before


async def test_removing_active_entry_blocks_unsafe_replacement(site):
    owner, (a, b), _ = site
    await owner.activate(a[4])
    owner.entries.pop("A")
    owner.changed()
    assert not owner.ready
    assert (await owner.activate(b[4])).status != CommandStatus.APPLIED
    assert owner.status == "previous_wallbox_unavailable"


async def test_only_active_profile_owns_battery(site):
    owner, (a, b), _ = site
    for _control, _bound, _, profile, _key in (a, b):
        profile.active = lambda target: True
        profile.battery.update = AsyncMock()
    await owner.activate(a[4])
    await a[3].permission(a[1].target, True)
    await a[3].reconcile_battery()
    a[3].battery.update.assert_awaited_with(20)
    await b[3].reconcile_battery()
    b[3].battery.update.assert_awaited_with(None)
    await owner.activate(b[4])
    a[3].battery.update.assert_awaited_with(None)


async def test_switch_fences_inflight_old_power(site):
    owner, (a, b), _ = site
    ac, ab, ap, ag, ak = a
    await owner.activate(ak)
    await ag.permission(ab.target, True)
    ap.received.clear()
    ap.release.clear()
    pending = asyncio.create_task(ac.change(ab.target, target_w=3500))
    await asyncio.wait_for(ap.received.wait(), 1)
    switch = asyncio.create_task(owner.activate(b[4]))
    await asyncio.sleep(0)
    ap.release.set()
    assert (await pending).status != CommandStatus.APPLIED
    assert (await switch).status == CommandStatus.APPLIED
    assert owner.active_wallbox == b[4]
    assert not ap.enabled and not b[2].enabled


async def test_ready_and_persistence_wait_for_off_readback(site, monkeypatch):
    from custom_components.wallbox_manager.protocols.ocpp.v21.adapter import (
        EvseControlAdapter,
    )

    owner, (a, _), _hass = site
    control, bound, peer, _, key = a
    peer.enabled = True
    entered, release = asyncio.Event(), asyncio.Event()
    original = EvseControlAdapter.read_enabled

    async def delayed(adapter):
        if adapter.target == bound.target and peer.permissions:
            entered.set()
            await release.wait()
        return await original(adapter)

    monkeypatch.setattr(EvseControlAdapter, "read_enabled", delayed)
    pending = asyncio.create_task(owner.activate(key))
    await asyncio.wait_for(entered.wait(), 1)
    assert owner.active_wallbox is None and not owner.ready
    assert (await owner.store.async_load()) is None
    assert not control.profile_permitted(bound.target)
    assert not peer.requests
    release.set()
    assert (await pending).status == CommandStatus.APPLIED
    assert (await owner.store.async_load())["active_wallbox"] == key
    assert peer.enabled is False and owner.ready


async def test_profile_selection_and_background_events_never_take_authority(site):
    owner, (a, _), _hass = site
    control, bound, peer, profile, _key = a
    await profile.select(bound.target, "NETZ")
    await profile.set_value(bound.target, "power_kw", 3)
    control.runtime._publish(control.runtime.get(bound.target.station))
    await profile.reconcile_battery()
    assert not peer.authority_requests
    assert owner.active_wallbox is None
