# PV FAST investigation and five-value power diagnostics

Date: 26 October 2026. Implementation base: validated `origin/develop`
`04cf93d6e980fe17cf1905926f576328ce1ae33f` (published v0.4.0-beta.17).
Branch: `feature/pv-fast-power-diagnostics`. Scope: diagnostics and analysis only.

## Requirements and implementation

Affected requirements: PV-DIAG-01–10, PV-ARCH-05/06, PV-BAL-07,
PV-RESP-01–07, PV-TUNE-01–07 and WBM-ARCH-01–12. The task explicitly approves
expanding the former cause-only measurement subset to five power values. The
[diagnostics specification](specifications/pv-decision-diagnostics.md) records
that narrow supersession. No control policy is changed.

`pv.py:power_context()` projects five scalars from canonical held input values:

| Key | Source / normalization |
| --- | --- |
| `ev_actual_w` | Selected EV's canonical `ev_power()` result, captured once for the tick. |
| `grid_w` | `grid_import_power - grid_export_power`, positive import. |
| `pv_w` | `leistung_pv`, total positive PV production. |
| `battery_w` | Existing `signed_storage(storage_charge_power, storage_discharge_power)`: positive charge, negative discharge, both positive effective zero, either missing unknown. |
| `household_w` | Existing Fronius total-consumption contract: `max(0, leistung_verbraucher - ev_actual_w)`. |

Missing or explicitly unavailable source values remain JSON `null`. A missing
selected EV is not replaced by offered power or the planner's observation-only
fallback. Household power cannot be determined without both total consumption
and selected EV power. Source values and last-value-held semantics are unchanged.
The zero floor is the existing consumption contract, not new measurement repair.

The projection travels with the tick's copied values and selected EV power across
persistence awaits to target, input-condition and decision events. Out-of-tick
target invalidation uses held observations at that event. Cause-specific fields
are merged with the projection before serialization so overlapping keys occur
once. Existing thresholds, point information, reasons, identifiers, INFO severity
and diagnostic-level filtering survive. Semantic deduplication is unchanged;
power digits never enter its signature. No new events, polling, publication,
worker, persistence operation or command is introduced by logging.

## FAST code-path analysis

Inspected current files: `pv.py`, `pv_core.py`, `pv_soc.py`, `pv_economics.py`,
`grid_point.py` and `grid.py`.

1. A regular eligible PV tick captures held HA inputs and selected actual EV
   power. Profile-specific target T feeds the shared SoC policy. FAST can operate
   without PV production when SoC permits; it does not estimate inverter capacity.
2. `Controller.decide()` computes signed grid G = import minus export. In FAST,
   G <= configured deadband (default +100 W) selects the next legal upward point
   using `Envelope.navigate()`. This includes near-zero grid exchange; battery
   regulation that hides available discharge behind near-zero grid is why bounded
   probing exists. Navigation exhausts the current phase series before changing
   phases, using nominal 230 V points and actual advertised current steps/caps.
3. A true accepted 0 A to positive transition arms `startup=0`. Ten eligible,
   resolved cycles hold ordinary increases and reductions by default. Uptake
   reaching 50% early does not shorten this hold. Ordinary positive-to-positive
   ampere changes do not arm a fresh startup hold.
4. Further increases require actual EV power >=50% of the current nominal offer
   by default. This is an uptake threshold, not proof of complete EV, battery or
   inverter settling. The default three-cycle counter advances in resolved
   contexts and resets on accepted upward changes. Downward acceptance does not
   reset that counter. Each increase is limited to one legal step.
5. G >100 W takes the import branch: ideal power = actual EV power minus G.
   The full import is subtracted, not just the portion beyond 100 W. The existing
   phase-first solver selects the nearest nominal point within the current
   series and may skip multiple amperes downward. A downward correction is not
   subject to the three-cycle upward interval or EV uptake gate.
6. Default `pv_decrease_delay_cycles=0` acts on the first detecting eligible
   tick. At N=2, the first detection initializes the count to zero; the next two
   eligible detections of the same downward reason release the correction on
   detection three. The candidate is recalculated from current held inputs.
   An eligible disappearing/reversing condition clears the count. This is a
   reason-persistence delay, not a filter or a post-increase timer. Unresolved
   command periods do not add eligible cycles.
7. Independent economic accounting integrates raw positive import only while
   measured charging is active and import exceeds M/2. It uses the previous
   supported interval prospectively, preserves scope/history, and resets after
   strictly more than 15 s below/equal M/2. Exhaustion of the default 50 Wh
   budget overrides startup and ordinary reduction delay. Around 1p/6 A at
   230 V, M/2 is about 690 W: the quoted 260/294 W correction peaks need not
   accumulate economic Wh. Actual voltage/phase restrictions determine M.
8. `Grid` retains requested, acknowledged, observed and confirmed points.
   `point_uncertain` or an in-flight task yields `response_pending`; no ordinary
   stacked increases occur. Accepted starts controller timing but is not measured
   consumption. Current/phase confirmation/readback belongs to the existing
   execution path; confirmed offered amperes do not confirm that the home battery
   has finished responding. Stationary owns physical switching and protection.

## What the reported 26 October sequence proves

The task provides four excerpts from `pv1(1).log`; the complete file was not
available in the attachment or the repository search. Therefore this analysis
uses the supplied times/points/grid readings and code, not a claimed full-log
reconstruction or a verified record of the installed parameter values.

| Time | Supplied observation | Code explanation |
| --- | --- | --- |
| 10:28:02 | 1p/9 A to 1p/10 A, G=+43 W | Within default deadband; next-point exploration is permitted once uptake/cadence/response gates pass. |
| 10:28:12 | Reduction to 1p/8 A, G=+294 W | Import exceeds deadband; actual EV minus import is quantized into a lower nominal point. |
| 10:28:32 | 1p/9 A to 1p/10 A again | Grid exploration becomes eligible again after the intervening correction/recovery. |
| 10:28:42 | Reduction to 1p/8 A, G=+260 W | Same import-correction branch repeats. |

The established controller mechanism is asymmetric bounded exploration followed
by immediate multi-step deficit correction, with no ordinary positive-step
battery-settling hold. Quantization can turn a roughly one-ampere import peak
into a two-ampere command reduction. For illustration only, actual EV=2,200 W
minus 294 W gives 1,906 W; minus 260 W gives 1,940 W. Both are closer to 1p/8 A
(1,840 W) than 1p/9 A (2,070 W). These EV values are synthetic replay inputs,
not recovered measurements. At actual EV=2,300 W, the same imports would instead
select 9 A; the grid values alone do not reconstruct the exact lower selection.

This explains repeated up/down decisions without proving the physical cause of
the import. Battery/inverter lag, a real available-discharge limit, changing
household demand and asynchronous source delivery remain plausible. The
approximately 4,800 W practical maximum establishes neither actual discharge
nor currently available EV power; it is not an inverter measurement or controller
limit. No energy-balance reconstruction from that number is justified.

## External comparison: documentation, source and inference

Manufacturer documentation checked on 26 October 2026:

- [openWB 2.2.0 storage control](https://wiki.openwb.de/doku.php?id=openwb:vc:2.2.0:software:einstell-konfig:ladeeinstellungen:speichersteuerung)
  distinguishes passive storage consideration from active storage control and
  describes configured permitted discharge and storage/PV priorities.
- [openWB 2.1.9 surplus charging](https://wiki.openwb.de/doku.php?id=openwb:vc:2.1.9:software:einstell-konfig:ladeeinstellungen:ueberschussladen)
  centers regulation on grid-point exchange. It documents 1 A steps (~230 W
  single-phase), import/export/rounded preferences between steps, and delays for
  start/stop and phase changes. These are not evidence of a particular battery
  response time or of a two-cycle ampere-decrease delay.
- [go-e manufacturer FAQ](https://go-e.com/de-at/support/faqs), sections on
  current adjustment and Controller/DC-coupled storage, documents 1 A selection
  and actual charging power being limited by the vehicle. For avoiding permanent
  DC-storage discharge, it publishes a -200 W grid target with the preference
  for feeding power into the grid (with its HOME V2 exception). This is a
  different policy from WBM FAST's intended battery use. The consulted FAQ does
  not establish a regulation interval or battery-settling duration; neither is
  attributed to go-e here. No go-e implementation source was inspected.

Actual public source inspected separately from those versioned wiki pages:
[openWB/core](https://github.com/openWB/core/tree/809ac7425348dc4a0a2e080e742dfcd2f3cfc992),
revision **809ac7425348dc4a0a2e080e742dfcd2f3cfc992** (shallow checkout).
This revision is not asserted to match the two documented release versions.

- `packages/control/counter.py`: `calc_surplus()`, `calc_raw_surplus()`,
  `get_control_range_state()` and `_control_range_offset()` combine grid-point
  exchange, storage allowance and configurable control range.
- `packages/control/bat_all.py`: `get_charging_power_left_diff()` accounts for
  measured signed storage power, SoC/priority, allowed discharge and inverter
  output bounds. Its storage-priority path deliberately leaves an export margin
  so an indirectly controlled battery can increase charging.
- `packages/control/general.py`: configured control range and default
  `control_interval=10`.
- `packages/main.py`: `handler10Sec()` obtains updated module data before
  setup/calculation and uses the configured control interval.
- `packages/control/algorithm/integration_test/pv_charging_test.py`: inspected
  fixture/setup structure with nominal 230 V multi-point phase/current cases;
  external tests were not executed or treated as WBM acceptance.

Engineering inference: discrete steps and interacting grid/battery responses can
produce repeated direction changes. Modest persistence before reducing may help
if the excess import is transient. Manufacturer behavior supports examining
response timing and preferences, not importing their policy or proving WBM's
physical root cause. No allowed-discharge model, grid-target preference or active
battery commands are copied into WBM.

Historical WBM comparison: `v0.3.2-beta.18`, commit
`87963014c9af73416c438807f523a3da2a98bd22` (annotated tag object
`4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`), inspected
`custom_components/wallbox_manager/pv_regulators.py` and
`pv_input_diagnostics.py`. Proven useful concepts are actual versus offered
power and diagnostics tapping consumed inputs without polling or running a
second regulator. Its discharge ceiling, source-generation/freshness gates,
response timers and detailed Level-3 taps are historical; no legacy runtime,
cap or timing state is restored.

## Minimal FAST proposal and next hardware evidence

A **two-cycle ordinary downward delay is a reasonable first controlled live
experiment**, after the diagnostics are separately released/installed and a
baseline log has been captured. Use the existing setting at 2 while observing a
PV_MAXIMUM session that remains in FAST. This adds two eligible cycles from
first detection, nominally 10 s at a 5 s cadence; pending execution can lengthen
wall-clock time. Sustained import still reduces on the third detection; a short
peak that clears earlier avoids reduction. SoC, intent and economic STOP keep
priority. A synthetic regression covers both clearing and sustained peaks.

The existing parameter is shared with BALANCE. This operational experiment is
FAST-focused, not a newly FAST-only configuration feature. Do not silently
change its default or shared semantics. The minimal FAST-only code proposal,
**deferred pending evidence and approval**, is to reuse the existing downward
counter with a two-cycle delay specifically for the FAST `grid_import` reduction
branch, leaving BALANCE's configured delay and every overriding STOP unchanged.
No new timer, filter or controller is needed. Only pursue that narrower policy
if the five-value capture shows import clearing during those cycles and a
benefit over the existing setting; persistent deficits require correction rather
than a larger assumed battery budget. This task implements neither proposal nor
any live setting change.

The next log should compare baseline versus the delay experiment, including
request/confirmation events, event times and all five power fields. It must
resolve whether discharge actually rises after an upward EV step, whether PV or
household demand changes during import, and whether missing/both-positive
storage channels or asynchronous delivery make the apparent balance ambiguous.
These remain held snapshot values, not guaranteed simultaneous physical samples.
Sparse decision logs can expose the changed context but cannot reconstruct every
unlogged transient; use existing source history for finer timing when needed,
without adding WBM polling or periodic Level-2 records.

## Validation and boundaries

New tests cover all three battery PV profiles, target changes/unavailability,
mode/decision and SoC/economic STOP/restart events, charge/discharge and grid
signs, household exclusion/floor, unknown values and unique JSON keys. A
persistence-await mutation test verifies decision-snapshot causality. Mocked
acquisition, OCPP and publication surfaces reject diagnostic side effects;
FAST/BALANCE replay traces compare requested points and publication counts with
logging disabled/enabled. Payloads stay bounded and exclude arbitrary source
attributes/secrets. The existing 300-tick noise/sparse-log runtime test remains
part of the suite. Synthetic delay cases are explicitly separate from hardware
observations.

Final validation:

- Complete Python suite: **800 passed**, 5 upstream deprecation warnings,
  139.14 s (Python 3.14, localhost OCPP simulator).
- Frontend: **78 unit tests + 1 browser layout test passed**; card JavaScript
  syntax check passed. Browser layout required execution outside the restricted
  sandbox because Chromium's local socket operation was blocked there.
- `ruff check .`: passed; `ruff format --check .`: 163 files formatted.
- `git diff --check` and staged whitespace check: passed.
- FAST/BALANCE controllers, economics, technical solver and NETZ execution files
  are byte-for-byte unchanged from the validated base.

No merge, tag, release,
installation, deployment, hardware command or live parameter change is part of
this work. Existing local untracked logs/reports are excluded from the commit.
