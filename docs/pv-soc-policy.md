# Shared PV SoC policy

PV Surplus, PV Optimum and PV Maximum share `pv_soc.soc_policy`. Profile target
calculation feeds this pure policy; its STOP/PV_BALANCE/FAST_DISCHARGE result
selects the existing power regulators, followed by the operating-point solver
and the existing wallbox controls. No profile-specific Maximum regulator exists.
The FAST_DISCHARGE power algorithm, including its transient import grace and
ramp, is unchanged. Hardware pumping remains a separate follow-up.

## Targets and boundaries

R is the live Home Assistant minimum reserve (`min_soc_speicher`), H is the one
shared `soc_hysterese` parameter (default 2 percentage points), and T is the
effective target. All targets satisfy `R + H <= T <= 100 - H`.

- Surplus: configured `soll_soc_speicher` (default 95, subject to clamping).
- Optimum: the existing daily planner within independently configurable ordered
  lower/upper bounds. For R=5, H=2, lower=30 and upper=80 remain valid.
- Maximum: always `R + H`; no forecast, capacity or household estimate is needed.

Set `L=T-H/2`, `M=T+H/2`, `U=T+H`. The common transitions are:

| SoC | STOP / paused / new | PV_BALANCE | FAST_DISCHARGE |
| --- | --- | --- | --- |
| `SoC <= L` | STOP | STOP | STOP |
| `L < SoC < M` | STOP | PV_BALANCE | PV_BALANCE |
| `M <= SoC < U` | PV_BALANCE only if raw PV > household; otherwise STOP | PV_BALANCE | FAST_DISCHARGE |
| `SoC >= U` | FAST_DISCHARGE | FAST_DISCHARGE | FAST_DISCHARGE |

Household excludes the controlled EV, using the same raw evidence as the daily
planner. Equality PV=household does not permit a start. This is a start gate;
later loss of surplus does not itself change an active BALANCE mode to STOP.
The regulator, minimum charging point and electrical stop delay still determine
whether charging can continue. Low-SoC STOP bypasses that economic delay.
For H=0 the thresholds coincide; the lower protection check takes precedence at
exact equality. No newly connected vehicle bypasses the common start thresholds.
Surplus intentionally permits battery discharge in FAST mode at/above U.

PV operation never writes a temporary storage reserve. Grid reserve restoration
continues through its existing ownership mechanism. Missing or invalid live
inputs block new positive decisions; beta.9 pauses when the hard power budget
cannot be established and preserves an active stop deadline across gaps; a mathematically impossible target range produces
STOP. Zero-current pauses retain charging permission and do not bypass station
re-enable or phase-switch lockouts.

## Configuration and migration

The shared H defaults to 2 only when absent. Explicit legacy H values win over
defaults; the existing central-option/first-sorted-profile migration order remains.
New H edits accept 0..50 and must leave a feasible interval for the current R.
There is no separate buffer setting. Profile number entities advertise live
minimum/maximum values, including ordered Optimum endpoints; the English/German
card exposes the relevant target and discharge-limit settings for each profile.

On load, old targets are individually clamped into the permitted range, first
against R=0 if live reserve has not arrived, then against the actual R. An inverted
Optimum pair is repaired by raising upper to lower after clamping. Original target
values are retained in `legacy_soc_targets`. Explicit older H values that leave
no feasible interval are retained and block charging until corrected. Profiles
and daily planner state are not discarded for out-of-range old targets.

Live R increases clamp targets again; R decreases expand the permitted editing
range but do not restore formerly clamped preferences automatically. Normal
profile persistence saves the projected values. Explicit target edits outside
the live interval or with upper<lower are rejected. Changing H projects existing
targets before validation. The effective planner target is always clamped again.

The beta.6 daily planner remains BEFORE_SURPLUS / DYNAMIC / FINISHED, with its
first strict raw-surplus trigger, remaining-calendar-day household estimate,
300-second target cache, FINISHED latch, local-midnight reset and version-2 day
persistence. Common mode continuity is journaled with ownership and restored only
when the existing recovery path confirms continuing charging in the same
transaction. Otherwise STOP/new rules apply; legacy continuation journals default
to BALANCE rather than granting FAST.

## Diagnostics and validation

`soc_mode` transition events carry mode, previous mode, reason and target profile.
Level 1 remains deduplicated and event-oriented. Level 2 adds T/L/M/U, SoC and
start evidence; level 3 retains cycle traces. Measurements and varying target
values do not enter transition identity. Cycle context includes the current mode
and thresholds even without a transition.

`test_pv_soc.py` covers the pure boundary matrix; `test_pv_common_runtime.py`
checks every profile adapter at each exact/adjacent boundary, both start-evidence
values, migration, dynamic/global bounds, Maximum and diagnostic deduplication.
Existing planner, protocol, phase, permission, freshness and command-fence
regressions continue to exercise the shared runtime.
