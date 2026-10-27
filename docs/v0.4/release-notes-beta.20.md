# Wallbox Manager v0.4.0-beta.20

Beta.20 is an **observability-only prerelease**. It packages independently
approved feature commit `f4df7ac34a7f87acc170ee008362a43c067e2e65` unchanged.
Manifest version: `0.4.0-beta.20`; annotated tag: `v0.4.0-beta.20`.
HACS uses the standard tagged GitHub source archive; no binary asset is required.

## Dedicated Home Assistant PV target SoC sensor

The new native integration sensor is enabled by default:

- German: **PV Ziel-SoC**; English: **PV Target SoC**.
- Unit `%`, state class `measurement`, no battery device class.
- Normal Home Assistant entity registry and Recorder/history support.
- Stable existing-helper unique ID design:
  `<entry_id>:pv:["<station>","<evse>","<connector>","effective_target_soc"]`.
  It belongs to the existing wallbox device. HA determines the actual entity ID.

The value is exactly the existing canonical
`PV.attributes()["effective_target_soc"]`. There is no separate target formula,
rounding or zero fallback. The existing Wallbox Manager card keeps using this
same target. The sensor is available for valid PV_MAXIMUM, PV_OPTIMUM and
PV_SURPLUS with a configured home battery. It is unavailable for NETZ,
battery-free PV_SURPLUS, invalid/missing mandatory target inputs and invalidated
profile/mapping contexts, until normal planning establishes a valid target.
Actual battery SoC is independent of target-formula validity.

Valid passive target planning continues with manager charging release OFF,
independently of OCPP connection, authority or transaction. The existing scheduler
and planner remain the only calculation path. No extra polling, sensor read,
forecast request, regulation calculation, timer or control worker is added.

Only exact target changes, availability transitions and normal initial
registration/recovery publish sensor state. There are no volatile power,
timestamp or station-connectivity attributes. Unchanged cycles, unrelated
telemetry and generation broadcasts produce no redundant HA state publication or
Recorder activity. Continuous target history can therefore be compared with
actual home-battery SoC without recording unchanged regulation ticks.

## Extended compact pause diagnostics

At diagnostic Level 2, existing unchanged eligible STOP/zero-current decisions
keep the same `pv_sample` and add four fields:

| Field | Meaning |
| --- | --- |
| `soc_pct` | Observed home-battery SoC. |
| `target_soc_pct` | Canonical effective target T for that captured regulation tick. |
| `pv_surplus_proven` | Existing proven PV-start surplus evidence. Missing evidence never becomes proven surplus. |
| `economic_stopped` | Existing economic STOP provenance for that tick. |

This covers SoC STOP, minimum-positive-point BALANCE STOP and eligible zero-current
holds. An economic STOP can instead hold zero amps while the underlying SoC mode
remains FAST or BALANCE. The economic boolean distinguishes that independent
inhibition. Battery-free PV_SURPLUS retains its existing behavior: SoC, target
and battery-policy surplus proof are `null`, while economic STOP stays boolean.

The five original powers remain: `ev_actual_w`, `grid_w`, `pv_w`, `battery_w`,
`household_w`. Grid positive means import; battery positive means charging;
household demand excludes the selected EV. Existing signed-storage normalization
and missing-value `null` semantics remain unchanged. All measurement/SoC/target
values come from the same captured tick, including across persistence awaits.
This does not claim physically simultaneous Fronius/OCPP source sampling.

Changed semantic decisions still produce the complete `pv_decision`, with their
existing reasons, thresholds and point/mode context. No extra sample is emitted
for that same decision. Positive-current FAST/BALANCE compact samples retain their
original eleven-key schema. Target changes remain separate `pv_target` events;
measurement digits do not create duplicate full decisions.

There is **no additional cyclic diagnostic stream**. At five seconds the existing
maximum remains about 720 decision-related records/hour during continuously
eligible active PV regulation, plus independent target/input transitions and
sparse configuration context. Release OFF, passive planning, NETZ and contexts
without eligible decisions emit no cyclic samples. Levels 0/1 remain unchanged;
Level 3 inherits the same records. Logging failures cannot affect control.

## Sparse hysteresis configuration context

A `pv_config` event records `soc_hysterese` (H) only when establishing an eligible
Level-2 diagnostic context or when captured H actually changes. Existing
profile/release/generation/transaction lifecycle, reload and disabling/re-enabling
diagnostics re-establish context. Inactivity does not create a periodic stream;
configuration changes are recorded at the next eligible decision.

H is not repeated in every compact sample. Neither are these derived fields:

- `soc_stop_pct`
- `balance_restart_pct`
- `fast_restart_pct`

The existing calculations and comparisons are unchanged: mandatory SoC STOP at
`SoC < T-H`, STOP→BALANCE strictly at `SoC > T+H/2` with proven PV surplus,
STOP→FAST inclusively at `SoC >= T+H`. Economic STOP/restart remains independent.

## Unchanged control and compatibility

The new sensor is read-only observability and does not participate in charging
control. FAST/BALANCE selection, operating-point regulation and timing,
PV_OPTIMUM mathematics, SoC STOP/restart, economic import budget and restart,
phase/current restrictions, OCPP dispatch and authority, manager charging release,
Fronius input semantics and all default parameters remain unchanged from beta.19.
No additional measurement/polling, battery-discharge learning, physical protection,
configuration migration or persistence schema is introduced. No Stationary or
Fronius integration update is part of beta.20.

The seven defaults remain 3 cycles increase interval, 10 cycles startup hold,
100 W grid deadband, 0 cycles decrease delay, 50 Wh import budget, 50% EV uptake
and 500 W FAST restart household threshold. Existing configured values, mappings,
entities, dependencies, V3 journal and economic STOP/Wh history are preserved.
Existing user settings, including a separately configured two-cycle decrease
delay, are not changed by this release.

The operator's 27 October PV_OPTIMUM analysis motivated the added visibility:
dynamic targets changed as expected, SoC STOP was correct, and battery recovery
did not reach the moving restart threshold within the recorded period. The
reported Fronius inverter restart was an external hardware event, not a WBM
regulation fault. This release performs no new hardware test or optimization.

## HACS upgrade and rollback

These are instructions for a **separately authorized operator action**; GitHub
publication performs none of them.

For an authorized upgrade from beta.19, select prerelease **v0.4.0-beta.20** in
HACS and use the normal operator-controlled installation/restart process. Keep
manager release OFF, take the normal full pre-upgrade HA backup, preserve source
mappings, settings and journals, and verify installed manifest **0.4.0-beta.20**.

**The existing WBM safety behavior resets manager charging release to OFF after
integration reload/restart. The operator must explicitly authorize charging again
if required.** This behavior is unchanged. Stationary retains physical and
electrical safety ownership; the sensor grants no charging permission.

The new sensor registers automatically; HA controls its actual entity ID. Existing
entities are preserved. No new mandatory source mapping or dashboard change is
required to retain functionality. Optionally add the sensor and actual battery
SoC to an HA history graph. The existing Lovelace card continues using the same
canonical target; its JavaScript is unchanged from beta.19.

There is no beta.19-to-beta.20 persistence migration. A separately controlled
package-only rollback to beta.19 restores the previous entity implementation and
diagnostics; beta.19 no longer provides the new target sensor. Do not delete its
Recorder history or entity registry entry as an automatic rollback side effect.
Retain PV V3 journals and economic budget/STOP provenance; do not erase journals
or replay saved charging intent. Preserve the
[beta.17 backup/migration/rollback precautions](release-notes-beta.17.md#hacs-upgrade-and-rollback)
for earlier downgrades: a package-only downgrade to beta.16 does not restore
retired configuration or interpret V3 economic history. The
[beta.19 notes](release-notes-beta.19.md) and
[beta.18 notes](release-notes-beta.18.md) retain their historical scopes.

Publication establishes no physical charging acceptance. Installation, HACS
operations, HA reload/restart, live configuration, manager release/authority,
OCPP charging commands, Stationary/Fronius changes and inverter/battery/wallbox
operations are excluded. The operator installs and validates hardware separately.

## Evidence

- [Release validation report](release-validation-beta.20.md)
- [Approved feature implementation, examples and source comparison](../pv-target-sensor-pause-diagnostics.md)
- [PV target sensor specification](../specifications/pv-target-soc-sensor.md)
- [Pause diagnostic specification](../specifications/pv-decision-diagnostics.md#approved-pause-context-extension--27-october-2026)
- [Requirements status and release authorization](../specifications/README.md)
