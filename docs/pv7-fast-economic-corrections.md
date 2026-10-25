# pv7 FAST confirmed-offer and economic-deficit corrections

Date: 15 October 2026. Candidate branch:
`fix/pv-fast-confirmed-offer-economic-deficit`.
Baseline: `70f5ee3807034b9062ea16db483366275bb8a646`, published beta.14.
`git fetch origin` confirmed that both local and remote `develop` resolve to this
commit. The candidate is uncommitted and unstaged, for external code review.

## Scope and evidence

This change corrects only F1 and F2 from
[`pv7-beta14-investigation.md`](pv7-beta14-investigation.md), sections 6.2 and 7.
The complete investigation, consolidated Lastenheft/decision history and the
feedback-stability, economic-relaunch and grid-import-policy reports were read.
The V3 requirements remain a draft; no open policy question is resolved here.

The original local `.analysis/pv7.log` remains ignored and unchanged. Its SHA-256
was checked against the investigation:
`3d6eea35ce1504be0681603de5baeee2ed22e339ab71afe6035891a797567690`.
The regression data uses the logged power/voltage values and source ordering.
Historical source spacing is shifted into the live test fixture's freshness
window. Surrounding command/observer scheduling is reconstructed deterministically;
this is not a complete replay of unlogged hardware callbacks.

## F1 root cause and correction

`FastDischargeRegulator.request()` starts from its retained soft `requested`
value. It can lower that value against `hard_max` while physical response remains
unproved. `pv_plan()` then sends the result through DOWN selection, whose
direction is relative to requested **watts**, not to the last confirmed current.
Consequently, reducing the old 4200.376 W preference to 2706.761 W selects 1p/11 A
after 1p/6 A was already confirmed. A phase-lockout fallback can leave the same
old-preference/new-confirmation mismatch.

The regulator now records authorization only when its existing ordinary upward
branch passes source-generation, response, interval, settling, import and pending
checks. A changed confirmed phase/current key revokes that authorization.
Unchanged confirmation/reuse does not reset response timing or create new proof.
The original dispatch evidence, measured baseline, command direction and delayed
ACK handling are unchanged.

At the existing runtime-to-solver boundary, an unauthorized executable FAST
increase is capped at the confirmed current/phase point repriced using **current
valid voltages**. Frozen confirmation watts remain the original response basis;
repricing does not rewrite confirmation or measured response. This distinction
also prevents a voltage drop from converting an old watt cap into more amperes.
With unavailable voltage for that confirmed mode, the cap is conservative;
ordinary voltage/capability checks and the exact-floor query still apply.

The soft preference remains separate from this execution bound. Desired-phase
planning can still express a future phase intention; elapsed lockout/probe time
does not authorize a higher executable offer. A genuine response plus a new
complete generation releases ordinary progress, including a legitimate phase retry.

## F2 root cause and correction

The budget already uses measured EV power and a conservative EV floor. For the
logged E=5388.5634765625 W, B=4199.2 W, D=4000 W, import=29.4 W,
export=0 W, PV=1763 W and total load=5721.4 W, it produces H=5089.3634765625 W.
The confirmed 3p/7 A offer was 4717.655426025391 W; repricing it at the logged
voltages gives about 4800.511 W. Both nominal offers fit H, even though measured
consumption and battery discharge demonstrate an economic deficit. The old
request/nominal-offer comparison therefore retained 7 A indefinitely.

For measured B>D, the existing controller now retains the measured shortfall
`max(0, E-H)`. Once **all six sources cross the latest command's dispatch
evidence**, the executable offer is bounded by:

```text
current-voltage confirmed offer - max(0, measured EV power - H)
```

The result is clamped at zero and supplied as an additional solver bound, not a
replacement for H or an electrical limit. H retains its existing 100 W reserve
and conservative EV-floor calculation. In R12 the shortfall is 299.2 W, so the
approximately 4800.511 W offer is bounded to approximately 4501.311 W. With only
the verified 3p mode permitted, the real solver selects 3p/6 A, approximately
4114.723 W. With both modes permitted it can select a legally cheaper positive
phase/current point instead.

This measured economic bound also applies to desired-point planning. Otherwise,
the original higher desired phase could make the existing pre-dispatch phase
fence reject a legitimate lower point selected in another mode. The F1 response
bound remains execution-only, preserving desired/executable phase separation.

The projection is recomputed from the confirmed offer and qualified measurements;
it is not repeatedly subtracted from an old request. After reduction confirmation,
the same evidence cannot cause another step. A further complete post-command
cohort can support another bounded reduction if measured excess persists, even
if unchanged EV power has not proved physical compliance. Genuine response is
still observed separately. No fixed watts-per-amp vehicle response is assumed.

Original hard-budget reductions remain immediate, including with incomplete
post-command evidence, pending response or a long upward interval. The projected
correction does not create a new timer, polling path or independent regulator.

## Exact minimum and retained safety/lifecycle behavior

The pure solver, current-grid intersections, phase eligibility and technical
minimum query are unchanged. Only an independently revalidated **exact** minimum
witness may exceed the ordinary economic/feedback bound. It cannot authorize a
higher point. At the actual permitted minimum, positive continuation remains
available; battery-allocation excess does not become an independent OFF rule.

The unified measured-grid-import policy remains the sole economic STOP owner:
50 Wh of the full measured import above dynamic M/2, strict >15-second supported
reset, persistence and model-based restart inhibition are unchanged. Battery
allocation excess is not added to grid-import exposure.

OCPP dispatch serialization, authority, ownership, connection and transaction
identity, queued-command validation, post-dispatch snapshot semantics and actual
ACK handling remain in place. A real reduction creates a new response obligation;
ACK, command reuse, repeated generations and time alone do not discharge it.
The added hard solver bound also prevents soft current retention from undoing
the required reduction.

Requirements addressed: **PV-FAST-02/-03/-04**, **PV-PHASE-01/-02** and
**PV-ECO-06**. Adjacent protections: PV-ARCH-01, PV-OCPP-01/-02/-03,
PV-ECO-01 through -08 and PV-QA-01/-02. No normative requirement changed.

## Test-first evidence

Before any production edit, the initial three reconstructed cases failed against
the unmodified beta.14 checkout at final operating-point assertions:

```text
test_r6_exact_logged_unanswered_rebound
    assert plan[3].point.current_a == 6
E   AssertionError: assert Fraction(11, 1) == 6

test_r12_r13_measured_excess_selects_executable_reduction_or_minimum[7]
    assert plan[3].point.current_a == 6
E   AssertionError: assert Fraction(7, 1) == 6

test_r12_r13_measured_excess_selects_executable_reduction_or_minimum[6]
    assert plan[3].point.current_a == 6
E   AssertionError: assert Fraction(7, 1) == 6

3 failed in 1.61s
```

The third case additionally exposes F1 at the exact minimum: the old high soft
preference rebounds to 7 A. These were semantic current-selection failures after
the expected H and unproved-response conditions were established, not setup or
missing-API failures. After the initial two-file production correction, those
same cases passed: **3 passed in 1.70s**.

For final before/after evidence, the completed regression file is also run against
an isolated `git archive` of the exact beta.14 commit under `/tmp`, using the same
virtual environment and original production files. No baseline API compatibility
shim is needed. Temporary outputs are kept outside Git.

Final isolated beta.14 matrix: **13 failed, 8 passed in 6.95s**. Failures include
all three R6 profile variants, R12/R13, voltage-drop/current rebasing, unrestricted
phase selection, nonstandard current limits, above-nominal measured power,
stepwise deficits, combined deficit/response recovery and both delayed-fallback
variants. These are operating-point assertions; the remaining eight safeguarding
cases already pass beta.14. Corrected matrix: **21 passed in 8.02s**.

Reproduction commands, run in their respective repository/archive roots:

```bash
# Candidate and exact beta.14 archive (same test file and dependencies):
/home/elutz/Projekte/wallbox-manager/.venv/bin/pytest -q \
  tests/test_pv7_fast_corrections.py --tb=short --show-capture=no
```

The isolated archive is `/tmp/pv7-beta14-regressions`. Its production sources
come directly from `git archive 70f5ee3807034b9062ea16db483366275bb8a646`;
only the new regression file is copied into it.

## Regression inventory

`tests/test_pv7_fast_corrections.py` contains 21 HA/solver/simulated-OCPP cases:

* Exact R6 values and 19→6 A confirmations, source skew, retained preference and
  final 6 A retention for all three battery PV profiles; no 11 A wire command.
* R12 with the frozen logged nominal offer and contemporary unequal voltages;
  final 3p/6 A selection and confirmation. R13 retains positive exact-minimum
  operation without a separate battery-excess OFF rule.
* EV measured above, equal to and below the nominal offer; same evidence cannot
  drive another step or manufacture response after a reduction.
* Economic 7→6 A correction, asynchronous newer EV/older site data, unanswered
  hold, genuine downward physical response and normal resumed upward progression.
* Every stale post-command source channel separately, with prompt independently
  binding hard-budget reduction still available.
* Rejected 3p attempt, same-phase positive fallback, deliberately delayed fallback
  ACK, preserved original dispatch baseline, fresh/stale site combinations,
  command reuse and the falling-ceiling same-phase 11 A rebound. A genuine fresh
  response and sufficient headroom then permit a legitimate 3p retry.
* Voltage-drop repricing, all-phase availability, nonstandard minimum/current
  steps intersected with a real vehicle limit, and stepwise correction of
  unchanged measured excess through new source generations down to the minimum.

The tests inspect final solver-selected points, actual confirmed points and wire
request sequences. Existing directional/response and lifecycle tests supplement
this matrix. All results are simulated or automated; none proves physical control.

## Changed files and minimality

| File | Reason |
| --- | --- |
| `custom_components/wallbox_manager/pv_regulators.py` | Retain existing-gate upward authorization per confirmed command and measured economic shortfall; expose one conversion-boundary cap. |
| `custom_components/wallbox_manager/control/runtime.py` | Apply that cap through the existing shared solver using live voltage and the existing exact-minimum witness. |
| `tests/test_pv7_fast_corrections.py` | New deterministic integration regressions and safeguards above. |
| `tests/test_pv_beta12.py` | Qualify the real response/upward gate before an old voltage-only fixture injects its historical soft target; voltage, safety and retention assertions remain unchanged. |
| `docs/pv7-fast-economic-corrections.md` | This review document and validation evidence. |

The initial adjacent run had **215 passes and seven failures**, all in that one
old fixture's attempt to increase 6→7 A solely through an injected target while
suppressing the ordinary upward interval. One additional real `pv_request()`
before freezing its historical preference fixes the setup. No assertion or
production gate was weakened to preserve the old bypass.

Only two production files change. There is no new controller, independent state
machine, timer, ramp rate, tolerance or sensor requirement. BALANCE, SoC policy,
PV_OPTIMUM planning/forecast/FINISHED behavior, the economy model/store, station
firmware, EVSE diagnostics, sensor acquisition and integration version are unchanged.

## Validation

Validation commands:

```bash
.venv/bin/pytest -q --tb=short --show-capture=no
node tests/test_wallbox_card.cjs
node --check custom_components/wallbox_manager/www/wallbox-manager-card.js
.venv/bin/ruff check . --no-cache
.venv/bin/ruff format --check . --no-cache
git diff --check
```

| Check | Result |
| --- | --- |
| New integration regression matrix | **21 passed**, 8.02 s |
| Complete Python suite, final assertion-complete run | **2505 passed**, 5 dependency deprecation warnings, **272.55 s** |
| Frontend regression suite | **145 passed**, zero failed |
| JavaScript syntax check | Passed |
| Ruff lint | All checks passed |
| Ruff formatting | **206 files already formatted** |
| Whitespace check | Passed |

Compared with beta.14's reference, Python gains exactly **21 tests**
(2484 → 2505); frontend remains **145**. The complete Python suite covers existing
FAST/BALANCE, physical direction, power budgeting, phase lockout, OCPP confirmation,
snapshot/transaction fencing, import accounting/storage/relaunch/restart,
PV_OPTIMUM/SoC and SURPLUS/MAXIMUM shared execution regressions. The five warnings
originate from upstream Home Assistant/backoff uses of deprecated
`asyncio.iscoroutinefunction`.
The preceding complete run also passed all 2505 tests in 221.22 s; the final
repeat above includes the strengthened delayed-fallback rebound assertions.

Final candidate inventory: three modified tracked files and two new task files,
all unstaged. HEAD remains the beta.14 baseline. Pre-existing untracked files
preserved separately:

```text
docs/ocpp-meter-values-interval-investigation.md
docs/pv-decision-logic-audit.md
docs/pv7-beta14-investigation.md
docs/validation/beta9-candidate-source-manifest.json
docs/validation/beta9-deployment-rollback.md
docs/validation/beta9-hardware-readiness.md
docs/validation/beta9-hardware-test-plan.md
pv2.log
```

Python integration runs require local-loopback access for temporary HA/OCPP
fixtures. An initial restricted invocation could not start those fixtures and
was interrupted; it supplies no correctness result. Setup-only failures while
constructing the exact numerical fixtures were corrected before recording the
semantic beta.14 failures above. Intermediate candidate test failures also caught
the necessary desired-phase economic-bound interaction and test cohorts that
did not supply enough headroom/new source evidence for a legitimate phase retry.

## Deliberately unresolved questions and hardware acceptance

The observed 7 A nominal offer versus roughly 8 A-equivalent measured power is
not diagnosed or repaired here. Actual CP current, station register/readback,
phase currents, metering attribution, deployed station revision and inverter
acquisition timing need independent physical evidence. Cold adopted under-offer
states without a causal baseline remain conservative; no response timeout or
widened tolerance is introduced. The historical T-versus-L lockout SoC conflict,
permission/startup hypothesis, separate EVSE activity/diagnostics defect and
unagreed quantitative BALANCE acceptance criteria remain outside this task.

Remaining supervised hardware acceptance, after separately authorized release
and HACS installation:

1. Capture installed manager/station provenance, synchronized raw OCPP commands
   and ACKs, effective CP/current/phase feedback and original meter timestamps.
2. Reproduce the 19→6 A reduction and rejected-phase positive fallback, including
   delayed ACK and asynchronous measurements. Require no unanswered 6→11 A
   rebound; require normal progress after genuine fresh physical response.
3. Reproduce sustained B>D at 3p/7 A. Confirm a legal positive reduction and
   measure the actual EV/battery response instead of assuming a 1 A power delta.
   Confirm exact-minimum continuation and electrical/phase restrictions.
4. Verify combined deficit/fallback/recovery sequences and existing measured
   50 Wh STOP/reset/restart, same-transaction reload and late-ACK protections.
5. Perform the separately specified BALANCE and permission/current-truth tests;
   these fixes do not constitute their hardware acceptance.

No hardware commands, deployment, release, tag, version bump, commit, push or
merge are part of this candidate. The eight pre-existing untracked files remain
untouched and unstaged; `.analysis/` remains local and ignored.
