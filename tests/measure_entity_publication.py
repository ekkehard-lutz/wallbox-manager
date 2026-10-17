"""Deterministic benchmark; runnable unchanged against beta.16 and this branch.

Run explicitly with pytest -q -s tests/measure_entity_publication.py.
Uses seven common control projections; this is a work counter, not a CPU estimate.
"""

import json
from unittest.mock import Mock

from test_control_runtime import manual as manual
from test_ha_observations import observation
from test_ocpp21_control import connected as connected
from test_pv_beta12 import authority
from test_pv_optimum import setup_optimum
from test_pv_surplus import base_grid as base_grid
from test_pv_surplus import grid as grid

from custom_components.wallbox_manager import profiles, pv_soc
from custom_components.wallbox_manager.control_entity import ControlEntity
from custom_components.wallbox_manager.core.authority import ControlAuthority
from custom_components.wallbox_manager.core.telemetry import Quantity


async def test_measure_two_thousand_reports(grid, monkeypatch):
    p, target, (control, bound, peer, *_) = grid
    setup_optimum(p, target, actual=0)
    authority(control, target, ControlAuthority.LOCAL)
    p.changed(control.runtime.get(target.station))
    await p.hass.async_block_till_done()
    entities = [
        ControlEntity(control, p.entry_id, target, f"counter_{i}") for i in range(7)
    ]
    unsubscribe, writes, telemetry = [], [], []
    for entity in entities:
        entity.hass = p.hass
        if hasattr(entity, "session_view"):
            entity._session_context = entity.session_view()
        write = Mock(side_effect=lambda entity=entity: entity.extra_state_attributes)
        monkeypatch.setattr(entity, "async_write_ha_state", write)
        notification = Mock(wraps=entity._telemetry)
        writes.append(write)
        telemetry.append(notification)
        options = (
            {"select": entity.runtime_view} if hasattr(entity, "runtime_view") else {}
        )
        unsubscribe.extend(
            (
                control.runtime.subscribe(notification, **options),
                control.runtime.sessions.subscribe(entity._session_changed),
                control.subscribe(entity._changed),
            )
        )
    setting = Mock(wraps=p.setting)
    clamp = Mock(wraps=profiles.clamp_settings)
    bounds = Mock(wraps=pv_soc.target_bounds)
    monkeypatch.setattr(p, "setting", setting)
    monkeypatch.setattr(profiles, "clamp_settings", clamp)
    monkeypatch.setattr(pv_soc, "target_bounds", bounds)
    before = list(peer.operations)
    for _ in range(2000):
        control.runtime.observe(bound.token, (observation(target, Quantity.POWER, 0),))
    await p.hass.async_block_till_done()
    print(
        json.dumps(
            {
                "reports": 2000,
                "control_telemetry": sum(m.call_count for m in telemetry),
                "control_attribute_builds": sum(m.call_count for m in writes),
                "setting": setting.call_count,
                "clamp_settings": clamp.call_count,
                "target_bounds": bounds.call_count,
                "ocpp_operations": len(peer.operations) - len(before),
            },
            sort_keys=True,
        )
    )
    assert peer.operations == before
    for remove in unsubscribe:
        remove()
    for entity in entities:
        entity._cancel_expiry()
