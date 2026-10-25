# PV economic accounting across internal task relaunches

Date: 2026-10-14. Branch: `fix/pv-regulation-feedback-stability`.
Scope: the existing, uncommitted beta.14 candidate. Current `docs/lastenheft/`
requirements and the unified economic import policy were read before changes.
The earlier FAST/BALANCE corrections remain binding.

## Investigation

Demonstrated violations: **PV-ECO-03, PV-ECO-04, PV-ECO-05**.
Retained guarantees checked alongside them: PV-ECO-07/-08, PV-OCPP-01/-02 and
PV-QA-02.

An ordinary valid meter event can resume the existing regulation loop after
waiting for input or startup eligibility. `PVSurplus.pv_measurement_changed()`
plans the new state, calls `GridProfiles.invalidate()` and relaunches the task.
The beta.14 correction already retains the power regulator, session identity and
startup clock across this synchronous internal operation.

However, general invalidation also calls `economic_invalidate()`, which invokes
`GridImportPolicy.invalidate("lifecycle_invalidated")`. That policy operation
correctly preserves proved energy, activity, pending/in-flight/paused flags,
economic STOP cause, restart inhibition/reference and meter-generation deduplication.
It also deliberately drops the meter anchor, pending causal events and proved
below-threshold duration. This is appropriate at real measurement/authorization
uncertainty boundaries, but an internal task replacement is not such a boundary.

The difference is observable without changing the accounting equations:

* At 2,000 W import with supported samples at 0 and 5 seconds, proved energy is
  10,000 W s. Invalidating between that sample and a valid sample at 10 seconds
  leaves it at 10,000 W s, instead of the supported 20,000 W s. Already proved
  energy survives; the next otherwise-supported interval is skipped.
* With retained energy and 500 W import below a verified 690 W threshold,
  samples at 0, 5 and 10 seconds prove ten below-threshold seconds. Invalidation
  loses this proof. Samples at 15 and 15.001 seconds no longer reset energy at
  the original strict >15-second boundary, despite supported source/acceptance
  continuity.

The new deterministic integration tests reproduced both violations **before the
production correction**, using actual HA state listeners, the real
`pv_measurement_changed()` callback, task replacement and OCPP peers:

* During an owned, physically confirmed positive charge, 800 W import at t=0/5
  proves 4,000 W s. A PV-input gap prepares protective OFF, but recovered PV
  arrives before OFF dispatch. The callback increments the epoch and replaces
  the task; the next valid import sample at t=10 leaves energy at 4,000 W s
  instead of 8,000 W s.
* After a real protective OFF is confirmed, low-import samples at t=5/10/15
  prove ten reset seconds with 10,000 W s retained. A real PV recovery relaunch
  immediately changes the proved reset duration to zero instead of retaining ten.

Baseline command:

```text
.venv/bin/pytest -q tests/test_pv_economy_relaunch.py -k 'import_energy or below_threshold' --tb=short --show-capture=no
```

Result: **2 failed, 9 deselected, 1.91 seconds**. Both failures were economic
assertions after the tests had established actual task replacement, not setup
failures. No FAST/BALANCE power calculation needed to change.

## Required distinction

Only a validated, same-scope, non-pending meter-driven task relaunch may retain
live accounting evidence. Genuine user OFF, transaction termination, authority
or connection uncertainty, explicit control edits and other lifecycle changes
retain their existing behavior. Explicit OFF/NETZ/real transaction termination
end economic scope through the existing implementation. Authority loss breaks
live coverage without granting a fresh budget or erasing STOP history.

Actual missing/invalid/future/out-of-order measurements and acquisition or
acceptance gaps remain owned by the existing economic observer. They must still
break reset continuity without clearing proved energy. Duplicate generations
must neither add energy nor advance reset time. Retaining evidence across a task
replacement cannot manufacture additional samples or bypass these checks.

## Correction and verification

The correction adds one optional preservation flag through the existing
invalidation chain:

* `GridProfiles.invalidate(..., preserve_economic_evidence=False)` forwards the
  choice to `economic_invalidate(..., preserve_evidence=False)`.
* Only the validated, non-pending, same-scope meter-driven relaunch opts in.
* The economic invalidator still removes activation-pending, wait and positive
  dispatch authorization markers. It skips only `GridImportPolicy.invalidate()`
  when preservation is requested. Task cancellation, epoch advancement and
  control-intent generation changes retain their existing behavior.

The live policy is retained in place: its previous sample, pending causal
threshold/activity events, generation fingerprints and proved reset duration
remain available to the next supported distinct sample. No energy or reset time
is added by the relaunch itself. Normal invalidation defaults remain in effect
for every other caller, and the observer still independently rejects or
reanchors invalid or unsupported intervals. A relaunch cannot restore evidence
that an earlier invalid observation already discarded.

The regression file is `tests/test_pv_economy_relaunch.py`. It covers:

* A real internal relaunch during measured import accumulation, preserving the
  same policy and identity and counting the next supported interval exactly.
* A pending, physically observed voltage/M/2 change across relaunch, splitting
  the interval at the original causal time rather than applying it retroactively.
* Ten proved low-import seconds across a protective-OFF/recovery relaunch, with
  energy retained at exactly 15 seconds and reset only at 15.001 seconds.
* A measured 182,000 W s exhaustion, real confirmed economic OFF and subsequent
  low-import reset. Both a blocked ordinary wake and an actual BALANCE/start-delay
  task relaunch retain the first STOP reference/cause and restart inhibition;
  unchanged or worse economics still send no positive command after the delay.
* Missing/invalid grid input, independent source/acceptance gaps, repeated HA
  reports of one physical generation, and interruption of an established reset
  interval. Unknown intervals contribute neither energy nor reset time.
* Actual user OFF and genuine OCPP transaction termination versus authority loss
  and disconnect. Scope removal and uncertainty invalidation remain distinct.

The focused existing suite also exercises the real delayed-OCPP-ACK marker
revocation test, policy persistence/recovery, dynamic technical minimums,
FAST/BALANCE dispatch/response behavior, startup/snapshot/restart fences,
ownership and authority. Source review confirms authorization markers are still
cleared unconditionally during the preservation path.

An independent SHA-256 audit against the pre-task candidate snapshot found only
the three production files above changed among 223 snapshotted files. The prior
FAST/BALANCE regulator, optimum logic and diagnostic changes, economic model and
storage, manifest, OCPP/control runtime, authority/ownership, SoC, phase handling
and solver remain unchanged. This task adds only the new regression file and
this report besides that narrow production correction.

Final validation:

| Check | Result |
| --- | --- |
| New real-callback regressions plus two existing wire-marker revocation cases | 19 passed; 40 deselected; 5.03 s (17 new cases) |
| Focused existing economic, feedback, snapshot/restart/startup, ownership and authority suites | 484 passed; 1 dependency warning; 57.68 s |
| Full `.venv/bin/pytest -q` | **2,484 passed**; 5 dependency deprecation warnings; 209.44 s |
| `.venv/bin/ruff check . --no-cache` | All checks passed |
| `.venv/bin/ruff format --check . --no-cache` | 202 files already formatted |
| `git diff --check` | Passed |

The Python runs used approved local-loopback access for the actual HA/OCPP test
peers. Test logs remain under `/tmp`, outside the source changes.

No threshold, reset duration, restart comparison, power regulation, SoC/day
planning, phase operation, OCPP command handling or authority rule changed.
No commit, merge, push, version change, release or deployment was performed.
The result remains uncommitted on `fix/pv-regulation-feedback-stability`, with
HEAD unchanged at `e3debc135224b1e1b1cd85a306e0a57a1826f3e5`.
