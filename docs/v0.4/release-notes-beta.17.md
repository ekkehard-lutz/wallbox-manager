# Wallbox Manager v0.4.0-beta.17

This **release candidate** packages the reviewed PV Regulation V3 implementation
through `f6b834672128ae8c68500fc7eb8a1c3bd48d2aef`. It is prepared for final operator
approval, not yet tagged or published. The intended GitHub prerelease tag is
`v0.4.0-beta.17`; the integration manifest version is `0.4.0-beta.17`. HACS uses
the tagged repository source archive, without an additional integration ZIP.
Publication and supervised installation remain separate actions.

## Shared PV Regulation V3

- PV_SURPLUS, PV_OPTIMUM and PV_MAXIMUM share one runtime and one configurable
  scheduler, common SoC hysteresis and FAST/BALANCE operating modes. PV_SURPLUS
  retains its battery-free special case. With a battery, its saved fixed target
  must satisfy `reserve + hysteresis <= target <= 100 - hysteresis`; invalid
  targets become unavailable rather than being silently clamped. PV_MAXIMUM uses
  `reserve + hysteresis`. PV_OPTIMUM uses remaining-local-day forecast and
  household energy with approved lower/upper limits.
- Phase-aware navigation uses each enabled technical current series, including
  the 1p/6 A technical minimum, advertised station limits and existing phase-switch
  lockouts. Requested, accepted, confirmed and measured values remain distinct.
- Configurable increase cadence, startup hold, decrease delay and EV measured
  power-uptake threshold govern ordinary regulation. Existing `regulation_interval`
  remains independent, default **5 s**; cycle settings count eligible regulation
  steps, not seconds and not new physical measurement generations.
- Fronius storage **charging and discharging** power mappings supply signed battery
  power. BALANCE adds **no measurement smoothing**. Both-positive channel reports
  are neutralized internally, without changing HA source values. Old smoothing
  configuration is retained for compatibility but does not drive V3.
- The configurable grid-import energy budget defaults to **50 Wh**. Accounting
  is prospective and transaction-scoped; exhausted budgets retain economic STOP.
  The existing reset requires import at or below half the technical minimum power
  for strictly more than 15 eligible seconds. Recovery also requires confirmed
  zero offer, measured zero EV power and SoC permission. Positive battery charging
  permits restart; FAST additionally permits household load strictly below its
  configured threshold (default **500 W**). Battery-free SURPLUS uses its approved
  grid-surplus restart. There is no extra cooldown or restart timer.
- Level-2 diagnostics describe meaningful mode, point, target, STOP and restart
  transitions without emitting identical regulation messages every tick.

## Home battery status and continuous daily planning

The existing read-only home-battery block displays actual SoC and the canonical
backend effective target, with exactly one locale-aware decimal place. Missing
actual SoC and an unavailable target are independent `—` values. NETZ continues
to show the observed writable reserve, including confirmed temporary overrides.
The simplified beta.16 card layout and existing HA Number APIs remain.

A selected battery PV profile calculates its target immediately on setup and
continues on the **same scheduler while manager charging release is OFF**.
PV_OPTIMUM observes BEFORE_SURPLUS → DYNAMIC → FINISHED, changing forecasts,
roughly 300-second replanning, local midnight and DST, with durable day/phase
history. FINISHED stays latched until the next local day. Valid PV greater than
valid total consumption proves strict surplus even when EV power is unknown;
otherwise only genuinely observed EV power may be subtracted. Release OFF never
implies EV=0, and missing evidence never invents past surplus.

**Planning is not permission to charge.** Planning requires no OCPP connection,
transaction, Remote authority or enabled hardware, and creates no positive or
zero-current profile command. It exits before FAST/BALANCE control and economic
accounting while release is OFF. Start preserves the existing phase/target and
grants separate control intent; all authority, transaction, hardware and execution
guards still apply. Stop retains the existing authorized zero behavior while
planning continues. OCPP recovery does not replay planning-derived commands.

## Seven new integration parameters

Available in integration options and HA configuration Number entities. Existing
SoC hysteresis (default **2 percentage points**) and regulation interval remain
separate settings. Changes apply on the next normal cycle, without an extra
power-regulation pass.

| Parameter | Default / unit | Behavior |
| --- | --- | --- |
| `pv_increase_interval_cycles` | 3 cycles | Minimum eligible ordinary cycles between upward technical steps; range 1–720. |
| `pv_startup_hold_cycles` | 10 cycles | Hold the applied startup point before ordinary upward/downward adjustment; range 0–720. Mandatory STOP is not an ordinary ramp. |
| `pv_grid_deadband_w` | 100 W | Grid-power deadband for ordinary correction; range 0–10,000 W. |
| `pv_decrease_delay_cycles` | 0 cycles | Additional ordinary downward delay; zero means no extra delay; range 0–720. |
| `pv_grid_import_budget_wh` | 50 Wh | Economic import allowance; range 0.1–10,000 Wh. Editing it does not rescale or erase accumulated Wh. |
| `pv_ev_uptake_threshold_pct` | 50% | Required measured uptake of the confirmed nominal offer before an ordinary increase; range 0.1–100%. |
| `pv_fast_restart_household_load_limit_w` | 500 W | Additional FAST economic restart branch for household load strictly below this value; range 0–100,000 W. Missing household evidence disables this branch. |

## Compatibility and migration

- Existing config-entry bindings, integration domain, supported entity unique IDs,
  profile selections, ordinary saved settings, session/statistics identities and
  source mappings remain. New parameters receive their defaults unless explicitly
  configured. OCPP dependencies remain `ocpp==2.1.0` and `websockets==15.0.1`.
  New storage-charge mapping must be configured; it is not inferred from discharge.
- **`pv_start_delay` and `pv_stop_delay` are retired.** Setup removes their options,
  stored profile fields and exactly owned control registry entries. Their old
  seconds are not converted to cycle settings and their identities are not reused.
  No automatic recreation of deleted entities or Recorder histories is promised.
- **`optimum_max_discharge_w` remains as a deprecated compatibility Number** with
  its existing identity/value/API. It no longer imposes a discharge-power ceiling
  on PV V3 regulation. Legacy `power_smoothing_window` is retained but unused.
- V3 uses `wallbox_manager.<entry_id>.pv` in HA storage for versioned day/phase,
  Wh, transaction scope, STOP provenance and station identity. If absent, supported
  historical `.pv_economy` and `.pv_day` records are imported read-only; originals
  are left untouched. Recognized history retains Wh and STOP, including pending
  old OFF provenance. Malformed or mismatched economics inhibits recovery instead
  of granting a fresh allowance. Beta.16 itself has no V3 journal consumer.
- Reload restores durable day/phase and economic history, never counts downtime
  as import/reset credit, and never resumes saved charging release. Economic reset
  evidence must be re-established after downtime. Existing explicit Stop, NETZ
  scope exit and proven transaction-end rules still govern scope changes.
- Existing NETZ power/phase settings, delay/duration drafts, minimum storage reserve
  ownership and recovery journal remain. As before, restart/reload retires active
  NETZ deadlines/editor ownership and leaves release OFF; saved drafts are not an
  automatically resumed schedule. Actual profile changes and confirmed loss of
  Remote authority retain their release-revocation semantics. No authority takeover
  is added by upgrade or planning.
- The resource stays `/wallbox_manager/wallbox-manager-card.js`; existing dashboards
  and entry bindings remain. Refresh browser/app caches and keep backend/card
  versions aligned. The five beta.16 PV card editors remain absent; supported
  tuning stays accessible through HA entities/options.

Stationary retains exclusive CP, relay, register and electrical safety ownership.
The existing beta.15 interface baseline remains applicable (Stationary develop
`279287c3c1d8863e803144eccf773ab3f10889a5`, reviewed implementation
`c32e9c935601b8b9271200f1dc3f669dde43275e`). This candidate requires no Stationary
source/configuration update or restart. Physical interoperability is not established
by simulated tests. An unusually slow journal write may delay economic or SoC STOP;
this accepted tradeoff is unchanged. No priority mechanism or extra writer is added.

## HACS upgrade and rollback

These are **prepared operator instructions**, not actions performed by this task.

1. Record the installed WBM version (normally **0.4.0-beta.16**), HA version,
   Stationary version and current mappings/settings. Keep manager charging release
   **OFF**. This does not physically stop Local-controlled charging; do not change
   authority, local current, hardware enable, relay or CP state as part of upgrade.
2. Take and retain a **full pre-upgrade HA backup**, including integration files,
   config entries/options, entity/device registries and all WBM `.storage` records
   (profiles, session, NETZ reserve recovery, legacy PV journals and any V3 journal).
   Keep the beta.16 package available. Use HA's supported consistent backup method;
   do not manually edit live `.storage` files.
3. Only **after approval and publication**, enable prereleases in HACS and select
   **v0.4.0-beta.17**. Install WBM and perform the normal HA restart under operator
   control. Verify `custom_components/wallbox_manager/manifest.json` reports
   **0.4.0-beta.17**. Do not update or restart `wallbox-stationary`.
4. Check valid Fronius/Smart Meter/BYD HA mappings: grid import/export, PV production,
   total consumption including EV, actual battery SoC, observed writable minimum
   reserve, separate battery charge/discharge power, battery capacity and remaining
   local-day PV forecast as needed by the selected profile. Check units and genuine
   unavailable values; battery-free SURPLUS gains no battery/forecast dependency.
5. Refresh the cached Lovelace resource on every browser/app client. Confirm manager
   release remains OFF, actual/reference values are truthful, and effective target
   SoC updates before Start. Check Optimum phase/forecast behavior without charging.
6. Check NETZ settings, time drafts and reserve display/recovery first. If a powered
   NETZ check is authorized, use normal explicit controls in a supervised test,
   then return release OFF; do not treat saved schedules as automatic permission.
7. For a separately authorized first PV charging test, select diagnostic **Level 2**,
   verify mappings, budget and actual authority/hardware/transaction state, then
   explicitly Start. Observe confirmed versus requested points, EV uptake, STOP and
   recovery. Save diagnostics for review; automated tests are not hardware acceptance.
8. If rollback is needed, keep release OFF and save a consistent post-test backup
   and diagnostics, including any new V3 economic STOP/Wh journal. HACS may reinstall
   **v0.4.0-beta.16**, followed by an operator-controlled restart/cache refresh, but
   **a package-only downgrade is not a storage rollback**. Beta.16 ignores V3 history;
   removed old delay values/entities are not restored, and a later re-upgrade may
   encounter the retained V3 journal. Never delete that history to create allowance.
   Use the complete pre-upgrade backup for a coherent rollback, and separately
   reconcile post-backup transactions, exhausted budgets and reserve changes before
   any charging is re-enabled. No automated reverse migration or cross-version
   economic-state preservation is guaranteed. Restoring a backup alone cannot
   account for charging that occurred afterward. Do not replay saved release or
   deadlines as charging instructions.

## Validation and review evidence

See [release-candidate verification](release-validation-beta.17.md) for exact local
results, branch ancestry and publication checks, and the
[implementation report](../pv-v3-implementation.md) for source revisions, migration
and failing-before regression evidence. The candidate preserves all intended PV V3
implementation commits and the four review corrections. Successful simulations do
not certify physical phase switching, EV response, electrical safety or live HA CPU
acceptance. Full initial station telemetry remains an interface acceptance gap.

Final operator approval and successful CI on the eventual publication commit/tag
remain publication gates. No merge, tag, GitHub release, HACS install, HA restart,
deployment or hardware command is included in this release-preparation task.
