# beta.9 Fronius/OCPP measurement synchronization investigation

Investigation date: 2026-10-09. Branch: `codex/beta9-regulator-safety`.
This is an architectural investigation, not a regulator change or hardware test.
The uncommitted beta.9 implementation, tests, configuration, previous reports and
metric artifact are preserved. No old-budget holding policy is approved or
implemented by this report.

## 1. Findings and recommendation

**Existing OCPP evidence can establish that a physical EV power value was read,
but the currently exported Fronius/OCPP metadata cannot generally establish that
all powers describe the same physical acquisition interval.** Both streams
advancing is useful evidence of progress; it is not proof of synchronization.

The local Fronius implementation is newer than the September polling analysis:
it already has selective FAST/SLOW polling, defaulting to 5/30 seconds. Its
single coordinator produces a consistent software snapshot after sequential
reads. That does not make different devices, model blocks, or their internal
measurement caches physically simultaneous.

The local wallbox implementation polls its SDM630 approximately every second.
Ordinary OCPP MeterValues reports default to every ten seconds and carry the
cached poll-completion timestamp. Thus the OCPP source time is substantially
better evidence than HA publication time, but **it is not a device-originated
ADC acquisition timestamp**. This refines the stronger wording in earlier notes.

There are two separate opportunities: remove an unjustified soft admission veto
when the minimum already fits valid hard evidence, and release a discrete upward
step after sufficiently correlated physical response. Neither opportunity proves
safe continuation through an intervening ambiguous generation. For the five
blocked examples, a specified counterfactual start has three favorable and two
unfavorable source-order outcomes; see section 7. This is more precise than
treating all five as having the same transient outcome.

**Recommendation:** pursue stronger evidence correlation together with the
existing response-state model, conditional on verified source timing bounds.
First establish those bounds using existing read-only observability and exact
firmware/source versions. Then implement only decisions proved safe by the
resulting evidence. Keep the current protective response where evidence is
ambiguous. Do not treat a 20-second hold, repeated similar values, a successful
ACK, or a new HA report as a synchronization guarantee. Existing exported data
alone does not justify promising all five restarts and higher steady utilization
without additional assumptions.

## 2. Scope and inspected sources

The available source repositories were inspected locally, without changes:

| Repository | Inspected state | Deployment qualification |
|---|---|---|
| Wallbox Manager | current uncommitted beta.9 review on `codex/beta9-regulator-safety`, base `eb792c3` | The code under investigation; the supplied logs predate these uncommitted changes |
| Fronius PV Manager | clean `main`, `b464f91`, manifest `v1.2.0` | Local source; exact deployed revision/options at each hardware test are not recorded in the supplied evidence |
| wallbox-stationary | clean `main`, `2baf0da` | Read only; exact deployed revision, SDM630 firmware and clock synchronization state remain unverified |

Principal inspected files and responsibilities:

| Source files | Evidence used |
|---|---|
| Manager `pv_budget.py`, `pv_regulators.py`, `pv_optimum.py`, `pv_surplus.py`, `power_history.py`, `freshness.py` | Raw versus smoothed powers, budgets, source boundaries, response and freshness gates |
| Manager `protocols/ocpp/common/metering.py`, `common/inventory.py` | Wire timestamps, normalization, MeterValues and TransactionEvent handling |
| Manager `runtime.py`, `core/telemetry.py`, `session_ledger.py`, `session_entity.py` | Generation fencing, source arbitration, session attribution, HA projection and one-second refresh |
| Manager `protocols/ocpp/v21/control_runtime.py`, `pv_input_diagnostics.py`, `pv_diagnostics.py` | Voltage/current evidence, diagnostic source fields and actual consumed readings |
| Manager `tests/test_pv_beta9_simulation.py`, `tests/test_pv_beta9.py`, `docs/validation/beta9-regulator-metrics.json` | Deterministic plant, source cadence, existing scenarios and unchanged baseline comparison |
| Manager `docs/beta9-implementation-report.md`, `docs/beta9-followup-investigation.md`, `docs/fronius-polling-analysis.md`, `docs/metering-runtime-state.md`, `docs/diagnostic-logging.md` | Previous findings and their limitations; old polling proposal is not current Fronius implementation |
| All three `.analysis/pv_*test.log` files | Available historical report timestamps and partial measurement evidence |
| Fronius `coordinator.py`, `transport.py`, `const.py`, `model_entity.py`, `sensor.py`, `storage_entity.py`, `entity_naming.py` | Sequential polling, publication, semantic power derivation and identity |
| Fronius `register_maps/model_160.py`, `model_122.py`, `_entity_catalog.py`, `docs/polling.md` | Raw time-register candidates and polling contract |
| Station `backends/sdm630.py`, `backends/modbus.py`, `domain/wallbox.py` | Physical meter reads, cache timestamp assignment, validity and domain measurements |
| Station `ocpp_runtime/metering.py`, `station.py`, `transactions.py`, `config.py` | Meter groups, transmission schedule, event timestamps and physical status refresh |
| Station `docs/meter-architecture.md`, `docs/ocpp.md`, Fronius register YAML maps | Supporting descriptions; implementation takes precedence over outdated prose |

Useful source anchors:
[Fronius coordinator](/home/elutz/Projekte/fronius-pv-manager/custom_components/fronius_pv_manager/coordinator.py:243),
[semantic site power](/home/elutz/Projekte/fronius-pv-manager/custom_components/fronius_pv_manager/sensor.py:540),
[SDM630 timestamp assignment](/home/elutz/Projekte/wallbox-stationary/backends/sdm630.py:91),
[OCPP sample construction](/home/elutz/Projekte/wallbox-stationary/ocpp_runtime/metering.py:59),
[manager normalization](/home/elutz/Projekte/wallbox-manager/custom_components/wallbox_manager/protocols/ocpp/common/metering.py:134),
[session source attributes](/home/elutz/Projekte/wallbox-manager/custom_components/wallbox_manager/session_entity.py:117).

## 3. Actual measurement architecture

### Fronius

One coordinator and a shared cancellation-safe I/O lock own the integration's
Modbus endpoint. Configured device views are polled sequentially, and selected
model payloads are read sequentially. Payload reads are split into at most 100
registers per request. A model 203 payload of 105 registers therefore crosses two
requests; multi-request atomic acquisition is not established. Even a single
Modbus response does not establish a device's internal refresh/averaging period.

FAST models are 103, 124, 160 and 203. SLOW models are retained between their due
cycles. New successful reads create new model objects, even for unchanged
numbers. Raw model entities avoid rewriting retained unchanged SLOW snapshots.
Failed reads become unavailable rather than relabeling retained values as fresh.
The optional Solar API HTTP request follows the Modbus work and precedes
publication; its three-second timeout can add delay. That timeout does not bound
all Modbus, queue, scheduler or source-cache latency.

| Consumed signal | Physical/software source | Meaning and dependence |
|---|---|---|
| Battery discharge | Classified storage-discharge channel, model 160 `DCW` | DC-side power, not an independent AC household measurement |
| PV production | Sum of classified MPPT model 160 `DCW` channels | DC-side sum; current semantic code permits partial MPPT summation when a channel value is absent; no new completeness guarantee is inferred here |
| Grid import/export | Same model 203 signed total `W` | Positive means site import, negative export; the two entities are `max(W,0)` and `max(-W,0)`, not independent meters |
| Total consumption | Model 103 inverter AC `W` plus signed model 203 grid `W`, clipped at zero | Derived whole-site AC demand including EV; requires one mapped inverter and meter; not independent non-EV household power |
| SoC | Model 124 `ChaState` | Policy input, not proof of instantaneous battery response |

The snapshot dataclasses contain decoded data and availability, **no per-model
read-start/read-end interval, acquisition time or exported sample counter**.
The endpoint connection generation and discovery lease describe transport and
topology authority, not power acquisition. DEBUG poll elapsed time is per unit;
it excludes other units, lock wait and subsequent HTTP time and is not a
per-register acquisition bound.

The named Fronius entities in the nighttime logs match these semantic roles.
This supports the mapping, but does not independently prove their deployed
firmware or hardware cache timing. DC battery/PV and AC site/EV powers also have
conversion/loss differences. The two budget equations are two conservative
arithmetic checks, not statistically independent physical measurements. Exact
AC/DC balance and a universally sufficient 100 W error allowance are not proved
by synchronization alone.

The [official GEN24 Modbus manual](https://manuals.fronius.com/html/4204102649/en-US.html)
supports sequential requests, contiguous reads and at least a one-second request
timeout. Its inspected material does not establish a common acquisition latch
or a verified maximum age/skew for the required powers. Those recommendations
are communication guidance, not a physical sampling guarantee. The manual's
linked register ZIP returned HTTP 404 during this investigation; no missing
register semantics were inferred from that failure.

### Available Fronius time registers are not yet a synchronization contract

Model 122 has `TmSrc` and `Tms` (the latter described in the map as seconds since
2000-01-01 UTC). It is a SLOW model. Model 160 has per-module `Tms` and `TmsPer`;
the map explicitly describes `TmsPer` as unsupported by Fronius. Per-module `Tms`
entities are diagnostic and disabled by default. Their existence in a generic
register map does not prove live validity, epoch, update association or atomicity
on the installed inverter. The consumed power entities do not bind these fields
to `observed_at`, and no common timestamp links them to model 203 meter power.

These are candidates for read-only verification, not fabricated device
timestamps and not evidence currently available to the regulator. A device
clock value read near a power value is not necessarily that value's acquisition
time. Confirm its documented and measured association before using it.

### OCPP and the station's physical meter

The station's SDM630 backend uses one shared RTU worker. Approximately once per
second it reads phase U/I/P from FC04 `0x0000`, 18 registers, then explicit total
active power from `0x0034`, two registers. It publishes the two successful reads
together under a lock and sets `ts = time.time()` **after** both reads complete.
This gives atomic software publication, not simultaneous physical sampling of
the two blocks. The total is the separately read `P`, not a phase sum or U×I
estimate. Energy is a separate slower read and timestamp.

The domain `Measurements` object preserves `ts`, per-phase values, explicit total
power and validity. OCPP `build_meter_values` emits the physical U/I/P and total
power in one group with that cached timestamp. Energy has its own group/time.
Negative phase import power is clamped to zero by the station encoder; invalid
samples are omitted. The manager uses explicit total active import power for
session power and does not replace it with requested current or summed phases.

MeterValues waits the configured interval (default 10 s), reads the cache and
awaits the OCPP response before the next sleep. Thus report cadence includes
call/processing delay; it is not a synchronized ten-second sampling trigger.
Source time remains the last successful physical-meter poll time.

TransactionEvent has two distinct time layers: its envelope timestamp describes
the transaction/status event, while embedded MeterValue timestamps retain the
meter cache times. The current station also refreshes physical CP/status at a
nominal 60 s deadline; an event can carry the existing cached meter sample.
Neither a fresh event timestamp, its sequence number, nor a Charging state means
that an embedded watt value is newly acquired. Phase-mode reports likewise
establish configuration, not EV watts.

The manager normalizes MeterValues into EVSE/station scope and embedded event
meters into the transaction ledger's explicit scope. It retains source and
handler-reception times. Connection/boot/runtime fencing, transaction attribution,
out-of-order checks and equal-time conflict invalidation remain authoritative.
Session power selects a valid scoped observation matching the ledger's power
timestamp and value. EVSE-parent readings require unambiguous attribution.
No unrelated connector, transaction or older conflicting reading is substituted.

The session sensor writes HA state on ledger changes and every second while
active. Its `observed_at`/`received_at` attributes come from the selected meter
observation; the periodic HA write does not acquire a new meter sample. The
beta.9 reader uses `observed_at` when available, avoiding a one-second false
measurement generation caused by publication alone.

## 4. Timestamp, cadence and coherence table

| Layer / timestamp | Origin and precision | Nominal cadence / age guard | What it proves; what remains unknown |
|---|---|---|---|
| Fronius device acquisition | Not exported with consumed power; register-cache timing unverified | Physical cadence/maximum cache age unknown | No common acquisition generation currently established |
| Fronius integration poll | HA host monotonic scheduling; per-unit elapsed DEBUG to milliseconds | FAST default 5 s, configurable 2–30; SLOW default 30 s, 10–300; SLOW ≥ FAST | Ordered reads in one refresh; not simultaneous device sampling; completion and HTTP can extend publication interval |
| Fronius HA `last_reported` | HA clock, microsecond representation | Successful state writes around FAST cadence; manager live age limit 90 s | A report occurred, including unchanged values; cannot detect a stale internal device cache or time-order it against SDM630 acquisition |
| HA `last_updated` / `last_changed` | HA clock, microsecond representation | Only relevant attribute/value changes | Not a sampling clock; unchanged valid values can have old timestamps |
| SDM630 `ts` / OCPP group timestamp | Wallbox OS wall clock after both measurement reads; serialized ISO UTC with fractional seconds | Poll aims at 1 s; local validity requires latest successful read ≤3 s by monotonic age | A successful cache update from physical meter reads; unknown meter internal integration period and block-read skew |
| SDM630 energy `ts_energy` | Wallbox OS clock after energy read | At least 5 s between attempts; monotonic freshness 15 s | Separate energy read; cannot supply instantaneous EV power at a transition |
| OCPP MeterValues send | Station task reading the cache | Configurable positive integer seconds, default 10; response wait adds time | Transport publication of cached evidence; no forced synchronous SDM630 read |
| TransactionEvent envelope / `seq_no` | Station event/status clock and lifecycle sequence | Event-driven; CP/status inspection ~1 s; physical refresh nominal 60 s | Lifecycle order/status evidence; not a meter acquisition ID |
| Manager `received_at` | HA host wall clock in normalization handler | Per received group | Handler observation time; includes unknown transport/dispatch delay; not an acquisition timestamp |
| OCPP observation `valid_until` | `min(source+120 s, receipt+120 s)` | Session attribution guard; beta.9 live control also requires source and HA age ≤90 s | Maximum accepted software age, not an error bound or guarantee of constant load during that age |
| FAST source tuple / command evidence | Six source boundaries; EV `observed_at`, Fronius normally `last_reported` | Ordinary feedback requires every component to advance; 20 s response window does not authorize safety holds | Prevents repeated feedback on the same tuple; mixed timestamp origins prevent a physical ordering proof |

Wallbox and HA clocks are separate. The inspected station path uses the OS clock;
it does not discipline it from Heartbeat responses. Actual NTP/chrony offset,
clock steps and uncertainty were not measured. Manager rejection of timestamps
more than five seconds in the future is a plausibility filter, not a two-sided
clock synchronization guarantee. Microsecond representation is not microsecond
accuracy. Apparent receipt-minus-source delay combines clock offset, meter-cache
residence, send, transport and processing; it cannot isolate network latency.

OCPP out-of-order source samples and equal-time conflicts can be detected within
the accepted generation. Fronius publication order can be observed, but repeated
HA reports cannot reveal repeated internal device samples. Software snapshot
object identity is not exported as a common cross-entity measurement ID.

## 5. Historical log evidence and limits

The extraction counted complete `WBMGR subsystem=pv` JSON records and separately
decoded only complete nested input objects within truncated records. No missing
JSON tail or timestamp was reconstructed. Deduplicating each entity by its
reported time gives a **sample of logged reports**, not a complete device trace.

| File | Complete / invalid PV JSON lines | Recoverable timing evidence |
|---|---:|---|
| `pv_maximum_test.log` | 239 / 0 | Earlier schema does not supply the later per-input source metadata used in this timing analysis |
| `pv_maximum_ohne_pv_test.log` | 183 / 460 | Fronius report medians ~5.001–5.002 s; zero acquisition timestamps in recovered Fronius inputs; 16 distinct EV source timestamps, median source interval 10.012 s |
| `pv_optimum_ohne_pv_test.log` | 203 / 264 | Fronius report medians ~5.003–5.005 s; zero acquisition timestamps in recovered Fronius inputs; only one recoverable EV source timestamp, insufficient for an EV-cadence estimate |

For the recoverable PV_MAXIMUM EV objects, apparent receipt-minus-source median
is 0.649 s and maximum 1.081 s. These are observed offsets in that subset, not
latency bounds. HA report intervals for the recovered EV objects have median
1.182 s, illustrating repeated projection of slower source evidence.

A concrete PV_MAXIMUM record at file line 8 evaluates around
`2026-10-08T18:06:15.528089Z`. Fronius reports cluster at
`18:06:12.634041Z` (consumption), `.636722Z` (PV), `.637922Z` (import),
`.639271Z` (export), and `.641660Z` (battery discharge). All source-acquisition
fields are unavailable. EV source time is `18:06:12.382514Z`, reception
`18:06:12.904371Z`, and HA report `18:06:15.302120Z`.

This proves nearby HA publication and distinct EV source/receipt/publication
times. It does **not** prove that the Fronius values were acquired after that EV
sample. At the same record battery discharge is 444.47 W and derived AC site load
388.2 W; their difference is compatible with losses and/or timing differences,
but the record does not identify which. Neither an exact loss model nor an
acquisition bound can be fitted from that single discrepancy.

## 6. Timing and the two-world ambiguity

```mermaid
sequenceDiagram
    participant M as Manager
    participant W as Wallbox / EV
    participant S as SDM630 cache
    participant F as Fronius devices / coordinator
    participant H as HA states
    M->>W: Send current/phase setpoint
    W-->>M: ACK / accepted electrical setpoint
    Note over W: Physical EV response occurs later or never
    W->>S: Physical current changes; next scheduled reads detect it
    Note over S: ts assigned after phase and total reads
    F->>F: Sequential model/device reads of internal caches
    F->>H: Publish after poll and optional HTTP
    Note over H,M: New site values can coexist with old EV watts
    S-->>W: Latest cached U/I/P and source-read timestamp
    W-->>M: MeterValues or event carrying cached meter group
    M->>H: Scoped session watts + source/receipt timestamps
    Note over M,H: A 1 s HA refresh can reuse the same source sample
```

The relative order of Fronius publication and OCPP receipt can reverse. Neither
arrow fixes the unknown devices' actual acquisition windows.

Given old EV=0 and newer site D=C=3,080 W, PV=G=0, the following remain
indistinguishable until discriminating physical evidence arrives:

| World | Actual EV | Actual non-EV household | Consequence of 1,380 W setpoint |
|---|---:|---:|---|
| A | 1,380 W | 1,700 W | Continuing at minimum is physically feasible in the idealized lossless plant |
| B | 0 W | 3,080 W | A future EV response would raise demand to ~4,460 W; the minimum is not supported by the current discharge budget |

The conservative arithmetic returns `0+3500−3080−100 = 320 W`. A Fronius
increase alone cannot identify its load. An ACK cannot choose world A. Multiple
unchanged site reports add no independent information that eliminates world B.

A newly read, attributable positive EV power value rules out **EV=0 at that
measurement's physical sampling interval**, subject to meter validity and timing
uncertainty. It does not retroactively prove EV power at an earlier Fronius
interval, or exclude a later unrelated household increase. Per-phase measured
current supports physical flow, but U×I alone is apparent-power evidence and must
not replace explicit active watts. CP Charging status is not a watt measurement.

To prove ordering from timestamps, physical acquisition intervals and clock error
bounds are needed. For EV interval `[e0,e1]` and site interval `[f0,f1]`, a bound
such as `e1 + clock_error < f0` can establish ordering. It still needs a justified
bound on EV/load changes between intervals to transfer a watt value. Overlapping
intervals alone do not prove atomic sampling either. With no certified cache-age
or slew bound, interpolation and repeated-value bracketing remain assumptions.

### What the current guards do and do not establish

* `E_low` retains the lesser of the preceding/current EV samples until all site
  source boundaries cross the EV boundary. This avoids spending an upward EV
  observation against older site values in the idealized model. Between actual
  device samples, the minimum of two endpoints is not a mathematically proved
  lower bound without a continuity/variation assumption.
* In the real exported data, site boundaries are HA reports. Their crossing an
  EV cache timestamp cannot prove Fronius acquisition occurred later. The guard
  is protective bookkeeping, not a synchronization certificate.
* Response confirmation checks a new tuple relative to the tuple saved on ACK
  and EV watts near the offer. The saved tuple is not an exact acquisition-time
  fence at command dispatch. It prevents repeated speculative increases but does
  not establish cross-device physical ordering or command causality by itself.
* After genuinely correlated physical response, retaining the old lower EV basis
  or halving headroom forever is unnecessary. Releasing it merely because two HA
  report times advanced is not the same evidence standard.
* Smoothed PV/load are time-weighted HA-event histories used by the soft policy.
  FAST budget evidence uses raw power. Smoothing is not a remedy for acquisition
  skew and must not conceal a hard constraint.

## 7. Five restart-case assessments

At the end of each original trace: E=0, D=1,700 W, PV=0, household=1,700 W, G=0;
both raw bounds are 1,800 W and the 100 W reserve leaves B=1,700 W. All admit a
1,380 W minimum **at that initial boundary under the model**. Four also have an
import-restart latch; its existing projection adds all restored EV demand without
crediting supported battery supply. That is a distinct veto, not evidence that
the battery minimum is unsafe. A proposed correction must retain the site meter
as authority and separately justify incremental battery support.

The original blocked traces never send a restart, so they cannot demonstrate a
response. To investigate ordering, an isolated counterfactual schedules one
minimum command at absolute simulation second 360 after the settled pause. It
uses the existing 5 s site / 10 s EV cadences, the respective offsets and the
same first-order battery response. It keeps the command physically applied solely
to inspect the evidence; it is **not a controller that is allowed to ignore a
low budget**. For the variable case the original trace sent five commands; its
sixth would have ACK=1 s and physical-response delay=10 s after ACK.

| Original case | Initial admission / import latch | ACK / physical response | First site sample containing response | First EV sample containing response | Conservative continuation assessment |
|---|---|---|---:|---:|---|
| `load_up/3/8` | Battery-safe / set | 363 / 371 | 375 | 380 | At 375 B=363.125 W with E still zero: minimum must pause under current evidence; EV response becomes establishable at 380 if maintained |
| `load_up/3/8/offset3` | Battery-safe / clear | 363 / 371 | 375 | 373 | EV evidence arrives first; B remains sufficient throughout this injected trace; site correlation completes at 375 |
| `load_up/1/15` | Battery-safe / set | 361 / 376 | 380 | 380 | Both update on the same idealized tick; B remains sufficient; real separate devices do not inherit this atomic tick |
| `load_up/1/15/offset3` | Battery-safe / set | 361 / 376 | 380 | 383 | At 380 B=363.125 W before EV evidence: current policy requires pause; response becomes establishable at 383 if maintained |
| `load_up/variable/offset3` | Battery-safe / set | 361 / 371 | 375 | 373 | EV-first ordering; sufficient B throughout this injected sixth-command trace; site correlation completes at 375 |

Times are simulation seconds, not measured wall-clock latencies. At a deficient
boundary, sampled battery is 3,036.875 W, total load 3,080 W and import 43.125 W;
combining these with still-zero EV yields the 363.125 W ceiling. Later, with
E=1,380 W and settled site data, B approaches 1,700 W again.

Thus better correlation can avoid unnecessary holds **after** discriminating
samples are available. It cannot create the missing EV sample at 375/380 in the
two site-first cases. Under the present observations and uncertainty rule, their
pause at that boundary is unavoidable. An earlier physical EV report or a
verified independent bound on non-EV demand could change that conclusion; using
future knowledge of the simulation's commanded response cannot.

These three favorable outcomes do not prove that those scenario names always
restart successfully. Moving the command relative to the clocks or varying
latency can reverse the order. Physical restart feasibility, safe initial
admission and safely provable continuation are distinct questions.

## 8. The steady-state 230 W step

At E=2,530 W, D=3,030 W, household=500 W, PV=G=0 and B=2,900 W:

* **Selection:** 12 A / 2,760 W fits B. Expected settled battery power is 3,260 W
  in the idealized plant, 93.14% of the 3,500 W limit. Of the current 470 W gap,
  100 W is explicit reserve, 140 W is unavoidable rounding below B and 230 W is
  the extra point blocked by the soft half-headroom rule. `E_low=E` here; repeated
  generation gating does not explain the permanent gap.
* **During response:** with command at 360, ACK at 363, response at 371, site
  sample at 375 and EV sample at 380, the 375 sample is E=2,530, D=3,252.8125,
  C=3,260 and G=7.1875 W. B becomes 2,677.1875 W, below the 2,760 W offer. The
  hard check bypasses response waiting and forces reduction. The ordinary
  generation/response gates do not prevent this false deficit in world A.
* **After response:** at 380 the EV sample is 2,760 W and the model's site sample
  is current. B≈2,900.2246 W and later converges to 2,900 W. The step is then
  supported. This does not authorize ignoring the earlier ambiguous boundary.
* **Independent household increase:** the same partial tuple could mean the EV
  stayed at 2,530 W while household rose to 730 W. A later 230 W EV response
  would require ~3,490 W battery, above the existing 3,400 W reserve-adjusted
  target even though below the absolute maximum. A larger household step can
  also threaten 3,500 W. Automatically attributing every site increase to the
  command masks this case and weakens the established budget.

In an EV-first timing variant the lower EV basis protects the older site sample
until it advances, without necessarily blocking the new offer. Thus improved
response-state modeling can release headroom conditionally; it is not sufficient
to guarantee stability over every site-first transition. Mathematical steady
feasibility and safe transient operation must remain separate acceptance tests.

## 9. Comparison of approaches

| Approach | Required evidence / assumptions | Ambiguity and restart behavior | Utilization and stability | Complexity / compatibility |
|---|---|---|---|---|
| A. Existing conservative review policy | Current raw bounds, 100 W reserve, lower EV basis, generation/response guards; no old-budget hold | No new inference across unknown source timing; double-minimum veto unnecessarily blocks initial examples, and site-first gaps remain ambiguous | Stable existing 35 simulations, but 2,530/3,030 W constant result and five paused load-up cases do not meet the follow-up objective | Already implemented; preserves safety/retry architecture but is not an acceptable final utilization solution |
| B. Stronger measurement correlation | Trustworthy acquisition/read intervals, provenance, validated device-cache and clock bounds; only available fields may be used initially | Existing EV source time rejects stale/replayed evidence; Fronius HA time alone cannot resolve the two worlds. Once a provable joint boundary exists, minimum admission and continuation can be evaluated there | Can eliminate avoidable mixed-generation corrections; cannot bridge periods with no distinguishing evidence. Better observed timings reduce pauses, not prove them impossible | Moderate/high; bounded evidence history beside existing budget module, no competing regulator. Exposing absent provenance would be a separately reviewed integration change, not merely a manager calculation |
| C. Improved response-state model | Separate sent/ACK/observed-response states, source-generation fences, then site-response correlation; use measured watts | Admit an affordable minimum, inhibit further rises until physical response, retain hard reductions. After valid correlation remove temporary margins. With current site metadata, correlation still has an unresolved uncertainty | Can remove the permanent half-headroom trap after valid evidence; alone cannot prevent hard-bound cycling in site-first gaps or safely infer a never-observed response | Moderate; fits the existing one-controller/one-solver architecture, preserves timeout, lockout and dispatch fences |
| D. Additional existing physical evidence if available | Independent non-EV demand measurement with known scope, timing and accuracy, or earlier trustworthy EV watt observations; verify availability first | Non-EV upper bound can distinguish household from EV load without subtracting stale EV. Another whole-site meter or derived C−old E adds no independent information | Potentially supports safe continuation and high utilization; still subject to latency, AC/DC losses, demand and electrical limits | Higher integration/verification effort; architectural option only, no new hardware requirement or dependency is imposed |

Per-phase Fronius grid data alone does not identify which downstream load changed.
Station SDM630 per-phase data is a physical EV source, but currently shares the
same OCPP report group/cache cadence as total watts. Reading it through a different
interface is not automatically independent or fresher. A transaction status event
may incidentally deliver a more recent meter sample, but its lifecycle sequence
does not guarantee one. Raw model 122/160 time fields are investigation candidates,
not a ready-made solution B.

All approaches must retain G as whole-site signed grid power, pursue zero above
the minimum, and apply at most M/2 sustained import only to the currently reachable
minimum. The 10 s confirmation and recovery hysteresis remain separate from the
battery bound. Restoring EV demand must be projected against independently
supported supply; removal of EV load alone must not clear a restart veto. Actual
phase restrictions, including a locked 3p minimum, remain authoritative.

The ≤10% permanent-reserve requirement applies where a higher point is safely
achievable. A transient evidence gap is not permission to label an unnecessary
permanent half-headroom margin as a hard limit. Conversely, a soft utilization
goal does not justify spending a point above the established hard budget.

## 10. Proposed implementation plan — not implemented

1. **Establish the evidence contract first.** Record installed versions, selected
   entities, actual polling/report intervals, Fronius topology and DC/AC meaning.
   Verify what source-time registers actually return and what they timestamp.
   Specify acquisition/cache-age/clock uncertainty rather than equating reports
   with acquisitions. If adequate bounds cannot be established, explicitly retain
   the unresolved limitation; do not promise uninterrupted restart.
2. **Represent provenance without fabricating it.** Propose a small typed evidence
   record containing source identity, transaction/connection context, source/read
   interval when known, receipt/publication time, validity and an evidence quality
   flag. Unknown acquisition bounds stay unknown. Do not migrate persistence or
   alter SoC policy merely to add transient correlation state.
3. **Separate admission from progression.** Remove the universal 2×M soft veto
   only with tests for minimum admission against B and an independently justified
   restart-import projection. ACK opens a response-pending state, not a larger
   EV measurement. Keep station BUSY and phase restrictions outside this inference.
4. **Release headroom at proven boundaries.** After physical EV response and
   sufficiently bounded site correlation, allow the next common-solver point up
   to the existing hard budget, using one explicit reserve accounting. Preserve
   the one-controller architecture, interval semantics, no-rise-on-reused-data
   rule and dispatch revalidation. Timeouts must not manufacture response proof.
5. **Handle ambiguous frames explicitly.** Reduce/pause when the current safe
   bound cannot support a positive point. Distinguish `awaiting_EV_evidence`,
   `site_acquisition_unknown`, `household_bound_missing` and actual hard excess
   in diagnostic proposals. Do not implement old-budget retention as a default.
6. **Test causally distinct worlds.** Keep the 35 cases and prior artifacts, add
   injected single-minimum/230 W transitions at multiple clock phases, a never-
   responding EV, independent household steps, clock offsets/steps, cache delay,
   serial read skew and missing/out-of-order samples. A model with exact site
   timestamps must not be used to validate HA publication-time assumptions.
7. **Review before implementation.** Determine whether verified timing evidence is
   enough for B+C. If not, choose a separately approved evidence enhancement or
   acknowledge the remaining safety/utilization conflict. No implicit relaxation
   is part of this plan. Full regression and subsequent controlled hardware tests
   belong to the later implementation task.

## 11. Remaining evidence and hardware validation needs

No device connection or live polling was performed here. The next evidence
collection should use existing facilities where available and remain separate
from regulator deployment:

* Record exact GEN24, Smart Meter, BMS, SDM630, station and HA/integration versions;
  capture entity mappings, meter topology and configured intervals. Confirm whether
  the local reviewed code matches the deployment.
* Measure or obtain firmware-specific internal refresh/integration windows and
  cache age. Verify model 160/122 time values, unavailable sentinels and any
  documented association with 103/160/203 samples; do not enable controls or write
  registers to infer this.
* Retain complete input records plus command attempt/ACK and physical watts.
  Capture existing station meter-cache times, OCPP group times, handler receipt,
  HA publication, and Fronius per-unit poll timing. If per-block read intervals
  are missing, state that gap before proposing temporary instrumentation.
* Record the wallbox/HA clock offset and uncertainty throughout capture, including
  possible synchronization steps. A matching timezone is not clock synchronization.
* Use controlled minimum restarts and a 230 W step only in a separately approved
  hardware test; include an independent household disturbance and an EV that delays
  or refuses response. Compare full timelines and count site-first gaps, rather
  than deriving a guarantee from average latency.
* Validate DC/AC conversion and meter error against the 100 W reserve. Report
  battery physical excursions separately from command-budget compliance. An
  inverter-clamped simulation cannot prove physical limit enforcement.
* Preserve phase/reenable lockouts, Local authority, session identity, stop timers,
  command deduplication and all existing measurement rejection rules during any
  later test. Do not infer a 600 s deadline or new supply capacity from generic BUSY.

## 12. Investigation validation and stopping point

All 35 existing beta.9 simulation results were recomputed without editing their
code and exactly match the current metric artifact. Two isolated mathematical
experiments inspected (a) a single minimum restart for each of the five cadence
variants and (b) the 230 W upward step. They are explanatory counterfactuals, not
new successful controller or hardware acceptance tests. The full Python/frontend
suite was not rerun because no implementation changed, as allowed by this task.

The before/after file-hash audit covers all 193 pre-existing tracked and untracked
non-ignored workspace files. The only intended new file is this report; no
pre-existing file is to change. The original uncommitted git status, feature
branch, prior reports and comparison artifact remain intact. Both inspected
external repositories remain clean. No commit, merge, tag, release, configuration
change, firmware change or deployment was made.

**Next decision:** verify the missing timing/accuracy contract and then assess
an evidence-gated B+C implementation. Do not approve a budget hold or claim that
existing timestamps already solve the ambiguity. Stop here; no proposed control
change has been implemented.
