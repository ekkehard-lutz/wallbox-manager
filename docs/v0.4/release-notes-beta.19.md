# Wallbox Manager v0.4.0-beta.19

Beta.19 is a **diagnostic observability and minor Lovelace presentation release**.
It packages independently approved feature commit
`29d19eacf4dd24c468ed818338da4cb56ea9a799` without changing that implementation.
Manifest version: `0.4.0-beta.19`; annotated tag: `v0.4.0-beta.19`.
HACS uses the standard tagged GitHub source archive; no binary asset is needed.

## Full transitions or compact active-cycle context

At diagnostic Level 2, each existing eligible active PV regulation decision emits
exactly one decision-related INFO record:

- A changed semantic signature retains the complete **`pv_decision`**, including
  reason, mode/profile, relevant operating points/thresholds and five powers.
- An unchanged signature emits **`pv_sample`**, containing exactly `event`,
  `station`, `evse`, `connector`, `profile`, `mode` and the following five fields.
  Reason, thresholds, point structures and other verbose metadata are omitted.

| Field | Canonical W source and meaning |
| --- | --- |
| `ev_actual_w` | Actual measured selected EV consumption from canonical OCPP/session observations. |
| `grid_w` | Mapped grid import minus export; positive import, negative export. |
| `pv_w` | Total mapped PV production, positive generation. |
| `battery_w` | Existing storage charge minus discharge normalization; positive charge, negative discharge; both-positive channels yield effective zero. |
| `household_w` | `max(0, leistung_verbraucher - ev_actual_w)`, excluding selected EV under the Fronius total-consumption contract. |

Unavailable measurements stay JSON `null`, never fabricated zero. Both grid or
storage channels are required for their signed value; household needs total
consumption and actual selected EV power. Values use the same already captured
decision snapshot across persistence awaits and existing last-value-held semantics.
Silence does not expire observations. A snapshot does not guarantee physically
simultaneous Fronius/OCPP acquisition.

The rule applies uniformly to PV_MAXIMUM, PV_OPTIMUM and PV_SURPLUS with or
without a battery, wherever the existing regulation contract reaches a valid
decision. Response pending, startup hold, increase/decrease delays and repeated
**valid STOP decisions while manager release remains ON** follow the same rule.
Full and compact output are mutually exclusive for that decision; independent
significant target/input events can still occur in the same cycle.

Release OFF, inactive/unavailable contexts without a valid decision, NETZ and
passive target planning do **not** generate cyclic PV samples. Existing
change-driven target/input diagnostics remain. At the existing **5-second**
regulation interval, unchanged eligible operation can produce up to approximately
**720 compact samples per hour**. This is intentional log volume, not a new sensor
acquisition rate, fixed heartbeat or measurement callback stream.

Levels 0/1 remain unchanged and do not emit these Level-2 records. Level 3 includes
the same records without another periodic stream. No new polling, scheduler,
regulator, OCPP request/command, persistence or Recorder publication is added.
Logging errors cannot affect charging control. See the
[approved diagnostics specification](../specifications/pv-decision-diagnostics.md).

## Home Battery card cleanup

The card now shows the **Hausspeicher / Home battery** heading directly above
`63,3 % → 40,0 %` / `63.3 % → 40.0 %`. The redundant visible battery
**Ladezustand / State of charge** caption and its element are removed.
The separate upper **Ladezustand / Charging state** caption remains.

Locale-aware one-decimal values, arrow, profile-dependent target/reference,
independent unavailable sides, accessible actual/reference label and tooltip,
responsive desktop/mobile alignment and all charging controls remain unchanged.
See [WBM-HS-UI-02](../specifications/lovelace-home-battery-display.md).

## Unchanged control and compatibility

FAST/BALANCE mathematics, shared SoC transitions, PV_OPTIMUM planning, technical
phase/current point selection, grid import Wh accounting and economic STOP/restart,
NETZ, OCPP authority/release and held metering remain unchanged from beta.18.
Stationary and installed inverter/battery hardware retain physical safety.
No new timeout, filter, feedback loop, discharge cap or safety layer is added.

All seven PV parameter defaults remain **3 cycles** increase interval,
**10 cycles** startup hold, **100 W** grid deadband, **0 cycles** decrease delay,
**50 Wh** import budget, **50%** EV uptake and **500 W** FAST restart household
threshold. Existing user settings, source mappings, entity identities, dependencies,
HA configuration and journal schemas are preserved. There are no new migrations,
entities or mandatory measurements. Beta.18 settings and journals remain compatible.

**Operator-reported hardware experiment:** `pv_decrease_delay_cycles=2` is already
configured for a separately supervised FAST test. It is **not a beta.19 default**.
This release preserves that configured value and all other settings and performs
no live configuration change. The shared parameter also affects BALANCE. Future
FAST optimization remains open pending the new full-cycle measurement analysis;
no optimization algorithm is included here.

## HACS upgrade and rollback

The following are instructions for a **separately authorized operator action**;
GitHub publication performs none of them.

For an authorized upgrade from beta.18, select prerelease **v0.4.0-beta.19** in
HACS and use the normal operator-controlled HA restart. Verify manifest version
**0.4.0-beta.19** and preserve mappings, settings (including the configured two-cycle
delay), journals and economic STOP/Wh history. Because the JavaScript card changes,
check that every browser/app loads the current `/wallbox_manager/wallbox-manager-card.js`
resource rather than an older cached version. Any browser cache intervention is
separate from publication and is not performed by this task.

There is no beta.18-to-beta.19 migration; a separately managed package rollback
to beta.18 uses the same schemas. Do not erase journals or replay saved charging
intent. For earlier versions retain the
[beta.17 backup/migration/rollback precautions](release-notes-beta.17.md#hacs-upgrade-and-rollback);
a package-only downgrade to beta.16 does not restore retired keys or interpret V3
history. Beta.18 rollback returns to sparse-only unchanged-decision suppression
and restores the older battery caption.

The operator will install separately and capture PV_MAXIMUM FAST and BALANCE
measurements. Automated success does not establish physical EV/inverter/battery
response, simultaneous sampling, full station bootstrap or live HA acceptance.
No HA/HACS installation, reload/restart, cache intervention, charging command,
authority/release change, live WBM setting change, Stationary operation or
inverter/battery action is part of this release workflow.

## Evidence

- [Release validation report](release-validation-beta.19.md)
- [Reviewed implementation, examples and source inspection](../pv-cycle-diagnostics-card-cleanup.md)
- [PV diagnostic requirements](../specifications/pv-decision-diagnostics.md)
- [Home Battery requirements](../specifications/lovelace-home-battery-display.md)
- [Requirements status](../specifications/README.md)
