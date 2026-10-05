# PV Optimum

PV Optimum uses home-battery energy for EV charging while planning to recover its
own upper target SoC approximately at sunset. It does not control the battery's
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
not an inferred measurement. Lower/upper limits must satisfy 0 <= lower <= upper
<= 100. Discharge limit accepts 0–100000 W; household estimate accepts 0–1000 kWh.
The card exposes these four controls and the current calculated target.

Existing mappings are reused: `leistung_pv`, `leistung_verbraucher` (total
consumers **including** the selected wallbox) and `soc_speicher_aktuell`.
Configure only these additional references in General parameters:

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

PV Optimum becomes selectable when all eight references are mapped. Temporary
unavailability does not remove it. The existing 90-second report-age, numeric,
unit and explicit-expiry checks apply, also to energy/capacity. Sources must report
within that interval; old readings are not silently treated as constants or zeros.
Missing/invalid inputs suspend new decisions and retain confirmed hardware state,
as in the existing PV runtime. No positive command is authorized by stale data.

## Day detection and forecast

PV power at least 100 W continuously for 300 seconds starts the PV day. Below
100 W continuously for 900 seconds ends it. Unknown or expired PV evidence breaks
the continuous interval. Short clouds do not end the day. The ended state is
latched through that local calendar day and persisted across reloads. Local
midnight resets the state. Debounce evidence is not restored after restart.

The existing HA interval callback now observes current SoC, day state and fresh
power inputs every second, even without EV connection, charging permission or
authority. While in FAST_DISCHARGE it also evaluates the shared fast regulator,
including while a serialized OCPP command is pending. PV state-change events
observe threshold crossings too. The observer cannot send charging or battery
commands; only the existing authorized PV execution loop can do that.

During the active PV day, `sun.sun.next_setting` provides the local sunset planning
horizon. After HA advances next_setting to tomorrow with the sun below the horizon,
remaining time is zero; tomorrow's household consumption is never budgeted.
Unavailable, stale or inconsistent sun information suspends dynamic decisions.
There is no user-configured sun mapping.

```
remaining_house_Wh = daily_house_kWh * 1000 * max(0, seconds_until_sunset) / 86400
recoverable_soc = (remaining_PV_Wh - remaining_house_Wh) / capacity_Wh * 100
target_soc = clamp(upper_soc - recoverable_soc, lower_soc, upper_soc)
```

The household forecast is a separate pure function, replaceable without changing
the state machine or power regulators. Before production starts and after actual
production ends, target_soc is upper_soc. Household discharge below that upper
value overnight is allowed; it is only an EV policy target.

## Three independent time scales

| Purpose | Timing | Implementation |
| --- | --- | --- |
| Fast power observation/regulation | 1 second | HA observation callback and existing PV execution loop in FAST_DISCHARGE |
| Transient import grace | 3 seconds | Monotonic first-import deadline owned by the shared FastDischargeRegulator |
| Target-SoC planning | 300 seconds | Per-connector in-memory plan with a monotonic expiry |

Target calculation uses the unchanged formula above. A plan is calculated
immediately on selection/activation and startup/reload, on BEFORE -> ACTIVE and
ACTIVE -> ENDED transitions, at local midnight, and when its target parameters
change. If inputs are unavailable, calculation waits for valid evidence rather
than manufacturing a target. Normal forecast changes are consumed at the next
five-minute planning deadline. Plans are not restored as measurement evidence.

Freshness is still validated on every observation and pre-dispatch decision,
including forecast/capacity/sun information even when the target is cached.
The existing 90-second freshness window does **not** require one physical sensor
publication per second. Current SoC is compared against the cached target every
second with the existing hysteresis. Mode transitions wake a sleeping PV_BALANCE
loop; they do not wait for its regulation interval or the planning deadline.

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

`pv_optimum.py` owns only Optimum's day state, forecast, independent target and
mode selection. FAST_DISCHARGE enters strictly above target plus the existing
central SoC hysteresis (default 5 percentage points), and exits at or below target.
No new hysteresis setting is added. Recharging above target while the EV is absent
can re-enter FAST_DISCHARGE; there is no target-reached-for-today latch.

`pv_regulators.py` owns reusable power-only primitives:

- `pv_balance`: existing signed PV minus total consumers plus selected actual
  charging power. PV Surplus and PV Optimum call the same implementation. Surplus
  retains its existing SoC eligibility, upper/lower approximation and delays.
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

Optimum always passes DOWN approximation to the existing operating-point solver.
PV_BALANCE does not budget deliberate battery discharge. Optimum does not hold a
previous battery-supported operating point through the Surplus stop delay; a
valid zero/below-minimum request pauses immediately. The existing start delay,
PV_BALANCE command debounce, phase/current constraints, phase lockouts, retry machinery,
input-gap semantics and authority fences still apply. No independent current or
phase selection exists. Zero power uses the existing OCPP zero-current profile
and leaves ChargingEnabled unchanged.

Shared execution remains in the existing PV mixin and ControlRuntime. Diagnostics
include the new external measurements, calculated target, mode and PV-day state.
Changing a profile-specific setting does not invalidate another profile's control.

## Verification

`tests/test_pv_regulators.py` covers the shared arithmetic, user limit, existing
battery contribution, asymmetric reaction, grid deadband and invalid values.
`tests/test_pv_optimum.py` covers day thresholds/debounce, gaps, midnight, reload,
energy conversion, target clamps, sunset budgeting, mode hysteresis, independent
settings, EV absence/re-entry, stale/missing inputs, solver constraints, permission
and authority. `tests/test_pv_optimum_timing.py` covers the independent clocks,
immediate replanning, cached-target freshness and SoC-mode wakeups. The shared
regulator tests include full/partial/no headroom, grace recovery/expiry, exhausted
limits and unchanged upward smoothing under one-second observation. Frontend tests cover independent controls and decimal household
estimates. The complete existing PV/authority/OCPP regression suite remains in use.

No integration version, release, tag or merge is part of this change.

## Timing correction report

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
