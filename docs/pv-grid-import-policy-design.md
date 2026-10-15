# Unified PV import policy

This implementation is experimental. It changes economic charging decisions;
it has not been deployed to Home Assistant or tested on charging hardware.
The integration version remains unchanged.

Implementation status: the accounting/comparison model, storage, lifecycle and
shared minimum/STOP/restart decisions are implemented. The reviewed STOP migration
was applied after explicit user approval. Focused and full regression validation,
Ruff lint, formatting and whitespace checks pass. This worktree has no deployment
authorization. The final reviewed changes are authorized for one feature-branch
commit only.

## Decision hierarchy

1. Existing explicit ownership, Remote authority, connected station and matching
   external transaction establish the control scope. A profile does not acquire
   authority. Unknown recovery history cannot create a fresh energy allowance.
2. Existing profile availability, critical input validation, battery target SoC,
   hysteresis and startup rules determine eligibility. The forecast target
   algorithm and Wh/kWh/MWh normalization are unchanged.
3. The shared FAST/BALANCE regulator computes its ordinary power preference and
   economic battery allocation H. All battery-equipped PV profiles use this
   regulator; target selection remains profile-specific. Battery-free SURPLUS
   uses measured EV power plus measured export minus measured import.
4. A separate technical solver query determines M, the smallest executable
   positive point. It retains verified voltage, phase capability/restriction,
   current grid, EVSE limits and other supplied electrical limits. It excludes
   only economic H. Unknown feasibility does not manufacture a 1380 W floor.
5. An eligible continuation may offer exactly M when ordinary allocation cannot
   reach a positive point. The witness is revalidated at the command boundary.
   Higher points remain subject to ordinary H; physical limits are always binding.
6. The measured-import energy state machine owns economic STOP. Exhaustion
   requests OFF through the existing serialized sender. ACK, BUSY, failures,
   phase restrictions and stale-command fences remain owned by that sender.
7. Confirmed economic OFF can reset its energy budget after supported low import,
   but automatic restart also passes normal startup and the model comparison below.

The H-minimum economic veto, independent 10-second minimum-import STOP and
BALANCE/SURPLUS deficit STOP timers are replaced by this one policy. The existing
response settling window, OFF-response verification, startup response reserve,
SoC protection, missing-input handling and technical retry deadlines remain.
The legacy `pv_stop_delay` configuration remains stored for compatibility; it no
longer controls economic STOP. FAST's existing response correction delays adjust
ordinary power optimization and do not create a separate economic STOP allowance.

## Accounting convention

T = M / 2. Only actual measured positive site import P is used. Strict P > T
counts the entire P. P <= T suspends counting. Exact rational W seconds are
compared against 180000 (50 Wh). Strictly more than 15 seconds of supported
P <= T resets the budget; exactly 15 seconds does not.

Distinct meter generations close intervals using the previous sample held
constant until the next first acceptance. Duration uses monotonic first-acceptance
time. The canonical generation includes the mapped entity and source acquisition
time (`observed_at`, otherwise HA `last_reported`). Both the source interval and
acceptance interval must be at most 10 seconds. Both 10 seconds and zero elapsed
acceptance are supported. Delayed source timestamps still must pass normal live
freshness validation. Duplicate generations contribute once; old, conflicting,
future, missing or stale generations reject coverage without erasing proved energy.
Gaps break reset continuity and establish a new anchor at the next valid sample.

Threshold and confirmed activity changes split an interval at their causal
monotonic observation. A changed T reclassifies the current sample immediately,
without retroactively changing earlier segments or resetting E. Equal-valued HA
`state_reported` events can supply new meter generations. Observer and dispatch
processing share the same deduplicating policy; observers only wake the existing
regulator and cannot send hardware commands.

Once OFF is confirmed, unrelated household import adds no EV exposure. Valid
below-threshold reports may still prove reset. Queued, rejected or BUSY OFF does
not count as confirmed OFF and does not erase accumulated energy.

## Model-based restart and anti-cycling comparison

Use the existing inputs E (measured EV power), I (measured site import), X
(measured export), B (measured battery discharge), PV, total load L and configured
economic discharge allocation D. Let G = I - X. D is the configured allocation
in FAST and zero in BALANCE, whose intended source is PV surplus.

The signed grid-neutral capacity score is:

* Without battery: C = E - G.
* With battery: C = min(E + D - B - G, E + D + PV - L) - 100 W.

The 100 W reserve is the existing battery model uncertainty allowance. This
derivative removes the existing import allowance from H and uses actual E, not
the historical EV floor, ramp target, a clamped nonnegative H or elapsed time.
For coherent flows, removing EV load and its associated grid/battery contribution
leaves C unchanged. No mandatory battery-readiness entity is introduced.

At the first proved 50 Wh exhaustion, save C_stop and the then executable
technical minimum M_stop. Preserve this reference once, including across repeated
OFF retries, low-import reset and reload. Unknown STOP-time model evidence leaves
the reference unknown and inhibits automatic restart; it is never filled later
from post-OFF measurements.

For a proposed restart point W_candidate, meaningful improvement means exactly:

`C_now - max(W_candidate, M_now) > C_stop - M_stop + 100 W`

This reuses the regulator's existing 100 W grid deadband. Equality does not pass.
The cost reference is M_stop rather than the former higher operating point:
selecting a cheaper point already available at STOP cannot earn another cycle.
A genuinely newly executable lower minimum can count, as can increased supply
capacity or intended battery allocation. Clock time, measurement generations,
budget reset and changing only target metadata cannot count. Normal profile,
SoC, authority, electrical, freshness and startup conditions are additional gates.
If a normally eligible preferred point spends too much of the improvement to
pass, the planner may retry this comparison at the exact technical minimum.
It reapplies the existing startup response reserve and OFF-response proof. This
does not enable an allocation-less battery-free/BALANCE initial start, or waive
the normal FAST battery-assisted startup conditions.
M_now uses the proved physical phase restriction. A timed permission to probe a
lower phase does not itself prove that cheaper minimum executable; the comparison
charges at least the current proved minimum until the restriction clears. Real
capacity improvement can still authorize the existing technical probe.

Before this comparison, fresh EV and site sources must cross the confirmed OFF
boundary. EV power must show the existing <=100 W no-load response, and battery
regulation must have its existing OFF-response proof. Site evidence must cross
the EV acquisition boundary; stale positive EV power mixed with post-OFF site
measurements cannot simulate an improvement.

The model cannot guarantee actual future battery support. A legitimate
battery-assisted restart may still reach another economic STOP if the battery
does not respond as expected. The comparison prevents retrying unchanged modeled
conditions; it is not an inverter readiness or future power guarantee.

## Scope, persistence and recovery

One HA Store record binds exact energy, active/pending/in-flight/paused state, cause and
restart reference to entry, connector, station vendor/model/serial/firmware,
external transaction and fixed policy metadata. No monotonic anchor or partially
elapsed below-threshold reset is persisted. No downtime is extrapolated.
The historical `restart_required` flag is separate from current physical OFF.
An unexpected live readback or repeated OFF cannot erase the earlier economic
pause. A sent OFF retains its cause and comparison reference through budget reset
until its outcome is known. A known BUSY or unsent stale command may cancel only
an unconfirmed stop episode whose energy has reset; it cannot clear a confirmed
pause. Preparing a positive limit while charging permission is disabled does not
prove restart or clear this history.

An authorized positive command with permission enabled can establish activity by
a fresh actual EV response above the existing 100 W no-load deadband before its
protocol ACK. That evidence is bound to the current station connection, authority,
transaction and control-intent generation, and is revoked by lifecycle or direct
control edits. Accounting starts at the physical response observation. A later
positive ACK cannot erase a newer
50 Wh exhaustion proved while that ACK was pending.

The first dirty update schedules a checkpoint five seconds later. Subsequent
updates do not trail that deadline. Exhaustion is flushed before requesting OFF;
confirmed economic OFF and teardown also flush. Write failure inhibits subsequent
automatic positives but cannot suppress required OFF. With healthy scheduling,
the exposure is one accepted interval (at most 10 seconds) plus the five-second
checkpoint scheduling delay. Disk latency and event-loop interruption add time;
without an actual site power cap there is no universal Wh crash-loss bound. A
completely failed disk cannot durably record its own failure.

**15 October 2026 target-contract clarification:**
[PV-OCPP-04](specifications/pv-regulation.md#pv-ocpp-04--charging-permission-connection-readiness-and-physical-cp-observability-approved)
supersedes the dependency that general permission waits for transaction-bound
ON preparation. The recovery description below remains historical implementation
evidence at that ordering boundary. Restored exhaustion and uncertainty still
inhibit automatic positive operating points; permission without a real transaction
creates no accounting scope or reset. Retained-current startup before transaction
identity/manager-command evidence has an OPEN attribution question in PV-OCPP-04;
no historical intervals are fabricated and no accounting policy is invented here.

Recovery checks economic history before both disabled-station ON preparation and
enabled-station readback. Restored exhaustion cannot dispatch a positive point.
Missing, corrupt, mismatched or uncertain recovery inhibits automatic positives;
a retained owned charge is safely stopped through the normal boundary where
technical command prerequisites permit it. No fresh zero budget is granted by
reconnect or persisted ON intent.
For a disabled station with trusted exhausted history, persisted ON intent waits
in the existing startup task. Normal regulation-interval and meter-event wakes
recheck the verified reset and normal preparation conditions; there is no new
economic restart timer. Reset alone cannot authorize a positive charging point.

FAST/BALANCE and applicable PV profile changes retain the same verified budget.
Explicit user OFF, explicit NETZ scope exit and genuine transaction termination
end the scope. Re-enabling or selecting another PV profile alone cannot refresh
an existing allowance. Only an explicit new activation after scope exit or a
proved new transaction can begin fresh state. An identity handover marked
`superseded` is not proof of a genuine electrical transaction end.
The initial explicit scope checkpoint is also fenced across its storage await.
OFF, a scope exit or newer lifecycle context retires the binding before the old
preparation resumes. A stale ON completion cannot reattach that binding or
overwrite the newer OFF result; a later explicit ON can begin normally.

## Diagnostics and hardware limitations

Profile attributes expose actual import, M, T, E in Wh, proved below-threshold
duration, accounting state/cause, coverage gaps and restart inhibition. Missing
actual meter values remain unknown. Legacy PV/load-only SURPLUS configuration
remains visible, but charging requires mapped measured grid input for this policy.

Technical feasibility uses the limits actually supplied by the existing adapter;
it cannot discover unmapped vehicle/inverter protection limits. Meter cadence
above 10 seconds yields no accounting credit and breaks reset proof. Sensor
acquisition metadata remains necessary to distinguish repeated real reports from
re-publication of one device acquisition. Asynchronous sensors may conservatively
inhibit restart when the existing EV/site source-evidence contract cannot prove
the STOP reference or OFF response. Hardware validation must inspect these
diagnostics and confirm actual battery and station responses.
The 50 Wh threshold requests STOP; it is not a guaranteed physical energy cap.
Sampling cadence and station acknowledgement, failure or BUSY delays can allow
additional measured import before the device actually stops.

## Implementation inventory

The new `pv_economy.py` contains exact accounting and comparison state;
`pv_economy_storage.py` contains identity validation and HA Store checkpoints;
`pv_economy_control.py` binds them to the existing profile and command lifecycle.
The integration changes are in `profiles.py`, `pv_surplus.py`, `pv_optimum.py`,
`pv_regulators.py`, `pv_budget.py`, `pv_diagnostics.py` and `control/runtime.py`.
The options help text in `strings.json`, `translations/en.json` and
`translations/de.json` describes measured grid import/export requirements and
the retained, inactive economic `pv_stop_delay` setting. No dependency, entity
identity, public service or manifest version changes are included.

New regression files are `test_pv_economy.py`, `test_pv_economy_storage.py`,
`test_pv_economy_control.py`, `test_pv_economy_voltage.py` and
`test_pv_profile_consistency.py`. Existing PV, ownership, command, ledger,
diagnostic, smoothing, startup and transient tests now supply actual grid input,
coherent acquisition generations and explicit physical phase proof. Assertions
for the retired economic timers were replaced with verified-energy expectations;
authority, SoC, startup reserve and command-fence assertions remain binding.
The configuration-store migration fixture supplies the existing control
subscription interface and closes its added economic store/listeners.

The existing regression files changed are:

```text
tests/test_config_flow.py
tests/test_grid_profiles.py
tests/test_ownership_recovery.py
tests/test_pv_beta12.py
tests/test_pv_beta2.py
tests/test_pv_beta9.py
tests/test_pv_command_lifecycle.py
tests/test_pv_diagnostics.py
tests/test_pv_hysteresis_delay.py
tests/test_pv_input_diagnostics.py
tests/test_pv_ledger_consistency.py
tests/test_pv_optimum_desired.py
tests/test_pv_optimum_hold.py
tests/test_pv_phase_lockout_continuation.py
tests/test_pv_restart_review.py
tests/test_pv_smoothing.py
tests/test_pv_snapshot_fences.py
tests/test_pv_startup_reconciliation.py
tests/test_pv_surplus.py
tests/test_pv_transient_gaps.py
tests/test_regulation_settings.py
```

The audit's 11:02:43 scenario is reproduced deterministically with measured
voltages, 3-phase/6 A, H=4033.944 W, M=4121.190126 W and actual import=1078.7 W.
The approximately 87 W economic shortfall preserves exactly 6 A continuation
because import remains below M/2. This reproduces the decision inputs; it makes
no claim about hardware behavior after the historical recording ended.

## Validation

Run from the dedicated worktree with the existing repository virtual environment:

```bash
/home/elutz/Projekte/wallbox-manager/.venv/bin/pytest -q tests/test_pv_economy.py tests/test_pv_economy_storage.py tests/test_pv_economy_control.py tests/test_pv_economy_voltage.py tests/test_pv_profile_consistency.py tests/test_regulation_settings.py --tb=short --show-capture=no
/home/elutz/Projekte/wallbox-manager/.venv/bin/pytest -q --tb=short --show-capture=no
/home/elutz/Projekte/wallbox-manager/.venv/bin/ruff check .
/home/elutz/Projekte/wallbox-manager/.venv/bin/ruff format --check .
git diff --check
```

The implementation run before final review passed 337 focused tests in 13.83 seconds, with one upstream HA
deprecation warning. Ruff lint, all 186 formatting checks and whitespace checks
passed. That full run passed **2415 tests in 206.22 seconds**, with five
upstream HA/backoff deprecation warnings and zero failures.

During migration, earlier full runs failed on retired economic-timer expectations
and fixtures without actual grid, phase or source-generation proof. Those fixtures
and policy assertions were updated. Regression work also corrected genuine
startup-floor, delayed-command, pause-provenance and invalidation races described
above. One earlier full run had 2413 passing tests and one failure: the
configuration-storage test double lacked the newly used existing `subscribe`
interface. Its fixture now implements that interface; the focused run above
includes and passes the entire configuration-storage test file.

Some initial tool invocations could not execute because of transient approval
review capacity errors or a read-only Ruff cache. Retried checks completed; no
validation or approval issue remains outstanding.

The final branch is `codex/pv-unified-grid-import-policy`, based on local develop
`7dd748e67164f393838412b914bccb0324179438`. Its 40 changed/new files are ten Python
source files, three UI help catalogs, 26 regression files and this document.
The primary develop checkout and its pre-existing untracked files are preserved. The manifest remains
`0.3.2-beta.12`. Only one feature commit is authorized; push, merge, tag, release,
deployment and hardware tests remain unauthorized. No unresolved comparison-policy
question remains; the physical battery, meter and station limitations above
require later hardware validation.

## Final source review — 2026-10-12

The complete ten-file Python source diff was reviewed against the unchanged
develop base above, including the three new modules absent from a normal tracked
diff. Review covered exact full-import accounting, causal M/2 changes, freshness
and deduplication, strict >15-second reset, exact-floor witnesses and real
electrical limits, the signed model comparison and noise bound, identity-bound
storage, recovery, target-SoC/profile semantics, and OCPP authority/command fences,
OFF acknowledgement and retries. The beta.10 ledger/evidence implementation,
forecast algorithm, public controls, physical lockout durations and version
remain unchanged. No further material policy deviation was found.

Two review defects were corrected:

* The options help still described the retired PV/load input requirements and
  did not explain that the stored stop-delay value is inactive for economic
  STOP. English, German and source help now describe the current requirements.
  Four real-HA frontend-serialization regressions verify selecting both grid
  meters, either meter alone, and clearing the saved pair. Grid-only SURPLUS
  needs no battery, PV-generation, consumption or forecast mapping; partial
  grid-only mapping keeps NETZ available without enabling SURPLUS.
* An initial checkpoint could complete after explicit OFF and reattach its
  ended in-memory budget. A deterministic regression reproduced this failure.
  Scope/lifecycle checks now reject stale binding restoration, and the permission
  caller checks its epoch before handling the returned preparation result.
  The regression verifies preserved OFF, no remaining record/binding, no stale
  status overwrite, and normal later explicit ON. The affected activation,
  startup, economic-control and command-edge group passed 84 tests in 13.43s.

Initial UI-test failures were fixture issues: HA requires its entry-update API
and serializes selector domains as a list. The corrected four cases pass. The
source fixes above add no new charging policy, threshold, sensor or retry timer.
Final review validation used these exact commands from the feature worktree:

```bash
/home/elutz/Projekte/wallbox-manager/.venv/bin/pytest -q tests/test_config_flow.py tests/test_translations.py tests/test_pv_profile_consistency.py tests/test_pv_economy.py tests/test_pv_economy_control.py tests/test_pv_economy_storage.py tests/test_pv_economy_voltage.py --tb=short --show-capture=no
/home/elutz/Projekte/wallbox-manager/.venv/bin/pytest -q tests/test_pv_economy_voltage.py tests/test_pv_economy_control.py tests/test_pv_activation.py tests/test_pv_startup_reconciliation.py --tb=short --show-capture=no
/home/elutz/Projekte/wallbox-manager/.venv/bin/pytest -q --tb=short --show-capture=no
/home/elutz/Projekte/wallbox-manager/.venv/bin/ruff check .
/home/elutz/Projekte/wallbox-manager/.venv/bin/ruff format --check .
git diff --check
```

The UI/localization and policy focus passed 353 tests in 14.20s before the startup
race was added; the affected group after that fix passed 84 tests in 13.43s.
The final full suite passed **2420 tests in 205.48s**, with five upstream
HA/backoff deprecation warnings and no failures. Ruff lint passed, all 186 Python
files passed the formatting check, and the whitespace check passed. No material
issue remains unresolved. All validation used temporary HA instances and local
simulated OCPP peers; none connected to charging hardware or deployed the code.
