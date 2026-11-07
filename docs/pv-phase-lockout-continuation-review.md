# PV phase-switch lockout continuation and diagnostics review

## Incident and evidence

The operator supplied the following summary of the 7 November 2026
Wallbox01 PV_OPTIMUM test from `pv1(2).log`. Times are local to Europe/Vienna.
The raw log was not available in this workspace for independent inspection.

| Time | Recorded or reported observation |
| --- | --- |
| Startup | Physical station at 3p/0 A before charging |
| 11:39:59 | First phase switch confirmed at 1p/6 A |
| 11:44:11 | Single-phase ramp reached 1p/20 A |
| 11:44:26 | Regulator proposed 1p/20 A → 3p/7 A, about 33 seconds before the expected end of Stationary's 300-second phase lockout |
| 11:44:31 | Station still single-phase and measured EV power fell to about 1.79 kW |
| 11:44:36 | Measured EV power fell to about 1.31 kW, consistent with a 1p/6 A offer |
| 11:48:36–11:48:39 | A later upward phase proposal was followed by confirmed physical three-phase operation |
| Later FAST operation | Battery-limited exploration alternated between 3p/11 A and 3p/12 A. The operator accepts this as normal. |

The supplied summary has no original OCPP rejection payload. A phase-lockout
rejection is an inference from the timing and physical phase, together with
the beta.21 source behavior. The power drop alone does not prove the actual
offer or a particular rejection reason.

Beta.21's PV branch in `Grid.reconcile()` chose the first legal point in the
previous phase series after a recognized phase-lockout response. For this
station the 1p floor was 6 A. The shared retry deadline also blocked ordinary
same-phase proposals until the retry wait ended.

| Condition | Beta.21 expected requests | Feature expected requests |
| --- | --- | --- |
| 1p/20 A, atomic rejection of 3p/7 A | Attempt 3p/7 A, then send 1p/6 A | Attempt 3p/7 A and retain valid 1p/20 A without a second profile |
| 3p/7 A, atomic rejection of a lower 1p point | Attempt 1p, then send 3p/6 A | Attempt 1p and retain valid 3p/7 A. A later qualifying downward tick can choose 3p/6 A. |
| Wait with fresh same-phase demand | Global retry can block the change | Ordinary legal current changes remain possible |
| Deadline elapsed | Retry depends on regulator context | The next eligible tick calculates a new decision from the latest available inputs and current authorization |

## Execution and safety

Stationary checks the re-enable and phase-switch lockouts before the
relay/current operation under its command lock. For the specific atomic
`Rejected / PhaseSwitchLockout` result, the rejected target never becomes
accepted or pending on the station. The existing Grid uncertainty unwind
therefore leaves independently confirmed evidence usable. WBM checks that
the retained point is positive, current-generation and transaction-scoped,
consistent with reported physical phases, and within the current legal
envelope. In this case the safest continuation of 1p/20 A is to issue no
new positive command. A timeout, ambiguous rejection or partially executed
operation cannot establish those facts. An accepted profile still requires
existing readback and physical phase agreement to be confirmed.

A phase-only deadline is separate from the existing command retry timer.
Valid Stationary remaining time up to its supported 86,400 seconds uses the
existing five-second margin. Missing, malformed or out-of-range time uses a
60-second retry interval. There is no timer callback or saved positive
proposal for this restriction. The existing PV tick decides again after
expiry. The wire guard also blocks a different-phase positive profile while
the restriction is active.

The shared controller continues to calculate its normal decisions. A
justified lower cross-phase proposal can be projected onto the legal
physical phase series using the existing solver, down to that series'
positive floor. This projection is separate from the rejected command.
An invalid cap can force a legal reduction, or zero if continued operation
is unavailable. If a positive proposal cannot be supported by a confirmed
physical phase under the restriction, the existing pause path is available.
Ambiguous outstanding application still uses existing readback before any
further positive execution. Mandatory SoC/economic STOP and operator
revocation use their existing urgent path. Authority, release, permission,
connection, transaction and profile changes retire the scoped restriction.
Same-phase commands and cap changes in the same scope do not retire it.

WBM keeps its original policy, transaction, epoch and transport fencing.
The Stationary remains responsible for relay, CP and physical protection.
The phase lockout itself supplies no new electrical permission or hardware
confirmation.

## Diagnostics

The existing Level 2 `pv_decision` record now has
`previous_decision_point`, `requested_point`, `confirmed_point` and
`physical_phases`. Legacy `old_point` retains its prior regulator-decision
meaning and emission condition. The compact `pv_sample` schema is unchanged.

Sparse `WBMGR subsystem=pv` `pv_execution` records describe actual wire
submissions and outcomes. Point arrays are `[phases, amperes]`. The execution
schema includes station/EVSE/connector, profile, mode, authority, transaction
availability, observation generation, local command epoch, actual outcome and
reason, proposal, command, ACK, confirmed point and reported phase. An ACK
has null confirmation while its application is unresolved. A later
confirmation uses only an ACK from the same valid command context. An
invalidated response cannot create a continuation record. Phase-pending
records also include the justified continuation, retry interval, deadline and
whether the interval came from the station or the bounded fallback.

Illustrative excerpts from a simulated trace, with identity fields omitted:

```json
{"event":"pv_decision","old_point":[1,20],"previous_decision_point":[1,20],"requested_point":[3,7],"confirmed_point":[1,20],"physical_phases":1}
{"event":"pv_execution","command_status":"submitted","commanded_point":[3,7],"confirmed_point":[1,20],"physical_phases":1}
{"event":"pv_execution","command_status":"rejected","command_reason":"PhaseSwitchLockout","commanded_point":[3,7],"acknowledged_point":null,"confirmed_point":[1,20]}
{"event":"pv_execution","command_status":"phase_lockout_pending","continuation_point":[1,20],"retry_after_s":38,"retry_at":1305,"retry_source":"station_remaining"}
{"event":"pv_execution","command_status":"accepted","commanded_point":[3,7],"acknowledged_point":[3,7],"confirmed_point":null,"physical_phases":1}
{"event":"pv_execution","command_status":"confirmed","acknowledged_point":[3,7],"confirmed_point":[3,7],"physical_phases":3}
```

Ordinary held ticks retain their compact sample only. The execution trace
adds no control cycles, acquisition, Recorder entities or raw transaction IDs.
Levels 0/1 retain their existing OCPP reporting.

## Normative precedence and regression traceability

| Requirement | Regression evidence |
| --- | --- |
| [PV-LOCK-01–03, 05–06](specifications/pv-operating-point-response.md#approved-pv-phase-lockout-continuation--7-november-2026) | `test_pv_phase_lockout.py` replays 3p/0 A → 1p/6 A through all 1p steps to 20 A, rejects 3p/7 A at 267 seconds, and confirms a fresh 3p attempt after the deadline. Reverse direction, same-phase import correction and upward movement preserve the restriction. |
| [PV-LOCK-04, 07–08](specifications/pv-operating-point-response.md#approved-pv-phase-lockout-continuation--7-november-2026) | The same suite covers cap/phase invalidation, SoC and budget STOP, operator revocation, permission/authority loss, transaction replacement, generic and ambiguous outcomes, absent evidence, retry parsing and late rejection. Existing Grid and PV lifecycle tests exercise related recovery and command fencing. |
| [PV-DIAG-11–14](specifications/pv-decision-diagnostics.md#approved-pv-operating-point-execution-trace--7-november-2026) | Reconstructed proposal/command/ACK/confirmation sequence, delayed readback, Levels 0–2 and 300 unchanged ticks with no extra lockout event, command or composite-schedule read. `test_pv_power_diagnostics.py` protects the previous decision/sample and logging invariance contracts. |
| PV-STEP-01–06, PV-RESP-01–08 and PV-ECO | Existing pure-controller and runtime regressions cover phase-first navigation, startup and uptake, FAST and BALANCE, battery-free PV_SURPLUS and economic STOP/restart. Added integration coverage repeats the accepted 3p/11 A ↔ 3p/12 A behavior. |

PV-LOCK supersedes the historical minimum-only PV fallback and corresponding
PV-PHASE-02 acceptance language. [WBM-ARCH-01–12](specifications/architecture-responsibility.md)
retain precedence. The draft status of the overall PV V3 document is unchanged.

## Source cross-check and invariance

- WBM `v0.3.2-beta.18` peeled to
  `87963014c9af73416c438807f523a3da2a98bd22`. Inspected
  `docs/phase-lockout-protocol.md` and
  `tests/test_pv_phase_lockout_continuation.py`. The applicable precedent
  is atomic rejection, scoped phase restriction and fresh decisions after
  a wait. Its minimum-current fallback, runtime and historical import
  policy are superseded for this feature.
- Inspected openWB/core `849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
  `packages/control/chargepoint/chargepoint.py`, including `set_phases`,
  `check_min_max_current`, and their use with
  `check_phase_switch_completed` in `update`. openWB separates phase
  constraints, current limits and feedback. Its code is a reference for
  the responsibility boundary. [Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py)
- Inspected Stationary `279287c3c1d8863e803144eccf773ab3f10889a5`,
  `domain/wallbox.py` lockout configuration and apply guard, and
  `ocpp_runtime/station.py` `set_charging_profile`. The station maps the
  pre-operation lockout error to the explicit OCPP reason and optional
  remaining seconds. Other failure paths can have different guarantees.

Production changes are limited to `grid.py` and `pv.py`. The branch starts
at the verified beta.21 develop commit
`824a60004b98cd72eec9bae3020101ef697149d9`. The separate NETZ feature is
outside this ancestry. The NETZ fallback solver, shared controller, technical
point lattice, economics, SoC and planning logic retain their algorithms.
No Stationary or Fronius changes form part of this branch.

## Validation

| Check | Candidate | Published beta.21 baseline |
| --- | --- | --- |
| Complete Python suite | **918 passed** with five dependency warnings in 207.03 s | 895 passed |
| Statement coverage | **93.62%** (3,963/4,233 covered, 270 missing) | 93.51% |
| Frontend suite | **92 passed** | 92 passed |
| Headless Chrome | **1 passed**, 96 layout combinations | 1 passed, 96 combinations |
| Static checks | Ruff lint and format, production/test JavaScript syntax, whitespace, manifest and HACS, and 158 matching translation leaf keys | Passed |
| Specification and review links | 87 local links and anchors across six Markdown files | — |
| Exact source comparison | 43 integration files match the beta.21 Git blobs outside `grid.py` and `pv.py`, including `pv_core.py`, `grid_point.py`, `pv_economics.py`, `pv_plan.py`, `pv_soc.py`, `ocpp.py`, `control.py` and `grid_battery.py` | Reference |

The replay fixture owns tick delivery to avoid counting both a timer-fired
tick and a manual tick at the same simulated instant. The prior-outcome
PV Start fixture drains Stop's legitimate zero readback before seeding the
historical status. Both fixes are limited to tests. The GitHub Actions push
workflow validates the review commit independently.

## Later physical acceptance

The simulated trace demonstrates manager behavior for a known atomic
rejection. It does not prove that the earlier field incident returned that
response or establish real hardware behavior. If the station omits reliable
remaining time, a new regular attempt after the fallback interval may still
receive a lockout. An unavailable or inconsistent readback supplies no
positive continuation claim. In a separately authorized
session, capture Stationary's actual rejection and physical/readback points
for both phase directions. Verify the valid same-phase offer persists,
that real demand changes can still adjust it, and that Stop and permission
or authority loss never permit an obsolete phase request. Compare the Level
2 execution trace to Recorder EV power and separate OCPP response evidence.
