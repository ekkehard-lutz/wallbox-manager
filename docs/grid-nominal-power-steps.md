# Nominal NETZ power steps

Implementation of approved WBM-GRID-31–40 on
`feature/grid-nominal-power-steps`, based on develop
`7fc8caff3d1a4f3ce6c768be9d6f597df06823ee` (beta.12). This is implementation
work for independent review; it does not authorize installation or hardware tests.
The manifest remains `0.4.0-beta.12`.

## Planning and navigation

`grid_point.Envelope` is the single backend planner. For every advertised mode
(1p, optional 2p, 3p), derive its ceiling from hardware maximum, asymmetry maximum
where applicable, and that mode's WBM allowed-current cap. Zero-cap modes are
absent. Enumerate MinimumCurrent + k × CurrentStep up to the ceiling. Compute
phase count × 230 × current exactly, then round to 100 W using rational decimal
half-up arithmetic. Rounded offers define bounds, approximation and equivalence;
measured voltages never participate. Existing measurement entities, accounting
and Recorder precision are unchanged.

Each enabled mode is a separate contiguous current series. +/- moves to the
adjacent legal current within that same series, even if a different mode offers
a closer or overlapping nominal power. Only + at the actual capped upper
endpoint, or − at the actual lower endpoint, searches other eligible modes.
Choose the nearest strictly greater/lower rounded offer; skip equal or backward
powers. A tie between eligible modes chooses the lower phase count. No eligible
point means that direction is disabled. There are no hardcoded phase boundaries.

For the illustrative 6–20 A 1p / 6–27 A 3p station:

| Navigation | Result |
| --- | --- |
| 1p/6 A, 1.4 kW + | 1p/7 A, 1.6 kW |
| 1p/19 A, 4.4 kW + | 1p/20 A, 4.6 kW |
| 1p/20 A, 4.6 kW + | 3p/7 A, 4.8 kW |
| 3p/7 A, 4.8 kW − | 3p/6 A, 4.1 kW |
| 3p/6 A, 4.1 kW − | 1p/17 A, 3.9 kW |

Direct numeric entry remains separate: preserve confirmed-positive phase
retention and adjacent-envelope midpoint selection. Compare rounded offers
against the one-decimal request: DOWN takes the greatest offer at or below it,
UP the smallest at or above it, NEAREST the closest with ties upward. Internal
zero remains a pause candidate. Thus DOWN 1.6 selects 1p/7 A and DOWN 4.8 can
select 3p/7 A; between offers, 1.5 selects 6 A for DOWN and 7 A for UP/NEAREST.
Ordinary final requests retain hard-ceiling clamping and limitation reporting.
When navigating from a manually approximated value, advance from its resolved
technical point by one legal current step.

## Persisted intent and HA/card contract

An optional `power_phases` field accompanies `power_kw` in the existing draft
and approved records. An exact legal rounded point with that hint takes priority
over ordinary phase retention; invalid/unavailable hints are ignored. This
distinguishes 1p/18 A and 3p/6 A, both displayed as 4.1 kW. Phase-only differences
remain meaningful, and approval atomically copies the hint with power/timing.
Equivalence compares resolved phase/current against the approved desired point,
including during lockout, rather than comparing physical interim current.

Direct Number edits clear the hint, including a same-numeric-value edit, and
resume ordinary approximation. New numeric edits round to one decimal. Valid
saved numeric values (including older higher-precision values) remain stored
unchanged; their display and planning use the rounded one-decimal domain.
Missing, nonpositive, malformed or out-of-range drafts initialize to the lowest
positive technical point once reliable capabilities exist. Initialization is
durable and draft-only: previous approval/timing is retained, manager release
stays OFF, and it does not issue current/phase commands. No repeated write occurs
for a valid initialized draft. With no positive capabilities the editor remains
unavailable with a placeholder. Changed capabilities/caps can make an existing
draft invalid and cause this same draft-only initialization.

The existing Number entity exposes `selected_phase_count`, `draft_phase_hint`,
`power_step_up` and `power_step_down`. Neighbours contain `power_kw` and
`power_phases`; bounds are rounded positive offers. The integration entity service
`wallbox_manager.step_power` targets the NETZ Number and requires `direction`
(-1/+1), `revision` (grid draft revision) and `incarnation` (runtime incarnation).
It serializes through the Settings lock and rejects stale revisions/runtimes.
There is no new OCPP variable, polling loop or control authority.

The card queues relative button/hold/keyboard steps and waits for backend state
acknowledgment before sending the next revision. Unexpected revisions, runtime
replacement or rebinding cancel stale steps. Service completion alone is not
state acknowledgment. Direct edits continue using Number services. Neither
path approves a different operating point before Start/Apply. Multiple cards
share backend truth rather than maintaining independent electrical solvers.

The requested editor always uses one locale decimal. Live actual power always
uses two locale decimals in kW, including zero. Reserve and approximation editors
are removed from the card only; their HA entities, supported services,
persistence, immediate behavior and battery reserve policy remain available.
Entity identities and HACS packaging are unchanged; no release version is bumped.

## Lockouts and operational limits

Both DOWN 3p/6 A → 1p/17 A and UP 1p/20 A → 3p/7 A use temporary 0 A on the
physical phase mode if Stationary reports PhaseSwitchLockout. A positive interim
offer is allowed only if it satisfies the requested rounded-domain direction;
UP cannot retain 4.6 kW as fulfillment of 4.8 kW. Keep the original approved
phase/current as desired. Retry at the existing reported remaining seconds + 5,
or the existing 60-second fallback. A following ReenableLockout is honored in
turn. Existing release, authority, transaction, connection and intent-generation
guards fence all retries and late callbacks. Generic errors do not start a retry
loop. Interim zero is never reported as final target confirmation.

Actual power can be above or below requested nominal power as grid voltage
varies. Rounding is a planning convention, not electrical protection. Stationary
continues to enforce physical limits and owns switching safety. Optional 2p is
tested as a generic advertised capability, not claimed for the owner's hardware.
Software/socket tests do not establish physical switching or live HA acceptance.
No Stationary change or new coordinated installation is required by this feature;
the beta.12 compatible-pair requirement still applies. An older WBM ignores the
new hint and may choose its ordinary phase policy, so rollback should be done
with manager release OFF and reviewed drafts before any later Start.

## Source comparison and regression evidence

Examined `v0.3.2-beta.18`, commit
`87963014c9af73416c438807f523a3da2a98bd22`, particularly
`custom_components/wallbox_manager/solver/operating_point.py`, for its proven
current lattice, phase retention and deterministic approximation. Its measured
voltage optimizer/runtime was not restored. Actual openWB/core source at
`849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
`packages/control/chargepoint/chargepoint.py` (`_is_manual_lock_inactive`,
`is_charging_possible`, `_is_phase_switch_required`), distinguishes charging
policy eligibility from physical plug observation and desired phases from actual
contactor phases. Those safety/authority boundaries remain intact. This feature
does not import an openWB loop or change physical chargepoint control.

`tests/test_grid_nominal_steps.py` covers rounded offers, direct approximation,
every legal adjacent step, capped series endpoints, excluded modes, overlapping
ranges, optional 2p, initialization/persistence, valid saved drafts, measured
voltage independence, entity services and phase hints. Simulated OCPP covers
both specified zero-interim transitions, phase/re-enable deadlines, no draft
dispatch and no duplicate final retry. Existing approval, release-OFF recovery,
Local authority, scheduling, battery, late-callback and Recorder benchmarks remain
part of the complete suite. Card tests cover relative queues, stale writes,
hold/keyboard, shared state, locale precision and removed card-only controls.

Validation on 24 October 2026:

| Check | Result |
| --- | --- |
| Complete Python pytest | 450 passed; five upstream HA/backoff deprecation warnings |
| New nominal-step regression matrix | 60 cases included in the full Python suite |
| Complete card suite | 38 passed |
| Combined actual Stationary Domain/DeviceModel with simulated OCPP | Passed in the full suite; companion develop `0f97ebf6f4d83e0b56f33d3f4bb68f9642c92313`, mocked hardware |
| Full Ruff lint / format check | Passed; 128 Python files already formatted |
| JavaScript syntax | Passed |
| English/German translation key parity | Passed against strings.json |
| Manifest, HACS layout and service metadata | Passed; manifest remains `0.4.0-beta.12` |
| Git whitespace | Passed |

The original untracked investigation files were excluded. WBM develop remains
`7fc8caff3d1a4f3ce6c768be9d6f597df06823ee` and main remains
`09c6148da7a2e033e36ca2280cebe300cb217542`. Stationary was not modified. No tag,
release, installed component, service restart or hardware command was performed.
