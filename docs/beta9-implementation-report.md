# Wallbox Manager beta.9 implementation and validation

Review candidate based on `eb792c3` (the current `codex/pv-optimum` development
checkout, also the beta.8 release commit). Work branch:
`codex/beta9-regulator-safety`. No merge, version/tag creation, release,
Home Assistant deployment or station firmware change is part of this work.

## Follow-up investigation

The requested restart/utilization follow-up is documented in
[the investigation note](beta9-followup-investigation.md). It identifies the
five start vetoes, the additional grid restart gate, the 470 W reserve breakdown
and the source-order ambiguity requiring a safety-policy clarification. The
production implementation and results below remain the original review candidate;
no corrected-regulator completion or new full-suite result is claimed yet.

## Findings and scope

The three `.analysis/` hardware logs and the task's consolidated findings were
reviewed alongside the current regulator, solver, session ledger, control fences,
phase protocol, SoC policy and tests. Separate daytime/nighttime analysis reports
were not present in the checkout. Many level-3 JSON lines in the nighttime
logs are truncated, which also limits payload-level reconstruction. The supplied
counts (1,529 FAST evaluations, 36 unavailable EV evaluations, 54 duplicate
accepted setpoints) describe the
historical investigation, not new hardware measurements.

Verified implementation causes:

* FAST previously advanced its request on repeated evaluation of the same source
  readings, using a previous requested power as an upward baseline. Acknowledged
  current and measured EV consumption were not separate feedback boundaries.
* The minimum-positive policy could raise a soft request indefinitely without an
  independent power ceiling. The common solver accepted only current constraints.
* Both `pv_input_gap` and unavailable planning paths discarded the stop start
  time. Existing regression tests explicitly expected a new delay after a gap.
* OCPP reuse compared entire `OperatingPoint` objects, including calculated watts
  and the voltage basis, although the physical wire setpoint was unchanged.
* Session power intentionally becomes unknown if its selected observation and
  ledger disagree. A deterministic equal-source-time conflicting-value sequence
  reproduces this guard and recovers on a newer valid measurement. The supplied
  logs do not contain sufficient meter payloads to attribute all 36 historical
  incidents to that sequence. **No session-arbitration defect is established or
  claimed fixed.** Validation remains intact; precise availability reasons and
  protective sequence regressions were added instead.

The SoC thresholds, STOP / BALANCE / FAST transitions, daily planner states,
reserve handling, active station ownership and authority transfer are unchanged.

## Power equations and safety boundary

All quantities are validated watts. Let:

* `E` = measured EV consumption, `E_low` = conservative EV basis described below;
* `D` = nonnegative measured battery discharge, `L` = configured maximum;
* `P` = raw PV power, `C` = raw total consumer power including the EV;
* `G = grid_import - grid_export`, positive for **complete site** import;
* `U = 100 W` = explicit internal measurement/quantization reserve.

The two independent power-flow estimates are:

```
B_incremental = E_low + L - D + max(0, -G)
B_site        = E_low + L + P - C + max(0, G)
B_hard        = max(0, min(B_incremental, B_site) - U)
```

The second equation accounts for household demand `C - E_low`. Taking the lower
bound refuses to spend disagreement between the battery channel and site power
balance as extra charging headroom. Raw measurements, not smoothed policy power,
are used for this ceiling. Existing grid import can support the **existing**
operating condition in this incremental estimate; the grid exception never adds
another watt allowance to the battery budget. Grid policy separately removes
persistent import. No export, household demand or missing sensor is invented.

When an EV generation changes before every site channel has crossed its source
boundary, `E_low` retains the lesser of the preceding and current EV samples.
Only a subsequent site boundary releases that lower basis. Timestamp proximity
is never treated as acquisition synchronization. Sources without acquisition
metadata have report generations, explicitly a weaker evidence class.

`solver.power.solve(hard_max_w=...)` clips each phase's discrete current index
range before selection. DOWN, UP, NEAREST, mode retention, minimum-positive and
same-phase fallback all operate inside that range. A known empty positive range
returns verified OFF. The limit is carried in transient `ManualIntent.hard_max_w`
and applied by the common runtime. Before the wire write, fresh policy evaluation
also checks `current * sum(fresh phase voltages) <= B_hard`. Thus favorable soft
rounding, a phase lockout or an economic stop delay cannot override the ceiling.
If voltage/phase evidence is lost while an already confirmed offer exceeds the
new ceiling, the manager still requests verified OFF.

This is an **EV command guarantee under the stated measurement policy**, not an
absolute physical battery guarantee. Uncontrolled household loads, unreported
acquisition delays, inverter behavior and EV response can exceed a physical
limit without the manager having enough information or control to prevent it.
The 100 W reserve is an explicit engineering choice for hardware validation,
not a statistical confidence interval or a guarantee for arbitrary sensor error.

## Feedback and grid policy

There is one operating-point selection path. FAST supplies its soft watts;
BALANCE retains its existing PV balance target. Configured battery/grid inputs
also constrain BALANCE. A Surplus setup without the optional battery contract
continues using its existing PV-only policy.

FAST records six source boundaries: EV, discharge, import, export, PV and total
consumer power. For ordinary feedback every boundary must advance beyond the
last consumed tuple. Re-reading the same tuple, including a once-per-second HA
session entity refresh, cannot cause another ramp. Explicit `observed_at` takes
precedence over HA report time. Out-of-order boundaries do not count as new.

Accepted wire setpoints start a separate response observation at confirmation.
They are not measured power. Fresh post-command EV evidence within half a
current step (at least 100 W) establishes response. While a command is in flight,
soft power does not increase. A maximum 20-second response window bounds delayed
ordinary deficit correction. Even after that window expires, an unobserved
command cannot authorize another upward step. Hard reductions bypass all gates.
A short import response to a recent command is given up to 10 seconds within
that window, including when EV response arrived before battery response.

For new, usable evidence:

```
G > 100 W: zero-import soft target = max(0, E - G)
otherwise: upward soft target = E + max(0, B_hard - E) / 2
```

The upward half-headroom reserve avoids spending an unobserved EV response twice
when site metering advances first. It is not accumulated repeatedly above an
unchanged physical EV value. The minimum fallback must obey this response reserve
as well: a start from measured zero requires twice the minimum point in established
headroom; a measured continuing minimum does not require that extra start
reserve. The configured regulation interval gates upward updates; new deficit evidence need not wait through a long upward interval.
Selection uses the existing exact electrical grid and phase-improvement rules.

**Tradeoff:** the conservative ramp can settle one or two discrete current steps
below the static battery-only maximum. For the constant 500 W household model,
it settles at 2,530 W EV / 3,030 W battery rather than the old approximately
2,990 W EV / 3,490 W battery. This deliberately reserves headroom for asynchronous
feedback; hardware testing should evaluate this utilization cost alongside
stability. With less start headroom it can also defer a physically possible
minimum-current start until sufficient evidence/headroom is available. It must
not be described as maximum physical battery utilization.

The minimum-point exception uses the smallest currently reachable positive point
`M` from the common solver, including phase lockouts and current limits:

```
allowance = M / 2
measured complete-site import G <= allowance: minimum may continue
G > allowance for 10 s: reduce further if feasible, otherwise verified OFF
```

The allowance is not a target and is not applied to a higher selected point.
At 230 V this is 690 W for 1p/6 A or 2,070 W for locked 3p/6 A. Reused high samples
cannot extend the confirmation deadline. Missing budget evidence pauses charging
instead of resetting that deadline. Recovery uses a 100 W hysteresis below the
threshold; after a pause it additionally considers the EV increment that would
be reintroduced, avoiding restart solely because switching the EV off removed
import. Reaching a newly available one-phase configuration recomputes its minimum
and allowance. No former three-phase allowance is carried forward.

Only existing scoped phase-rejection evidence and existing task-local probes are
used. There is no fabricated phase-expiry or 600-second reenable deadline.
Generic BUSY remains generic BUSY; station retries retain their existing cadence.
A locally fenced, unsent command is replanned without classifying it as station
BUSY, while subsequent phase probes remain bounded.

## Missing inputs and stop delay

Existing unit/finite/future/expiry checks remain. Live readings retain the 90 s
maximum age; the forecast retains its existing 900 s class. If a source provides
`observed_at`, that age must pass as well, so HA refreshes cannot renew old EV
acquisition. Scheduled reevaluation uses that same source-expiry boundary, so a
long regulation interval cannot defer the stale-input pause. Source generations are still required for feedback within this
validity window. Explicit expiry can shorten it.

* No valid battery budget: zero-current pause while enabled. If already disabled,
  retain the existing startup wait/retry path rather than enable with invented
  positive data. No repeated acknowledged equivalent zero is sent.
* Missing ordinary PV-only input: no positive new decision; preserve the existing
  stop start time and cancel start-delay evidence. If its original deadline
  passes while input is missing, request zero without waiting for recovery.
* Hard constraints can pause earlier than the economic stop delay. The configured
  duration and SoC thresholds are not changed.
* Valid surplus recovery can cancel the stop timer through the existing policy.
  Missing input alone cannot cancel or restart it.

## OCPP reuse and diagnostics

Reuse compares `(charging, phase mode, current)` using `same_setpoint`, not
voltage-derived watts. It additionally requires the recorded transaction,
capabilities and current-limit context; current connection, boot, authority,
permission, ownership and dispatch fences still apply. In-flight, stale,
unconfirmed and rejected attempts are not equivalent confirmations. A genuine
retry after a rejection is transmitted. Recovery adoption records the same
context. The acknowledged point retains its historical voltage basis when reused;
this is not a claim of a new acknowledgement or measured EV power.

Level-3 FAST records include the six source-generation identifiers, feedback
revision, budget, reason for evidence reuse/waiting and response policy durations.
Planning adds hard maximum, grid decision, import deadline and original stop start.
Session power exposes `power_availability_reason` with disconnected, no fresh
scoped measurement, pre-transaction, timestamp mismatch, value conflict, invalid
sample, valid and completed outcomes. Existing detailed diagnostic fields remain.
Equivalent-command reuse uses a compact event subject to ordinary deduplication,
not another full meter snapshot. Normal logging remains opt-in and lightweight.
English/German card text explains the new pause states.

## Implementation map and transient state

| File / main functions | Responsibility |
|---|---|
| `pv_budget.py`: `source_time`, `PowerEvidence`, `battery_budget` | Source boundaries, raw conservative budget and constants |
| `pv_regulators.py`: `update_budget`, `acknowledge`, `request`, `minimum_allowed` | Generation consumption, EV response, grid allowance and hysteresis |
| `pv_optimum.py`: `optimum_request`, `optimum_pause_policy` | Shared policy adapter, reachable minimum, independent ceiling |
| `pv_surplus.py`: `reading`, `pv_input_gap`, `_pv_plan`, `permits_point`, `pv_confirm`, `pv_sequence` | Source freshness, timer continuity, live command fence, local replanning |
| `solver/power.py`: `solve` | Hard watt clipping before every discrete selection |
| `control/runtime.py`: `resolve`, `_apply_stored`, `setpoint_context` | Ceiling propagation, confirmed wire-equivalent reuse and context |
| `session_entity.py`: `power_status`, attributes, `native_value` | Explicit availability guard diagnostics; unchanged attribution rules |
| `pv_input_diagnostics.py`, `pv_diagnostics.py`, `diagnostics.py` | Correlation and safety/reuse explanations |
| `www/wallbox-manager-card.js` | Four translated user-facing pause explanations |

New regulator state: hard ceiling, current source tuple, consumed tuple, prior EV
sample/lower basis, accepted setpoint identity and its evidence/time, import start,
minimum-excess start/generation, restart latch, feedback revision and reason.
Runtime additionally caches the confirmation's transaction/electrical context.
All are in memory. No storage schema, entity identity or persistent session field
changes. Reload discards measurement/command caches and uses existing recovery;
no stale budget is restored. Existing stop timers remain process-local; the
requirement preserves them across measurement gaps, not across HA restarts.

## Quantitative regression experiment

`tests/test_pv_beta9_simulation.py` evaluates every second, updates site sensors
every 5 s and EV sensors every 10 s, with 0/3 s acquisition offsets. It covers
constant load, +1,200 W / -1,200 W household steps, +1,800 W then loss of PV,
near-limit discharge, discrete 1 A current, three acknowledgement/EV latency
pairs and per-command variable latencies: **35 deterministic scenarios**.

The separate real-runtime tests cover phase lockouts, authority, queued wire
writes, transaction replacement, missing measurements and station retries.
The closed-loop comparison uses the same disturbance schedules and plant for
beta.8's original request/floor algorithm and beta.9. It is not a replay of the
recorded hardware's EV samples: those depend on each controller's commands.
The frozen test-only `beta8_regulator_reference.py` supplies the beta.8 baseline;
production FAST requires evidence and has no legacy fallback controller.

Proposed regression bounds, not pre-existing product requirements: no change in
selected power during the final 60 s of a 360 s trace; final settling within 120 s
of the last disturbance; zero selected-budget violations; final import <=100 W
above minimum, or <=50% of minimum EV power. The broad 120 s bound covers source
cadence and variable EV response, and does not assert that every real car meets it.
Settling time in the table is time to the last command change, not time to the last
physical transient. Import duration is cumulative time above 100 W. The model
itself limits inverter discharge to 3,500 W, so its battery peak is **not** proof
of physical limit enforcement by the controller; budget-selection tests provide
that separate software assertion.

Detailed values are in [the metric artifact](validation/beta9-regulator-metrics.json).
The following representative runs use 3 s acknowledgement and 8 s EV response:

| Scenario | Commands old → new | Reversals old → new | Settling s old → new | Final import W old → new | Peak import W old → new | Seconds >100 W old → new | Battery peak W old → new | Final EV W old → new |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| constant | 31 → 3 | 24 → 0 | 353 → 53 | 11.14 → 0.0 | 131.17 → 345.0 | 18 → 4 | 3489.99 → 3030.0 | 2990.0 → 2530.0 |
| load_up | 55 → 5 | 24 → 1 | 238 → 13 | 317.82 → 0.0 | 1288.48 → 965.0 | 83 → 15 | 3499.88 → 3499.77 | 2990.0 → 0.0 |
| load_down | 28 → 3 | 19 → 0 | 238 → 43 | 11.14 → 0.0 | 600.0 → 600.0 | 21 → 7 | 3489.99 → 3080.0 | 2760.0 → 2530.0 |
| pv | 35 → 7 | 12 → 2 | 118 → 23 | 181.61 → 0.0 | 1240.0 → 1240.0 | 51 → 20 | 3499.47 → 3499.45 | 3680.0 → 2530.0 |
| near_limit | 0 → 0 | 0 → 0 | 0 → 0 | 0.0 → 0.0 | 750.0 → 750.0 | 3 → 3 | 3380.0 → 3380.0 | 1380.0 → 1380.0 |

Short transients are deliberately allowed. Some new transient peaks are higher
than the old algorithm despite much less sustained import and fewer commands.
Five of the seven load-increase variants finish paused because the conservative
start reserve cannot reestablish the minimum point with the remaining headroom.
A physical 1p minimum would be possible in this idealized plant. Their zero final
import therefore must not be interpreted as successful continuous charging at the
highest physically feasible point. The final-EV column and metric artifact make
this cost explicit. Refining the acquisition/response uncertainty contract to
recover that utilization remains a limitation for review and hardware validation.
Hardware performance and sensor uncertainty remain to be validated.

## Requirements traceability

| Task requirement | Implementation | Main regression evidence |
|---|---|---|
| 3.1 hard maximum through all fallbacks | Solver clipping, intent ceiling, live point fence | `test_pv_beta9`: all directions/floor, start-response reserve, 3p cap, queued limit/input changes |
| 3.2 independent discharge ceiling, household/PV/grid accounting | Two raw power-flow bounds, lower EV basis, reserve | Budget equations test; asynchronous simulations; excess-battery locked-floor test |
| 3.3 invalid/stale input, no fabricated zero or increase | Source/report validation; hard-budget OFF | Source-stale wire fence; FAST/BALANCE expiry bounds; gap tests; voltage-gap protective OFF |
| 4.1 / 4.3 zero site import above minimum | Fresh-evidence `E-G` correction | Household/PV simulations; long upward interval deficit test |
| 4.2 / 4.4 50% at reachable minimum only | Common minimum query, `minimum_allowed` | Both 1p/3p threshold tests; locked 3p runtime tests |
| 4.5 reassess after phase availability | Existing desired/executable planning and probes | `test_pv_beta9` lockout release; `test_pv_optimum_desired` |
| 4.6 bounded excess-import pause | Non-extending 10 s deadline, independent hard ceiling | Stale-evidence deadline and locked-floor pause tests |
| 4.7 avoid boundary cycling | Step response reserve, generation/response gating, restart hysteresis | 35 simulation traces, threshold recovery tests |
| 5 measurement-aware FAST, delayed ACK/EV response | Evidence tuple, consumed tuple and acknowledged response state | `test_pv_beta9`, `test_pv_beta9_simulation`, `test_pv_optimum_timing` |
| 6 EV unknown investigation without speculative weakening | Availability reasons; existing ledger arbitration retained | `test_session_power_reasons`, existing session/meter/transaction tests |
| 7 preserve original stop deadline through gaps | `pv_input_gap`, unavailable `_pv_plan` | Short/repeated/pre-expiry/spanning gaps; surplus recovery; hard-budget pause tests |
| 8 wire-level OCPP reuse with fencing/retries | `same_setpoint`, confirmation context and live checks | Voltage drift/new transaction/rejection reuse tests; existing authority/recovery/snapshot suites |
| 9 preserve station reenable behavior | Existing generic temporary-rejection retry path | `test_restart_busy_retries_fresh_targets_without_rearming` and existing retry tests |
| 10 sufficient correlation, limited normal logs | Generation/revision/budget/reason fields; deduplicated reuse event | Diagnostic levels/input-equivalence suites; session reason tests |
| 11 SoC / planner / authority / persistence invariants | Existing policy/state machines unchanged | Full SoC, daily planner, ownership, storage and protocol suites |
| 12 reviewable delivery / tests / report | This report, metric artifact, implementation branch | Full Python, Ruff and card checks below |
| 13 hardware validation | Sequence below | Prepared; not executed on hardware |
| 14 no merge/tag/release/deploy/station changes | Local feature branch only | Git working tree and unchanged release manifest |

## Verification results

* Unmodified beta.8/development baseline: **1,843 Python tests passed**.
* Final implementation: **1,910 Python tests passed** in 85.22 s, including the
  35 deterministic regulator scenarios and FAST/BALANCE source-expiry checks.
* Both runs emit the same five dependency deprecation warnings from Home
  Assistant/backoff; no test failures remain.
* `ruff check .`: passed. `ruff format --check .`: all 165 files formatted.
* `node tests/test_wallbox_card.cjs`: **129 tests passed**.
* Card JavaScript syntax check and `git diff --check`: passed.

The Home Assistant fixture suite was run with the approved local environment
access because its async startup stalls inside the restricted sandbox. The
baseline and implementation used the same installed dependencies. No hardware
execution was performed. All changes remain uncommitted on the feature branch;
no release/version metadata was changed.

Legacy assertions intentionally changed: indefinite FAST floor at excessive
import, stop-delay restart on missing samples, feedback ramp without new evidence,
voltage-only repeated wire commands and upward changes before physical response.
Activation fixtures now provide sufficient measured discharge headroom for their
intended positive-start lifecycle; separate tests cover an insufficient start
reserve. Phase lifecycle fixtures now provide explicit coherent source timestamps;
the separate simulation matrix exercises asynchronous clocks rather than assuming HA
fixture publication order represents acquisition order.

## Hardware validation sequence (not executed)

1. **Preparation:** review the 100 W reserve and headroom-utilization tradeoff;
   record voltage/current limits, 3,500 W discharge configuration, regulation
   interval and 90 s stop delay. Enable level 3 only for the controlled test.
   Confirm current owner/Remote authority through the existing explicit workflow.
2. **Nighttime FAST:** stable known household load, negligible PV. Record at least
   10 minutes after start: physical EV and battery power, complete site meter,
   generations, requests, hard ceilings, selected/acknowledged points, commands
   and reversals. Confirm a stable point and no repeated ramp on reused sources.
3. **Household disturbance:** apply/remove a known load. Record transient peak and
   time above 100 W, battery response and settling. Include a load large enough
   to exhaust EV budget; distinguish uncontrollable household discharge from EV
   demand. Verify a hard reduction is not held behind a delay.
4. **1p minimum:** establish 1p/6 A. Approach import from below 50%, then hold
   slightly above it for >10 s; verify one deliberate pause. Remove the load
   gradually and check restart hysteresis, allowing normal station rejection.
5. **3p lockout:** establish confirmed 3p/6 A with authoritative phase restriction.
   Verify current adjustment within 3p remains possible. At measured 230 V check
   approximately 2,070 W sustained import allowance and pause above it. Separately
   reduce battery headroom until 3p minimum is unaffordable: pause immediately,
   regardless of the grid allowance or stop timer.
6. **Phase availability:** let the station permit its transition and let the
   existing bounded probe confirm it. With persistent import, verify an appropriate
   1p point and the lower minimum allowance. Do not infer an expiry from elapsed
   manager retry time or generic BUSY.
7. **Measurement interruption:** while a 90 s stop delay is running, interrupt EV
   reporting at 20/40/89 s and across 90 s. Record the unchanged start/deadline,
   conservative budget pause where applicable, and absence of positive increases.
   Repeat a source-stale but HA-refreshed sample. Capture session availability
   reasons and exact OCPP source times to resolve the historical unknown gap.
8. **Reenable lockout:** after a deliberate zero-current pause, request recovery
   during station lockout. Confirm temporary rejection and bounded existing
   retries until actual acceptance, no invented deadline, no automatic Local
   takeover and no repeated accepted equivalent current/phase command.

Stop the hardware experiment if measured behavior disagrees with the budget or
if an authority/transaction fence fails. Investigate with source-timestamp logs
before changing the station contract. An authoritative future lockout reason and
expiry would require a separate station/manager design; it is not implemented or
required here.
