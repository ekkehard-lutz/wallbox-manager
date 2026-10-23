# NETZ reserve recovery after a session ends during HA downtime

Focused correction on `feature/grid-fixed-profile`, starting at
`588d2ee51097745da0dd39bea5762cc9343b1e2a`. Requirements: WBM-GRID-15/16,
subject to WBM-ARCH-05–12. No Stationary, version, release or deployment change.

## Reproduced before changing production code

`tests/test_grid_battery_restart.py` creates actual NETZ charging through a
schema-valid localhost OCPP 2.1 peer and disposable Home Assistant instance.
The real number service changes reserve 20% to 40%, and the integration durably
stores the original/temporary reserve journal plus the active session. Unloading
and setting up the integration again creates new State/Grid/BatteryReserve objects
and loads both stores; it does not manually assign `recovering=True` or call
`resume()` to bypass the coordinator. The simulated station retains Remote,
ChargingEnabled=true, NETZ and its 3p/16 A offer.

After BootNotification, inventory and supported current-generation readbacks:

- An accepted `TransactionEvent(Ended)` for the persisted transaction confirms
  `ended_at` in the current generation, but the reserve remains 40%.
- An accepted bound `StatusNotification(Available)` confirms physical vehicle
  absence, but the reserve also remains 40%.

Before the correction, the new restart suite produced **2 failures / 6 passes**:
both restoration tests timed out waiting for 40% to return to 20%. Ongoing
charging and ambiguous/missing/paused-session controls passed. Production code
was unchanged for that run at the specified starting HEAD.

Cause: `GridBattery.update()` would release its startup recovery gate only for
confirmed charging, Local authority, permission OFF or another profile. Actual
transaction termination/vehicle absence were absent from that decision, and
connector status was not directly subscribed. Silence and a pause correctly did
not release recovery, but neither did authoritative session-end evidence.

## Minimal correction

Only `grid_battery.py` production code changes:

1. Subscribe directly to the canonical bound connector-state key. This matters
   when Available does not cause a separate charging-state publication.
2. Release pending recovery after accepted registration and either a
   current-generation Available connector value or a current-generation ended
   session matching the bound connector. Continue through the existing `resume()`
   and `update(None)` compare-before-restore, number-service readback and durable
   journal cleanup. Do not add another restoration path, timer or OCPP command.

A missing session, historical ended session, missing/unknown/occupied connector,
pre-registration Available report or SuspendedEV/zero-power pause is not end
proof. Unrelated measurements need not all initialize once authoritative end
proof exists. Normal ongoing charging still adopts the journal without a
40→20→40 oscillation. External changes win; failed restoration retains the journal
and existing entity-recovery handling retries after availability returns.

## Regression and quality checks

The restart tests verify current-generation end and physical absence, ongoing
charging, incomplete initialization, occupied/unknown connector, charging pause,
historical end, pre-registration absence, external reserve ownership and failed
restoration followed by entity recovery. They assert persisted journal retention
or deletion after asynchronous work finishes, and no additional OCPP profile,
authority or permission writes after restart.

Validation results:

| Check | Result |
| --- | --- |
| Full maintained pytest | **255 passed**, 33.10 s; 5 existing dependency deprecation warnings |
| Ruff lint and formatting | Passed |
| JavaScript syntax and card suite | Passed, **18 card tests** |
| Manifest JSON and translation-key parity | Passed |
| Git whitespace checks | Passed |

These are simulated software checks; no installed HA or hardware was operated.

## Reference cross-check

Historical WBM `v0.3.2-beta.18`
(`4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`),
`custom_components/wallbox_manager/battery.py`: the existing
compare-before-restore/journal algorithm is retained unchanged.

Actual `openWB/core` revision
`ed5f7038c5c9be525e05429d3a82ec7571680bf7`,
`packages/modules/common/evse.py`, especially EvseStatusCode and
get_plug_charge_state: vehicle presence and charging are separate properties;
EV_PRESENT is plugged but not charging, READY is unplugged, and FAILURE does not
fabricate either boolean. This supports retaining the distinction between a
charging pause and confirmed physical absence. No openWB regulator or hardware
logic was imported, and no cross-repository defect was demonstrated.
