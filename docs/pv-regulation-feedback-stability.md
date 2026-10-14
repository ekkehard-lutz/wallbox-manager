# PV feedback stability — beta.14 review candidate

Work branch: `fix/pv-regulation-feedback-stability`. Behavioral baseline:
`v0.3.2-beta.13` (`aaef76d`). The branch also includes the newer, documentation-only
`develop` requirements commits through `e3debc1`. All seven mandatory preparation
documents were read before implementation. The requirements remain unchanged.

## Confirmed causes, recorded before implementation

### FAST: physical response is incorrectly equated with offered power

Affected requirements: **PV-FAST-01, PV-FAST-03, PV-FAST-04**; adjacent invariants:
PV-OCPP-01, PV-ECO-01/-06, PV-PHASE-01/-02.

`FastDischargeRegulator.request()` currently accepts physical response only when
every source has advanced after confirmation and measured EV power is within
`max(100 W, half a current step)` of the offered point. With 2,059 W offered at
9 A, a step is about 228.78 W; the 171 W shortfall at measured 1,888 W fails this
114.39 W test. Complete fresh generations still produce
`unobserved_command_no_increase` after the 20-second settling window, including a
deterministic evaluation at 4,800 seconds with a 4,788 W economic ceiling.
Timeout correctly does not prove response, but the absolute power comparison
rejects a genuine current-limited EV response below the offer. Existing closed-loop
FAST simulations model EV consumption as exactly the offered watts and miss this.

`reused_evidence` has a separate, valid cause: the six-source generation must
advance in every channel before being consumed again. Relaxing that requirement
would reintroduce speculative ramping and is not the fix. OCPP acknowledgement
remains a command boundary, never physical power evidence.

The intended correction is to retain actual EV evidence from before a command
and accept a meaningful fresh physical change toward that command as well as the
existing near-offer proof. An unchanged under-consuming EV must not authorize
successive increases. Response before a delayed ACK must retain its original
pre-command comparison. No new fixed ramp interval is warranted.

### BALANCE: mismatched filtering and missing upward-command feedback

Affected requirements: **PV-BAL-01, PV-BAL-02, PV-BAL-03**; adjacent invariants:
PV-ARCH-01/-02, PV-ECO-01/-06/-08, PV-QA-01.

The battery-equipped measurement path independently time-averages PV and total
consumer power, then adds the raw selected EV sample. After an EV increase, total
load can still represent the earlier charging level. The reported example yields
`1297.82 - 2417.69 = -1119.87 W` fictitious household load and
`2938.67 - 1297.82 + 2417.69 = 4058.54 W` apparent available power. Separate
source acquisition times add another mismatch even with smoothing disabled.

The common BALANCE adapter updates the raw economic ceiling but directly uses
the apparent surplus as its soft target. It does not consume upward evidence or
wait for the preceding current command's physical response. A budget ceiling
alone cannot prevent this feedback oscillation: it bounds offered power but does
not validate apparent additional PV surplus.

The intended correction is to retain the configured PV/total-load smoothing and
bound upward availability with conservative raw power-flow evidence, using the
existing lower EV basis across asynchronous acquisitions. BALANCE will use its
own PV-surplus target with shared source/response observation, rather than FAST's
battery-headroom preference. Real raw PV/household deficits, measured import and
binding economic/electrical limits must remain able to reduce immediately.
Battery-free SURPLUS keeps its measured `EV + export - import` contract.

## Evidence limits and scope

The arithmetic and code paths above are reproduced; the task's historical
hardware counts are supplied observations, not a new replay or hardware result.
Acquisition/report advancement is causal evidence under the existing sensor
contract, not a guarantee of physical simultaneous acquisition. No earlier
minimum-import STOP timer or economic `pv_stop_delay` is reinstated. Shared SoC,
daily forecast planning, measured 50 Wh accounting/restart, exact minimum and
phase-lockout policy, ownership and OCPP fences/retries remain binding.

## Implementation and timing

`pv_regulators.py` now retains the actual EV power and six source boundaries at
the existing dispatch hook. A changed confirmed command adopts that snapshot;
ACK still starts the existing 20-second settling window. Fresh actual movement
greater than the existing 100 W noise deadband in the **command's direction**
can establish response below the offered watts. Direction uses the previous
confirmed offer, rather than comparing an under-consuming EV with offered watts.
The original near-offer proof remains available. Every changed command resets
response proof; neither ACK, timeout nor an unchanged EV proves the next increase.
Partial under-offer movement does not bypass the existing phase-switch settling
window; its original near-offer completion path remains unchanged.

BALANCE uses a separate PV-only request calculation inside the same controller.
Its raw capacity is the lower of the PV/total-load balance and the measured
EV/battery/grid flow, using the existing conservative EV basis. A negative
household estimate cannot add power above raw PV production. The two configured
PV/total-load filters remain intact; actual EV, SoC and electrical inputs remain
raw. Smoothed available power is an **upward preference** capped by raw capacity.
Filter lag alone cannot reduce an offer that raw capacity still supports.

For continuing positive charging, each increase closes half the measured-EV-to-PV
target gap. It requires one newly advanced complete source set, site sources
crossing the EV source boundary, the preceding physical response, no pending
command, net import within the existing deadband and the configured regulation
interval. Initial eligible PV starts retain their existing allocation and common
startup response reserve. This is distinct from FAST's half-economic-headroom
preference; no fixed ramp interval or new configuration setting is introduced.
Restoring an initial allocation after fresh OFF proof does not wait for the
ongoing positive ramp interval; configured `pv_start_delay` retains its own clock.
An ordinary meter-driven task relaunch retains the regulator's response/source
proof, current session identity and start clock. Previously, its internal
invalidation could discard valid OFF proof and make the next read look like a
new session, restarting the configured delay. General lifecycle invalidation,
command generations and hardware fences are unchanged.

Raw PV/household deficits, measured import and the existing economic ceiling can
reduce on the next evaluation even during response settling, pending execution
or a long upward interval. Existing command serialization still controls when a
reduction reaches hardware. Exact-minimum continuation and economic OFF remain
owned by the unchanged technical-minimum/50-Wh policy.

Compared with beta.13, genuine under-offer response can release upward progress
without waiting for offer equality. BALANCE increases now wait for source/response
evidence and can take multiple configured intervals to approach available PV.
This trades some transient surplus utilization for feedback stability. Deficits
use raw evidence, so increasing the smoothing window cannot postpone their
correction. Report/source timestamps still do not guarantee physical simultaneous
acquisition, and there is no new universal hardware settling-time guarantee.

A cold adopted under-offer point without a causal pre-command baseline remains
conservatively inhibited. Existing absolute near-offer evidence can establish
its response; arbitrary stable consumption alone cannot safely distinguish that
case from a car still consuming at an unanswered older setpoint. Hardware review
must verify source metadata and actual responses, including reload/input gaps,
inverter latency, vehicle tapering and phase transitions. No hardware execution
or deployment is claimed.

## Regression evidence

New deterministic regressions cover 1,888 W actual / 2,059 W offered; no physical
response through 4,800 seconds; all six stale-channel cases; reuse and delayed ACK;
true/wrong response direction after a current reduction; and the real shared OCPP
dispatch hook in all three battery PV profiles. BALANCE includes the historical
negative-filtered-household adapter case, raw deficit correction despite a
300-second upward interval/pending command, false filtered deficits, asynchronous
EV/site measurement clocks, delayed response, changing household load,
intermittent PV and an EV drawing 90% of its offer.

An isolated `git archive v0.3.2-beta.13` checkout with the new test files produced
**16 semantic failures and 9 passes** across the FAST, BALANCE adapter,
response-direction and shared-dispatch tests. These failures are power/response
assertions, not missing-new-API failures. The new controller-only BALANCE matrix
uses the candidate's added method; the original adapter case separately proves
the beta.13 defect.
All **47 new regression cases** pass in the final full suite.

Older lifecycle/fence fixtures are updated to provide actual command responses,
fresh no-load evidence after OFF and the existing regulation interval. Their
SoC, ownership, economic accounting, voltage, retry and OCPP-fence assertions
remain binding. Validation results and the final file inventory follow below.

## File-by-file change inventory

| File | Change |
| --- | --- |
| `custom_components/wallbox_manager/pv_regulators.py` | Dispatch-bound, directional under-offer response proof and separate BALANCE request with raw capacity and asymmetric feedback gates. |
| `custom_components/wallbox_manager/pv_optimum.py` | Route common BALANCE through the controller; share electrical step calculation and expose raw capacity/source/response diagnostics. |
| `custom_components/wallbox_manager/pv_economy_control.py` | Invoke the regulator snapshot at the existing dispatch callback; economic accounting and lifecycle logic are unchanged. |
| `custom_components/wallbox_manager/pv_surplus.py` | Retain response evidence/session identity across an internal meter-driven relaunch, preserving the configured startup clock. |
| `custom_components/wallbox_manager/pv_input_diagnostics.py` | Add passive FAST response/baseline/command-change fields without additional sensor reads. |
| `tests/test_pv_fast_feedback.py` | Under-offer response, nonresponse, stale/reused sources, delayed ACK, cold adoption, budget correction and phase settling. |
| `tests/test_pv_balance_feedback.py` | False surplus, asynchronous closed loops, delayed response, smoothing, PV/household changes and prompt corrections. |
| `tests/test_pv_feedback_dispatch.py` | Real shared OCPP dispatch response below the offer for all three battery PV profiles. |
| `tests/test_pv_response_direction.py` | True and wrong-direction physical changes after an under-consuming EV's current reduction; fresh OFF response restores an initial allocation without a second ramp delay. |
| `tests/test_pv_beta12.py` | Keep the intentionally injected historical soft target isolated from a newly proved physical response; coherent restart/no-load evidence. |
| `tests/test_pv_beta2.py` | Fresh no-load startup/response fixtures while preserving configured start-delay and lifecycle assertions. |
| `tests/test_pv_hysteresis_delay.py` | Fresh no-load evidence after protective OFF, retaining the SoC boundary assertions. |
| `tests/test_pv_input_diagnostics.py` | Assert raw conservative BALANCE capacity and retained smoothed-input diagnostics. |
| `tests/test_pv_optimum_hold.py` | Fresh source/physical floor evidence before the existing phase probe. |
| `tests/test_pv_restart_review.py` | Genuine fresh OFF/response evidence and interval for restart and pending-snapshot cases. |
| `tests/test_pv_smoothing.py` | Real responding EV/site cohorts before a pending smoothed command; raw voltage checks retained. |
| `tests/test_pv_snapshot_fences.py` | Genuine commands and responses for snapshot races; live fences and no-wire assertions retained. |
| `tests/test_pv_surplus.py` | Fresh no-load recovery and budget-bounded initial request expectation. |
| `tests/test_pv_transient_gaps.py` | Physical EV response, coherent load and fresh OFF cohorts in gap/recovery fixtures. |
| `docs/pv-regulation-feedback-stability.md` | Root causes, requirements traceability, timing, evidence limits, inventory and validation. |

The pre-existing untracked documentation, validation artifacts and `pv2.log` are
outside this change. Requirements, manifest/version, SoC, daily planner, energy
accounting/storage, solver, authorization and OCPP transport sources are unchanged.

## Validation

All Python checks use the repository's existing `.venv`; HA/OCPP tests run with
local loopback fixture access. They do not connect to a Home Assistant deployment
or charging hardware. Final validation includes the affected startup/voltage/SoC
and lifecycle group (205 passed), snapshot/transient/smoothing group (82 passed),
the feedback/economy/phase safety group (353 passed before the added startup
regression), and the existing OCPP/control regressions. The broader intermediate
PV/OCPP group had 1,220 passes and two startup failures; those failures identified
the internal meter-wake evidence/session reset above and pass after its fix.

Commands:

```bash
.venv/bin/pytest -q --tb=short --show-capture=no
node tests/test_wallbox_card.cjs
.venv/bin/ruff check . --no-cache
.venv/bin/ruff format --check . --no-cache
git diff --check
```

Full Python suite: **2,467 passed in 212.91 seconds**, zero failures, five upstream
Home Assistant/backoff deprecation warnings. Frontend: **145 passed**. Ruff lint,
all **200** Python formatting checks and whitespace checks pass.
The final read-only source review found no remaining actionable issue after the
direction, raw-capacity, phase-settling and internal-wake corrections.
No release/tag, merge into `develop`/`main`, version bump, push,
Home Assistant deployment or firmware change is included. Changes remain
uncommitted on the feature branch for review.
