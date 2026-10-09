# PV Optimum

> **beta.9 update:** the historical FAST ramp, unlimited minimum hold and
> gap-reset behavior described below are superseded by the
> [beta.9 budget, measurement and grid policy](beta9-implementation-report.md).
> SoC and daily-planner architecture remain as documented here.

PV Optimum uses home-battery energy for EV charging while planning to recover its
own upper target SoC when forecast surplus for the remaining local day is exhausted. It does not control the battery's
reserve or maintain a nighttime SoC. PV Surplus's independent target remains
unchanged. PV Maximum is not implemented.

## Configuration

The four per-wallbox profile parameters are:

| Parameter | Unit | Initial value |
| --- | --- | --- |
| `optimum_lower_soc` | % | 20 |
| `optimum_upper_soc` | % | 80 |
| `optimum_max_discharge_w` | W | 0 (battery support disabled until configured) |
| `estimated_daily_house_consumption_kwh` | kWh per 24 h, excluding EVs | 0 |

Set the household estimate and discharge limit to appropriate values before using
battery support. Zero household consumption means an explicitly zero estimate,
not an inferred measurement. Lower/upper limits must satisfy R + H <= lower <= upper
<= 100 - H; see [shared SoC policy](pv-soc-policy.md). Discharge limit accepts 0–100000 W; household estimate accepts 0–1000 kWh.
The card exposes these four controls and the current calculated target.

Existing mappings are reused: `leistung_pv`, `leistung_verbraucher` (total
consumers **including** the selected wallbox) and `soc_speicher_aktuell`.
Configure `min_soc_speicher` for the live reserve R, plus these additional references in General parameters:

| Mapping | Required semantics |
| --- | --- |
| `storage_discharge_power` | Actual storage discharge, nonnegative W/kW/MW; zero when charging |
| `storage_capacity` | Nominal storage capacity, Wh/kWh/MWh |
| `remaining_pv_energy` | Forecast remaining PV production **today**, Wh/kWh/MWh |
| `grid_import_power` | Nonnegative grid import, W/kW/MW |
| `grid_export_power` | Nonnegative grid export, W/kW/MW |

Capacity comes from Fronius **Nennkapazität des Speichers**, translation key
`model_120_whrtg`, SunSpec WHRtg (e.g. 11059 Wh). It is an entity measurement, not
a profile setting. MaxDisChaRte is never used. A 3500 W configured limit remains
3500 W regardless of a battery's higher physical capability.

PV Optimum becomes selectable when its execution/planning references are mapped;
its policy additionally requires a valid live reserve. Temporary
unavailability does not remove it. Live inputs retain the existing 90-second
report-age limit. Only `remaining_pv_energy` uses the generic slow freshness class
(`SLOW_FRESHNESS = 900` seconds): an age of exactly 900 seconds is accepted; older
forecasts are stale. Storage capacity and every other input retain their existing
freshness limits. Numeric, unit, future-timestamp and explicit-expiry validation
remain unchanged. Diagnostics and regulation use the same freshness class. No
forecast integration is refreshed or polled: only existing HA states are consumed.
Old readings are not silently treated as constants or zeros.
Missing/invalid inputs suspend new decisions and retain confirmed hardware state,
as in the existing PV runtime. No positive command is authorized by stale data.

## Day detection and forecast

The daily planner is independent of SoC mode selection and has three phases:

- `BEFORE_SURPLUS`: target is the configured upper boundary.
- `DYNAMIC`: starts on the first fresh, strict `PV > total consumers - selected EV`
  comparison. Equality does not start it. Clouds and extended instantaneous deficits
  never clear this latch.
- `FINISHED`: entered as soon as remaining PV energy today is less than or equal to
  estimated remaining household energy today. Target becomes upper immediately and
  stays there even if later forecasts improve.

Per-connector date/phase records are saved in the existing PV-day Store using a
version-2 payload. Legacy `active`/`ended` records are not trusted as proof of the
new conditions. Local midnight resets the phase. A new valid surplus can then
start the new day. Reload restores the phase, not cached measurement evidence.

The trigger shares selected-session sensor selection and the existing `pv_balance`
subtraction with regulation. Missing selected power is not zero: fallback requires
live confirmed permission OFF or fresh authoritative physical idle evidence.
Ambiguous, stale, negative or inconsistent household measurements do not start
planning. Other EV loads remain part of household consumption under the selected-EV
scope; this is not an all-EV subtraction.

Both forecast terms cover the remainder of the local calendar day:

```
remaining_seconds = UTC(next_local_midnight) - UTC(now)
remaining_house_Wh = daily_house_kWh * 1000 * remaining_seconds / 86400
remaining_surplus_Wh = remaining_PV_today_Wh - remaining_house_Wh
recoverable_soc = remaining_surplus_Wh / nominal_capacity_Wh * 100
target_soc = clamp(upper_soc - recoverable_soc, lower_soc, upper_soc)
```

The household setting represents a 24-hour average load. Actual elapsed time to
local midnight accounts for 23/25-hour DST days. No sunset entity, sunset-minus-one-
hour truncation or interval Solcast forecast is required. A nonpositive remaining
surplus ends DYNAMIC before the ordinary cache deadline.

Fixed upper targets in BEFORE_SURPLUS and FINISHED do not need a fresh forecast
for display. Charging commands still require all existing fresh execution inputs.
The observer cannot dispatch commands or change inverter settings.

## Three independent time scales

| Purpose | Timing | Implementation |
| --- | --- | --- |
| Fast power observation/regulation | 1 second | HA observation callback and existing PV execution loop in FAST_DISCHARGE |
| Transient import grace | 3 seconds | Monotonic first-import deadline owned by the shared FastDischargeRegulator |
| Target-SoC planning | 300 seconds | Per-connector in-memory plan with a monotonic expiry |

Positive-surplus target calculations are cached for 300 seconds. Midnight,
first surplus, forecast exhaustion, explicit Enable, genuine vehicle connection
and reload bypass stale planning state. Exhaustion is checked on observations,
not deferred until the next five-minute replan. Plans are never restored as proof
of current measurements.

The independent one-second observer validates current execution evidence and
advances SoC mode against the current target. Mode changes wake BALANCE promptly.

The execution loop uses a one-second start-to-start deadline in FAST_DISCHARGE,
subtracting time already spent evaluating/executing. It does not add the normal
one-second positive-command debounce on top of this cadence. OCPP commands remain
serialized: a slow command/phase transition may defer dispatch of the next point,
while the independent observer continues collecting current flow/grace evidence.
No command bypasses live authority, permission, phase lockout or safety fences.

### Meaning of `regulation_interval`

The setting is retained with its range, persistence and default of 5 seconds.
It still controls:

- PV Surplus regulation cadence, unchanged;
- Optimum PV_BALANCE regulation cadence (earlier freshness/delay deadlines and
  SoC-mode wakeups can shorten a wait);
- the existing recovery/runtime/measurement retry checks that used this setting;
- Optimum's upward smoothing time: 25% of the remaining increase per configured
  interval, using elapsed time. Sampling more often does not accelerate the ramp.

It does **not** control the fixed one-second fast observation cadence, three-second
grace deadline or five-minute target planning period. Phase-retry timing and
PV-start/PV-stop delay settings retain their existing independent roles.

## Policy and shared power regulation

`pv_optimum.py` retains the daily planner. All three PV profiles use the
[common SoC state machine](pv-soc-policy.md) in `pv_soc.py`. STOP, BALANCE and FAST
use the same L/M/U boundaries on activation, reconnect and continuation. There is
no special initialization above T. At/above U all profiles enter FAST; at/below L
all stop immediately. Temporary input gaps block new decisions.

`pv_regulators.py` owns reusable power-only primitives:

- `pv_balance`: existing signed PV minus total consumers plus selected actual
  charging power. All three PV profiles call the same implementation and resolve storage-aware
  power requests with DOWN approximation.
- `fast_discharge`: actual wallbox power plus a controlled fraction of net export
  and unused discharge headroom. Meaningful grid import suppresses all increases;
  its non-graced portion is subtracted immediately. Discharge above the configured
  user limit is always subtracted immediately. Grid fluctuations within +/-100 W
  are ignored;
  increases close 25% of the gap to available power per regulation interval.
  A shared time-based smoothing state lets requests grow beyond the minimum
  operating point even while measured wallbox power is still zero. Repeated
  dispatch-fence evaluations do not accelerate the ramp; actual measurements
  bound its current ceiling. Genuine/non-coverable deficits and excess battery
  discharge bypass upward smoothing.

### Transient grid-import algorithm

The shared regulator uses the existing 100 W grid deadband. On net import above
100 W, it records the first observation's monotonic time; later evaluations cannot
extend that deadline. No upward increase is allowed while this import persists.

```
headroom = max(0, configured_limit - measured_discharge)
excess_discharge = max(0, measured_discharge - configured_limit)
allowance = 0
if headroom > 100 W and elapsed_import_time < 3 seconds:
    allowance = min(net_import, headroom)
requested_wallbox = max(0, measured_wallbox - (net_import - allowance)
                          - excess_discharge)
```

Headroom of at most 100 W is effectively exhausted: it gets no grace. At exactly
three seconds, the allowance becomes zero and the remaining measured deficit is
subtracted in full. If discharge exceeds the configured limit, headroom is zero
and both import and excess discharge are corrected immediately. If headroom is
lost during grace, its unavailable portion loses grace immediately too.

At 5000 W wallbox / 2000 W battery / 4800 W limit / 2500 W import, the request stays
5000 W for up to three seconds, giving the external inverter time to take the load.
If import persists, it becomes 2500 W. At 4000 W battery discharge instead, only
800 W can get grace: the immediate request is 3300 W. Once that reduction removes
1700 W import, any remaining 800 W must disappear by the original deadline or be
corrected too. At the 4800 W limit, the immediate request is 2500 W.

When net import returns to the deadband, the grace state clears and that recovery
evaluation holds actual wallbox power rather than causing an unnecessary reduction
or upward jump. Subsequent valid evaluations may resume the normal slow increase.
Missing/stale evidence resets the regulator/grace state and suspends new decisions;
no unknown interval proves that the inverter has responded. Freshness gaps keep
the existing confirmed-hardware-state behavior.

The fast primitive has no SoC or PV-day knowledge and accepts explicit tuning.
It can be reused by a future independently targeted PV Maximum profile.

### Minimum charging and deliberate pauses

FAST_DISCHARGE intentionally stays at the lowest currently reachable positive
charging point when an ordinary power budget falls below minimum. This applies
at startup and while charging, including measurement settling and large household
load steps. It continues for as long as FAST_DISCHARGE remains active; persistent
low budgets do not start a separate fast-mode stop timer. The raw regulator,
three-second battery grace and prompt correction of uncovered import are unchanged.
Holding minimum can leave **residual grid import**. That is an explicit policy
tradeoff, not a claim that the battery can absorb the entire household load.

The minimum comes from verified electrical limits and measured voltages. It is
not always 1p/6 A: when currently constrained to three phases it may be 3p/6 A,
or a higher current if known vehicle/installation limits require it. A lower phase
mode is considered only with applicable transition evidence. Specific station
phase-lockout rejection restricts executable Optimum selection to the confirmed
mode while fresh energy-desired selection still considers verified other modes.
The existing bounded retry may probe a transition again; elapsed retry time does
not prove the station guard has expired. Accepted transitions or changed physical
feedback clear the restriction. Unknown evidence suspends decisions rather than
inventing a safe positive point.

Whenever permitted by the common SoC policy, PV_BALANCE uses the same balance
regulator as PV Surplus. At/below L, protective STOP takes precedence.
For an established charge, insufficient power first holds the reachable positive
minimum, rather than retaining a previously high battery-supported offer. A pause
requires continuous valid insufficient-power evidence for the configured
`pv_stop_delay` (default **90 seconds**), with a minimum of one
`regulation_interval` (default **5 seconds**) even when the stop delay is zero.
Recovery, input gaps and SoC-mode changes reset that evidence. An already-paused
or initially disabled station need not start charging merely to debounce a pause.
No new delay option is added. PV Surplus retains its existing electrical
insufficiency delay; low-SoC STOP bypasses that delay in every profile.

A deliberate pause sends the existing OCPP zero-current profile and leaves
ChargingEnabled unchanged. It can trigger the station's configured restart
lockout (600 seconds on the tested wallbox). Positive retries retain the existing
approximately 60-second backoff. Generic BUSY never becomes phase-lockout evidence.

The policy raises an insufficient FAST budget to the common positive floor; it
does not change DOWN rounding globally. Safety/control stops and hard electrical
limits still take precedence. Missing inputs mean no decision, including during
Optimum enable preparation: no unconditional zero profile is inserted before a
valid positive start. FAST starts do not insert a start-delay OFF step. Balance
start delay and command debounce retain their roles when not already charging.

Diagnostics distinguish `optimum_minimum_hold`, `optimum_pause_pending`,
`optimum_deliberate_pause` and `optimum_no_positive_point`. They include the raw
regulator target, minimum reachable power and observed net grid import, so a
minimum hold is distinguishable from ordinary target realization. They also expose
`desired` and `executable` operating points, `phase_transition_blocked`, and a
minimum-hold flag that remains visible alongside `WAIT_PHASE_LOCKOUT`. A temporary
phase restriction can therefore show desired 1p operation alongside an executable
3p positive hold. Both derive from the same fresh regulator sample; retries never
replay an old desired point. Recovery to a desired 3p point supersedes an earlier
1p preference, including revalidation before a queued phase-changing wire write.

Shared execution remains in the existing PV mixin and ControlRuntime. Diagnostics
include the new external measurements, calculated target, mode and PV-day state.
Changing a profile-specific setting does not invalidate another profile's control.

## Verification

`tests/test_pv_regulators.py` covers the shared arithmetic, user limit, existing
battery contribution, asymmetric reaction, grid deadband and invalid values.
`tests/test_pv_optimum.py` covers daily surplus/reserve latches, gaps, midnight, reload,
energy conversion, target clamps, remaining-day budgeting, mode hysteresis, independent
settings, EV absence/re-entry, stale/missing inputs, solver constraints, permission
and authority. `tests/test_pv_optimum_timing.py` covers the independent clocks,
immediate replanning, cached-target freshness and SoC-mode wakeups. The shared
regulator tests include full/partial/no headroom, grace recovery/expiry, exhausted
limits and unchanged upward smoothing under one-second observation.
`tests/test_pv_optimum_hold.py` exercises the real policy/regulator/solver/OCPP path:
startup lag, phase restrictions and retry probes, current grids and limits,
FAST hold, BALANCE persistence, safety fences and a simulated 600-second restart
lockout. Frontend tests cover independent controls and decimal household
estimates. The complete existing PV/authority/OCPP regression suite remains in use.

No integration version, release, tag or merge is part of this change.

## Historical beta.4 timing correction report

The day detection and sunset planning described by that earlier correction are
superseded by the remaining-day planner above.

Feature branch: `codex/pv-optimum`, continuing the reviewed implementation
`2873dfab82f0fda1a5dd7e6b02baeca7083e5483`.

Changed files for this correction:

- `custom_components/wallbox_manager/pv_regulators.py`: reusable transient deadline
  and partial-headroom allowance, preserving asymmetric smoothing.
- `custom_components/wallbox_manager/pv_optimum.py`: five-minute plan cache,
  responsive SoC/power observation and mode-change wakeup.
- `custom_components/wallbox_manager/profiles.py`: observer cadence, initial/reload
  and activation planning state.
- `custom_components/wallbox_manager/pv_surplus.py`: fast execution cadence,
  immediate day-transition planning and input-gap regulator reset.
- `tests/test_pv_regulators.py`: transient-load and smoothing regressions.
- `tests/test_pv_optimum.py`: sunset test advances the planning deadline.
- `tests/test_pv_optimum_timing.py`: independent clocks and integration tests.
- `docs/pv-optimum-design.md`: timing, algorithm and setting compatibility.

Physical hardware validation should measure Fronius/BYD discharge response and HA
publication latency during appliance load steps. Confirm that the three-second
grace covers typical inverter response, that partial headroom corrects the
uncoverable deficit, and that persistent import reduces power without oscillation.
Observe OCPP acknowledgement/phase-switch latency: serialized execution can delay
actual application even though power observation continues every second. Sensor
publication delays within the existing 90-second freshness window are not proof
of fresh physical measurements. No hardware behavior is claimed from simulated
OCPP tests alone.

Correction validation: **1388 Python tests passed** (five pre-existing dependency
deprecation warnings), **108 frontend tests passed**, Ruff lint and formatting
passed (146 Python files), JavaScript syntax passed, and `git diff --check` passed.
No merge, tag, release or integration-version change is included.

The focused regressions in `tests/test_pv_optimum_desired.py` cover distinct desired
and executable points, bounded automatic retry with a fresh current, recovery to
3p across retry deadlines, and a transport-lock race that rejects an obsolete 1p
phase while retaining safe current coalescing when the desired phase is unchanged.
