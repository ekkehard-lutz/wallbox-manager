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

An existing HA interval callback observes the policy every five seconds, even
without EV connection, charging permission or authority. PV state-change events
also observe threshold crossings. This observer publishes policy only; it cannot
send charging or battery commands.

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
  and unused discharge headroom. Meaningful grid import suppresses all increases
  and is subtracted immediately. Discharge above the configured user limit is
  also subtracted immediately. Grid fluctuations within +/-100 W are ignored;
  increases close 25% of the gap to available power per regulation interval.
  A shared time-based smoothing state lets requests grow beyond the minimum
  operating point even while measured wallbox power is still zero. Repeated
  dispatch-fence evaluations do not accelerate the ramp; actual measurements
  bound its current ceiling. Meaningful import and excess battery discharge
  bypass this smoothing and reduce immediately.

The fast primitive has no SoC or PV-day knowledge and accepts explicit tuning.
It can be reused by a future independently targeted PV Maximum profile.

Optimum always passes DOWN approximation to the existing operating-point solver.
PV_BALANCE does not budget deliberate battery discharge. Optimum does not hold a
previous battery-supported operating point through the Surplus stop delay; a
valid zero/below-minimum request pauses immediately. The existing start delay,
command debounce, phase/current constraints, phase lockouts, retry machinery,
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
and authority. Frontend tests cover independent controls and decimal household
estimates. The complete existing PV/authority/OCPP regression suite remains in use.

No integration version, release, tag or merge is part of this change.

## Implementation report

Feature branch: `codex/pv-optimum`.

Final validation: **1369 Python tests passed** (five dependency deprecation
warnings), **108 frontend tests passed**, Ruff lint and format checks passed,
JavaScript syntax check passed, and `git diff --check` passed. No open design
questions remain after the specified linear household model and day boundaries.
Hardware behavior has been exercised through the repository's simulated OCPP
wallbox tests, not a physical installation.

Changed files:

- `custom_components/wallbox_manager/pv_optimum.py`: independent profile policy,
  household forecast and persistent day observation.
- `custom_components/wallbox_manager/pv_regulators.py`: shared balance and reusable
  smoothed battery-supported regulation.
- `custom_components/wallbox_manager/pv_surplus.py`: shared balance call, energy
  units and shared runtime routing; Surplus policy retained.
- `custom_components/wallbox_manager/profiles.py`: parameters, persistence,
  availability and lifecycle integration.
- `custom_components/wallbox_manager/config_flow.py`: five missing entity mappings.
- `custom_components/wallbox_manager/number.py`: four profile controls.
- `custom_components/wallbox_manager/pv_diagnostics.py`: Optimum measurements and
  diagnostic cycle support.
- `custom_components/wallbox_manager/strings.json`, `translations/en.json`,
  `translations/de.json`: labels and mapping descriptions.
- `custom_components/wallbox_manager/www/wallbox-manager-card.js`: Optimum controls
  and target display.
- `tests/test_pv_optimum.py`, `tests/test_pv_regulators.py`,
  `tests/test_wallbox_card.cjs`: policy, regulator and frontend coverage.
- `README.md`, `docs/pv-surplus-profile.md`, `docs/pv-optimum-design.md`:
  configuration, architecture, behavior and verification documentation.
