# PV regulation V3 implementation and review record

Implementation branch: `feature/pv-regulation-v3-runtime`.
Base: `docs/pv-shared-regulator-simplification-20261025`, commit
`533e9ed2f06ff41f9feea1660d287b8a5284791f`.
The operator's implementation task authorizes the consolidated three PV specifications;
their older documentation-only approval notices are not a second implementation gate.
No normative requirements were edited. This branch is for human code review, not a
release, installation or hardware acceptance.

## Responsibilities and execution

- `pv_soc.py`: common exact S/L/M/U matrix. The beta.18 inclusive L STOP is replaced
  by strict SoC<S; STOP restarts BALANCE strictly above M with surplus, or FAST at U.
- `pv_plan.py`: profile-specific T: fixed SURPLUS is validated without clamping,
  MAXIMUM uses R+H, and OPTIMUM remains bounded against reserve/H; preserved remaining-day
  energy calculation, first-surplus daily latch, FINISHED latch, local midnight/DST,
  300-second planning and immediate forecast-change replanning on the next PV tick.
- `pv_core.py`: one pure FAST/BALANCE policy, unsmoothed signed storage power, SoC
  bias, minimum-point STOP A, one-step increases, uptake gating, startup hold and
  configurable ordinary downward delay. Battery-free SURPLUS uses only EV and grid.
- `grid_point.py`: existing capability/current lattice and navigation, with a shared
  `regulation_target()` numerical-target adapter. Positive phase series take precedence
  over globally closer overlapping points. Nominal offer power remains 230 V × phases
  × current, separate from actual EV consumption and economic measurement pricing.
- `pv_economics.py`: prospective measured import accounting, complete import power
  above dynamic M/2, strictly >15 seconds reset, separate STOP provenance/restart.
- `pv_migration.py`: read-only import of beta.18 economic and daily journals. Wh and
  STOP survive; D, source-generation locks, old reset clocks and response models do not.
- `pv.py`: one canonical HA observation map, one periodic regulation clock, a versioned
  per-entry journal and sparse Level-2 diagnostics. OCPP power comes from the canonical
  selected session channel. Events update observations and interrupt invalid accounting
  intervals; they never execute another PV decision. Valid held values do not expire.
- `grid.py`: existing guarded transaction-bound OCPP executor, manager release,
  accepted/applied distinction and phase/re-enable lockouts. Ordinary PV dispatch is
  admitted only from a clock decision. Asynchronous observations can confirm an existing
  request. Lockout expiry does not run another PV calculation or replay a queued point.

Stationary remains solely responsible for electrical safety, physical switching, CP,
interlocks and hardware protection. NETZ retains its event-driven fixed-point execution,
absolute business schedules and independent battery-reserve ownership. No battery or
household mapping is added to NETZ or battery-free SURPLUS.

### Timing and minimum-point decisions

A successful executable positive request following a confirmed zero starts the startup
counter at zero. The next ten **eligible and resolved** cycles hold the point, including
when uptake already exceeds 50%; ordinary adjustment is released on the following cycle.
A rejected request starts no settling window. Accepted interim positive points during a
phase lockout also establish settling if they constitute a real zero-to-positive start.
An unresolved current/phase change earns no upward ramp progress and cannot stack another
ordinary point. Subsequent increases count eligible cycles from the prior accepted
increase; held unchanged sensors are valid input, not a generation barrier.

Downward delay N waits N additional eligible cycles after first detection; zero acts on
that detecting tick. Operator Stop, mandatory SoC STOP, authority/lifecycle fencing and
economic exhaustion override normal timing. Storage failure inhibits new positives but
never prevents a required zero request. No 10-second or 90-second economic timer is added.

For ongoing FAST/BALANCE charging, a saturated ideal retains a feasible positive minimum
until shared SoC/economic policy requires a pause. An ordinary zero/nonpositive computed
ideal does not force a new positive session. An explicitly applicable BALANCE upward SoC
bias can choose the first legal point above a zero ideal, with the usual start evidence
and all common execution/timing guards. BALANCE A requires a genuinely applied, enabled/legal
1p/6 A, SoC<L, completed startup hold, and valid negative signed storage power; a mere ACK
or phase-locked desired minimum is insufficient. A real phase lockout retains the current
series minimum and prices M accordingly until a changed phase is actually established.

### Economic state and recovery

`wallbox_manager.<entry_id>.pv` stores versioned Wh, scope, STOP provenance, station
identity and planning-day state. Scope includes connector binding, Remote ownership and
real external transaction. Interval endpoints and reset dwell are process-local and
never restored over downtime. Actual held phase voltages price M when available;
nominal point identity is unaffected by voltage reports. Threshold changes are prospective.

Journal writes are serialized and verified against saved content. STOP is retained in
memory and dispatched even if persistence fails; persistence failure never authorizes a
positive request. Restart requires supported reset, confirmed zero offer **and measured
zero EV power**, SoC permission and either positive signed battery charge (including 1 W)
or FAST household power strictly below its configured threshold. A both-positive storage
pair becomes zero only inside WBM and cannot qualify as charging evidence. Battery-free
SURPLUS uses positive grid-surplus evidence for its restart, with the same OFF/reset gates.
There is no additional retry edge, one-shot latch, backoff or cooldown.

PV-to-PV selection and threshold edits retain economic history; release resets on a real
profile switch. Explicit manager Stop and NETZ scope exit close the economic scope.
A proven Ended event allows a new transaction's allowance; an uncertain identity handover
does not. Hardware identity mismatch or unrecognizable persisted state inhibits positive
recovery. The original beta.18 `.pv_economy` and `.pv_day` documents are left untouched.
Recognized old records preserve exact stored Ws as Wh and STOP state, including old
in-flight OFF; malformed/mismatched history remains inhibited rather than becoming a
fresh allowance. Legacy D-dependent restart references are deliberately not reused.

Temporary disconnect produces no commands or accounting/reset credit. HA-only target
planning may continue provisionally, with no invented EV reading, first-surplus evidence,
physical point or command queue. A Local authority report retires release. Recovery needs
current-generation supported observations, explicit authority/release where applicable,
and the next ordinary PV tick, rather than a reconnect-triggered positive command.

## Configuration and entity migration

All new parameters are available through integration options and distinct configuration
Number identities. Defaults and validation limits are deliberately bounded:

| Key | Default | Accepted range / unit |
| --- | ---: | --- |
| `pv_increase_interval_cycles` | 3 | 1–720 whole cycles |
| `pv_startup_hold_cycles` | 10 | 0–720 whole cycles |
| `pv_grid_deadband_w` | 100 | 0–10,000 W |
| `pv_decrease_delay_cycles` | 0 | 0–720 whole cycles |
| `pv_grid_import_budget_wh` | 50 | 0.1–10,000 Wh |
| `pv_ev_uptake_threshold_pct` | 50 | 0.1–100% |
| `pv_fast_restart_household_load_limit_w` | 500 | 0–100,000 W |

Cycle bounds limit configuration mistakes to finite delays without converting the
independent `regulation_interval` seconds. Budget/uptake controls use a 0.1-unit editor
step. Finite bounds and whole cycles are validated in options and Number services.
Settings edits do not execute a regulation pass; the next scheduled cycle reads them.
Accumulated actual Wh does not scale/reset when the configured limit changes.

`storage_charge_power` is added beside the existing discharge map. Select nonnegative
power channels in W/kW/MW; energy planning channels accept Wh/kWh/MWh. Invalid, unavailable,
restored, negative and wrong-unit readings remain unavailable. The existing
`leistung_verbraucher` contract explicitly means total consumption **including the selected
EV**; WBM subtracts actual EV power to obtain household demand. A missing household map
only disables that optional FAST restart branch. BALANCE requires both storage channels.

`pv_start_delay` and `pv_stop_delay` are removed from active settings, options, translations
and exact owned control entity identities. Their stored profile keys are removed during
initialization; unrelated settings, entities, statistics and foreign profile fields are
preserved. No old seconds are interpreted as cycles and no retired identity is reused.
`optimum_max_discharge_w` keeps its Number identity, value and service contract, labeled
deprecated/unused. Legacy `power_smoothing_window` is retained unchanged for compatibility
and is **not consumed by PV V3**. The card receives the canonical effective Optimum/Maximum
T through the existing profile entity; it has no new electrical solver or tuning controls.

## Diagnostics

PV Level 2 emits compact `pv_target`, `pv_decision` and `pv_inputs` records at INFO under
`custom_components.wallbox_manager.pv`, subject to the existing diagnostic-level setting
and HA logger configuration. Set that logger to `info` when collecting decisions; no global
log levels are changed. Existing OCPP diagnostic levels/severity remain unchanged.

Semantic signatures use mode/reason/discrete phase/current/economic state, not fluctuating
measurement digits. Events include only deciding grid/EV, storage/SoC, target/planning or
Wh/reset/restart context. No transaction ID, raw packets, full snapshots, recurring HOLD
summaries or measurements on every tick are logged. Logging failures cannot affect control.
A 300-cycle steady/noisy replay proves zero additional PV decision events and commands;
repeated missing input produces one failure transition and one recovery.

## Source evidence

- WBM base/develop runtime inspected: `grid.py`, `grid_point.py`, `settings.py`,
  `config_flow.py`, `number.py`, `ocpp.py`, `control.py`, `battery.py`, `grid_battery.py`.
- WBM `v0.3.2-beta.18`, commit `4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`:
  `pv_soc.py`, `pv_optimum.py`, `pv_regulators.py`; additionally `pv_economy.py`,
  `pv_economy_storage.py` and `profiles.py` for stored-state compatibility. Target bounds,
  daily latch and energy calculation are preserved; the old STOP matrix and D/source
  generation/response power-regulator runtime are superseded.
- Actual [openWB/core](https://github.com/openWB/core/tree/809ac7425348dc4a0a2e080e742dfcd2f3cfc992),
  commit `809ac7425348dc4a0a2e080e742dfcd2f3cfc992` (read-only source checkout):
  `packages/control/algorithm/surplus_controlled.py` and `packages/control/bat_all.py`,
  especially `SurplusControlled._set()`, EVU control-range/current adjustments and
  `get_charge_mode_vehicle_charge()`. These confirm grid exchange and storage priority
  as coordinated inputs, not a universally storage-only control law. No openWB code,
  hardware policy, two-amp adjustment or battery-control behavior was imported.
- Actual local Fronius PV Manager source, commit
  `b464f91154091c232b25d37343b3a592f5a20eaf`: `semantics.py` classifies `StCha` and
  `StDisCha`; `sensor.py` exposes Model 160 DCW channels; `coordinator.py` handles common
  FAST model reads and separate HA publication. Source states are never modified by WBM.

## Requirement map and review sequence

| Requirements | Implementation / principal verification |
| --- | --- |
| PV-ARCH-01–09, WBM-ARCH-01–12 | One snapshot/clock; pure shared controller; unchanged Stationary ownership; event-vs-tick, passive/NETZ, disconnect, no-queue and single-clock regressions |
| PV-SOC-01–03 | `pv_soc.py`, planner bounds; complete S/L/M/U × previous mode × surplus matrix and equal-T equivalence |
| PV-PLAN-01–04 | `pv_plan.py`, legacy daily import; forecast changes, 300-second cadence, FINISHED, midnight and 23/25-hour DST tests |
| PV-FAST-01–04 | Grid feedback, zero-grid/night exploration, one-step upward cadence, deadband, uptake, minimum and downward tests |
| PV-BAL-01–03/06/07 | Signed two-source battery flow, both-positive zero, SoC bias and STOP A/B; steady replay, household pulse and unlock replay; no smoothing |
| PV-STEP-01–06, PV-RESP-01–08 | Shared lattice, phase-series navigation, one outstanding request, ten-cycle settling, cycle delays, urgent STOP precedence and real-peer lockouts |
| PV-TUNE-01–12 | Settings/options/Number validation, defaults, obsolete entity/key retirement, budget edits, compatibility and configuration persistence |
| PV-ECO-01–09 | Dynamic M/2, complete import integration, >15 s reset, no backfill, Wh persistence, zero evidence and both restart branches; 499/500 W and 1 W tests |
| WBM-PV-RELEASE-01–07 | Existing guarded Start/Stop/profile retirement; real-peer PV release, Local/Remote, late ACK and reconnect tests |
| PV-OCPP-01–06 | Reused command-generation/transaction/authority/readback and bootstrap paths; existing protocol/NETZ suite plus PV-specific unresolved ACK, zero ACK, lockout and recovery tests. Full physical bootstrap acceptance remains external. |
| PV-DIAG-01–10, PV-QA-01/02 | Decision-context tests, 300 unchanged/noisy cycles, input failure/recovery sparsity, deterministic policy/closed-loop replay and existing full regression suite |
| PV-QA-03 | Hardware acceptance remains pending; no performance or safety acceptance claimed from mocks |

Suggested human review order: (1) pure SoC/planning and target equivalence;
(2) shared point navigation and FAST/BALANCE decisions; (3) timing and command fences;
(4) economic scope, persistence, migration and restart; (5) sparse diagnostics,
configuration migration and requirement/test coverage. Independent review is a separate
step and has not been claimed as completed by the implementing agent.

## Verification and remaining acceptance

Final command results are recorded below after the final complete run. Python tests use
Home Assistant 2026.8.0, Python 3.14.7 and real localhost WebSocket OCPP peers; loopback and
headless Chrome require running outside this workspace's network-restricted sandbox.
The initial sandbox-only integration/browser attempts were interrupted or failed on local
socket restrictions and are not counted as successful verification.

Original implementation verification (reviewed commit `d394d6b`), 25 October 2026:

| Command | Exact result |
| --- | --- |
| `.venv/bin/pytest -q --cov=custom_components.wallbox_manager --cov-report=term-missing` | **698 passed, 0 failed, 5 warnings**, 112.67 s; **93%** total statement coverage (3,937 statements, 286 missed) |
| `.venv/bin/ruff check .` | All checks passed |
| `.venv/bin/ruff format --check .` | 157 files already formatted |
| `node --check custom_components/wallbox_manager/www/wallbox-manager-card.js` | Passed |
| `node tests/test_wallbox_card.cjs` | **78 passed, 0 failed** |
| `node tests/test_wallbox_card_layout.cjs` | **1 passed, 0 failed**, real headless Chrome |
| `git diff --check` | Passed |

The new PV Python files contribute **168 passing test cases** (136 pure policy,
planning/migration/replay cases and 32 HA/OCPP runtime/mapping cases). PV statement
coverage: adapter 91%, pure core 94%, economics 95%, migration 91%, planner 96%,
SoC 94%. The five warnings come from installed dependencies: one Home Assistant
`web.Application` inheritance deprecation and four `backoff` references to deprecated
`asyncio.iscoroutinefunction`. They are not PV control warnings or hardware results.
No tests were skipped. No assertion failures remain.

Existing storage-failure and options-precedence tests that referenced the retired delay
settings now exercise the distinct cycle parameters, with explicit PV-TUNE-10 comments.
All other existing tests remain in the complete suite. The frontend additionally checks
canonical effective target display, invalid values and both locales.

All task files are committed; pre-existing untracked investigation documents and `pv2.log`
are intentionally left untouched and excluded from the deliverable. The feature branch is
pushed for subsequent human review, without a merge or release.

No runtime requirement is intentionally deferred. Remaining external acceptance covers the
installed station's full bootstrap/report coverage, physical phase/re-enable behavior,
actual vehicle uptake/CP responses, selected HA source semantics/units and temporal skew,
real import Wh detection latency, real restart cycles, and agreed PV-QA-03 performance
metrics. The existing interface does not guarantee a complete immediate physical telemetry
burst; missing observations stay unknown and are not manufactured. No Stationary changes,
hardware commands, installation, merge, tag, release or deployment were performed.


## Independent review corrections after d394d6b

Reviewed baseline: `d394d6b23d0eb5f4f84d759d6a5beb1074e6f6d0` on the same
`feature/pv-regulation-v3-runtime` branch. The remote branch was fetched and matched
that exact commit before corrections. These changes address the four independent
findings without modifying normative specifications, NETZ, OCPP wire dispatch,
configuration/entity identities, or Stationary.

### Finding 1: fixed SURPLUS target validation

Root cause: `Planner.calculate()` reused dynamic-target `clamp_target()` for the fixed
user SURPLUS target. When an external reserve change invalidated that target, it produced
a substituted T. The planner now validates the original number against the full-precision
`R+H <= T <= 100-H` interval and rejects it unchanged if invalid. MAXIMUM's intentional
R+H and OPTIMUM's bounded dynamic calculation remain unchanged.

`validate_target_inputs()` is shared by the planner and the read-only validity projection;
it does not advance planning state or execute control. Regression coverage crosses both
interval boundaries and fractional reserve edges, and demonstrates invalidation and
recovery without new positive commands, setting mutation or release/authority changes.

### Finding 2: effective-target validity and publication

Root cause: invalid calculation inputs raised/returned before replacing an earlier
`effective_target_soc`. Required input invalidation now clears that canonical value to
`None`, resets its semantic target-event signature and publishes the validity transition.
HA callbacks perform validation only; they never compute a regulation point or issue a
command. Recovery is calculated by the existing ordinary/provisional PV tick. Profile
changes also retire the previous profile's canonical target.

Dependency distinctions are explicit:

- All battery targets require a valid reserve and valid hysteresis/target interval.
- OPTIMUM additionally requires its valid remaining forecast and positive capacity.
- SURPLUS/MAXIMUM do not gain a forecast or capacity dependency.
- Actual battery SoC remains mandatory for **regulation**, but is not an input to any
  target formula. Its unavailable left-hand display and an otherwise calculable T remain
  independent under WBM-HS-UI-09. No zero SoC is invented and no regulation proceeds from
  missing SoC.
- OCPP disconnect alone does not clear valid HA-derived T. Provisional calculation and HA
  invalidation/recovery continue on the original PV cadence without an OCPP command.

Tests cover missing/deleted and unavailable inputs in all three profiles, unchanged held
values, disconnected recovery, and 300 unchanged invalid ticks with no further target
publications, PV diagnostic records or commands.

### Finding 3: accepted economic restart durability

Root cause: `PV.accepted()` cleared economic STOP in memory without scheduling a journal
checkpoint, leaving the durable state dependent on a later tick/unload. It now initiates
an immediate checkpoint through the existing serialized PV journal mechanism. The
checkpoint captures **live state after acquiring the journal lock**, not an old queued
restart snapshot. It does not reset Wh or equate ACK with applied current/consumed power.

Checkpoint write errors (including a swallowed save failure detected by file verification)
and cancellation are handled explicitly. If the same budget/scope/context still owns the
restart and no newer STOP has superseded it, failure conservatively restores its STOP
provenance while retaining current Wh. A newer operator action, economic STOP or transaction
scope is never rolled back. The existing input-problem channel exposes `journal_write_failed`
with transition-only diagnostics. Any required zero request remains on the normal guarded
control path; a checkpoint failure does not fabricate a physically stopped wallbox.

Cancellation drains an already started journal write while retaining the journal lock.
An older executor write therefore cannot finish after and overwrite a completed newer
STOP/operator/transaction journal write. This is I/O serialization inside the existing
persistence operation, not a new regulation loop, worker or retry cooldown.

Tests cover immediate disk state without another regulation tick, subsequent HA reload
and retained Wh, raised and unverified save failures, cancellation before/during writing,
queued/in-flight older checkpoints versus newer STOP/operator/transaction state, and a
late positive ACK rejected by the existing Grid intent fence after explicit Stop.

**Unavoidable crash boundary:** station acceptance and local journal persistence are not
atomic. A crash after the positive ACK but before verified checkpoint completion can
restore the older economic STOP conservatively; accepted, reported applied and measured
power still require separate reconstruction. A storage/verification error may leave the
last verified or an unverified completed disk write; memory/physical/disk state cannot be
claimed identical during such a fault. Reload still retires manager release, re-establishes
current-generation OCPP evidence and applies the existing conservative scope/recovery
policy. No new time-based retry restriction or fictitious physical OFF is introduced.

### Finding 4: BALANCE bias above a zero ideal

Root cause: the `candidate.amps > 0` guard skipped the deliberate upward SoC bias when the
ideal quantized to zero. The bias now uses zero itself as its basis for **exactly zero
ideal power** when SoC>M, selecting the next legal lattice point. This also avoids adding
a second step to the ordinary continuation floor of an already positive point.

This is a deliberate SoC regulation decision, not a forced ordinary technical minimum.
Below/inside the band, STOP without surplus evidence, a negative ideal, battery-free
SURPLUS, economic STOP, pending command and startup hold retain their existing semantics.
The current lattice, disabled modes, phase navigation, uptake/cadence gates and downward
delay are reused. Tests cover the exact reviewed 51.5%/T=50/H=2/zero-EV/zero-storage case,
M boundaries, 0 A and ongoing 1p/3p points, actual advertised 2p/3p-only capabilities,
common guards and execution through a real localhost OCPP peer.

### Regression proof and final correction verification

Four tests were added **before changing implementation**:

- `test_review_1_surplus_rejects_invalid_fixed_target`
- `test_review_2_invalid_forecast_clears_canonical_target`
- `test_review_3_accepted_restart_checkpoints_without_another_tick`
- `test_review_4_balance_bias_can_start_above_zero_ideal`

Against the unchanged reviewed implementation, the command
`.venv/bin/pytest -q tests/test_pv_review_regressions.py` produced **4 failed, 0 passed,
5 dependency warnings in 2.70 seconds**, each detecting its respective finding. All four
pass after correction. The expanded review tests live in that same file; existing tests
were not weakened or removed.

Correction verification, 25 October 2026:

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q --cov=custom_components.wallbox_manager --cov-report=term-missing` | **765 passed, 0 failed, 5 dependency warnings**, 131.52 seconds; **93%** total coverage (4,000 statements, 287 missed) |
| `.venv/bin/ruff check .` | All checks passed |
| `.venv/bin/ruff format --check .` | 158 files already formatted |
| `node --check custom_components/wallbox_manager/www/wallbox-manager-card.js` | Passed |
| `node tests/test_wallbox_card.cjs` | **78 passed, 0 failed** |
| `node tests/test_wallbox_card_layout.cjs` | **1 passed, 0 failed**, real headless Chrome |
| `git diff --check` | Passed |

The correction file adds **67 passing regression cases** to the unchanged existing suite.
PV adapter coverage is now 93%, planner 98%, and core 94%. The same five dependency
warnings remain: Home Assistant's `web.Application` inheritance and `backoff` use of
`asyncio.iscoroutinefunction`. No tests were skipped and no failures remain. Localhost
OCPP peers and headless Chrome were run with the required sandbox socket permissions;
no physical station, HA installation or deployment was contacted.

Final diff review confirms unchanged specifications, configuration/entity identities,
NETZ code, Grid/OCPP authority and transaction guards, and Stationary. All four corrections
are ready for another independent review on the pushed implementation branch. Pre-existing
untracked investigation files remain excluded and untouched.

The source cross-check was repeated against actual openWB/core commit
`809ac7425348dc4a0a2e080e742dfcd2f3cfc992`,
`packages/control/algorithm/surplus_controlled.py` and `packages/control/bat_all.py`
(including `get_charge_mode_vehicle_charge()`), and historical WBM beta.18 `pv_soc.py`
at `4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`. Those sources do not override the latest
fixed-target/display and zero-ideal bias decisions; no upstream algorithm or historical
SoC threshold was restored. No new threshold, smoothing, economic STOP condition,
restart cooldown, obsolete delay option or independent control loop was added.
The earlier external hardware/physical-bootstrap acceptance limitations remain unchanged.
