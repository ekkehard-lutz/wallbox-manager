# Phase-switch lockout continuation implementation report

## Review status

- Baseline: `v0.3.2-beta.10`, commit `a73145a` on `develop`.
- Fix branch: `codex/pv-phase-lockout-minimum`.
- Implementation commit: `8a7c4d168b094213585e694c8f0eabc25869f44e`.
- Validation results: all 2040 Python tests passed; see the validation section.

The change is restricted to PV continuation, phase-response stabilization, and
their regression tests. It preserves the beta.10 session ledger correction.
The diagnostic log remains a local investigation input and is excluded from the
commit and release artifacts.

## Exact cause of the unexpected STOP

The October 10 hardware trace directly records a successful `3p/7A` command at
13:57:55.637, followed by a `1p/10A` request at 13:57:56.789. The station rejected
the reverse transition at 13:57:57.030 with the correctly classified temporary
`phase_switch_lockout` response. The last applied point remained `3p/7A`, and the
next diagnostic evaluation identified three physically confirmed phases.

The diagnostic evaluation beginning at 13:57:56.721827 records:

| Quantity | Recorded value |
| --- | ---: |
| Raw regulator request | 2331.2 W |
| Independent hard power ceiling | 6227.7 W |
| Measured EV power during physical switching | 0 W |
| Measured net grid import | 0 W |
| Battery SoC | 84.3% |
| PV Optimum target SoC | 73.295128% |
| Minimum reachable three-phase offer | 4112.122742 W |

The minimum point was within the independent hard ceiling. However, the existing
`minimum_allowed()` check also reserved half of unobserved upward headroom. With
the temporary zero EV sample, that check allowed only
`0 + (6227.7 - 0) / 2 = 3113.85 W`, so it rejected the valid 4112.12 W floor as
`minimum_response_budget`. The pause policy converted that response-reserve
rejection into `hard_budget_pause`. The following cycle attempted OFF at
13:57:57.979 and confirmed it at 13:57:58.205. The trace then records a restart
rejection with `busy` at 13:58:57.336.

This STOP arose from applying a startup/upward-response reserve to continuation
of an already permitted charge. The trace demonstrates neither an actual hard
power-ceiling violation nor excessive measured grid import nor a battery SoC
stop at the time of the OFF command.

Relevant source: `pv_regulators.py:265` (`minimum_allowed`),
`pv_optimum.py:601` (`optimum_pause_policy`), and
`control/runtime.py:1130` (specific lockout handling). These paths are under
`custom_components/wallbox_manager/`.

## Why the immediate reverse phase request occurred

Direct observations establish that the accepted three-phase command originated
from a 4958.063 W regulator request. Its diagnostic record still used a positive
EV sample acquired at 13:57:51.413673. After the phase switch, a zero EV sample
acquired at 13:57:54.237052 arrived while the response was still settling. By the
reverse-command evaluation, the regulator's previous request had already fallen
to 2331.2 W even though the refreshed hard ceiling had recovered to 6227.7 W.
The trace marks the regulator as `awaiting_physical_response` throughout that
evaluation.

The intermediate budget reduction is reconstructed from the code and recorded
source values; it is not a separately logged regulator evaluation. Combining
the zero EV sample with the preceding external measurements gives the exact
2331.2 W value:

```text
incremental = 0 + 3500 - 1351.6 + 282.8 = 2431.2 W
site-flow   = 0 + 3500 + 3349.3 - 4246.2 = 2603.1 W
hard ceiling = min(incremental, site-flow) - 100 = 2331.2 W
```

The measurement observer can update the regulator while serialized OCPP work is
pending. Hard reductions intentionally bypass interval, generation, and settling
gates. When the subsequent external measurements restored the hard ceiling,
the regulator retained the reduced request while awaiting physical response.

The previous phase retention rule alone did not prevent reversal. It retains a
current-phase candidate only when actual charging evidence exists and the
candidate satisfies the directional solve within the configured 5% tolerance.
The temporary connected/zero-power state removed active-charging retention; a
2331.2 W DOWN request could use `1p/10A`, while the three-phase minimum exceeded
that soft request. Consequently the solver selected another phase configuration
only about one second after acknowledgment of the three-phase offer.

Relevant source: `pv_optimum.py:453` (measurement observation),
`pv_regulators.py:208` and `pv_regulators.py:220` (settling and immediate hard reductions),
`pv_budget.py:43` (independent budget), `solver/power.py:95` and
`solver/power.py:208` (phase retention), and
`protocols/ocpp/v21/control_runtime.py:14` (actual charging evidence).

## Implemented continuation

The known lockout context requires live charging permission and a positive
confirmed offer. It survives temporarily unknown physical-phase feedback so
that a valid SoC below target can still stop the charge. Positive continuation
additionally requires fresh canonical physical-phase evidence matching the
confirmed offer. A rejected requested phase is never substituted for physical
feedback. The common
electrical solver calculates the minimum positive point using only those
confirmed phases, verified device capabilities, current limits, the supported
current grid, and fresh measured phase voltages.

For the incident voltage readings, the minimum is:

```text
6 A × (228.969879 + 227.625854 + 228.758057) V = 4112.122742 W
grid-import allowance = 0.5 × 4112.122742 W = 2056.061371 W
```

At nominal 230 V per phase, this is the familiar `3p/6A`, 4140 W minimum and
2070 W allowance. The implementation uses the actual achievable operating
point, including a higher supported minimum or coarser current step when
applicable.

For established continuation, `minimum_allowed(..., continuing=True)` skips only
the half-step upward-response reserve. It retains the independent hard ceiling
and the existing pause-response guard. The 50% allowance is not added to the
hard battery budget. If the genuine hard ceiling or electrical limits cannot
support the minimum, the normal safety pause remains available.

Grid enforcement reads the authoritative configured import and export channels
with their existing freshness checks. It checks total site net import, without
adding or subtracting battery discharge, PV production, household power, or EV
power again. Unavailable, stale, negative, ambiguous EV-source, or materially
conflicting import/export measurements cannot authorize continuation. Excess
import uses the existing 10-second confirmation grace and restart hysteresis;
the allowance remains bounded after the grace. Where the existing configuration
does not supply a battery-discharge measurement contract, continuation still
requires fresh authoritative grid measurements and the same import gate; it
does not invent a battery ceiling. Where that contract exists, its independent
hard ceiling remains mandatory.

The shared fallback applies to `PV_SURPLUS`, `PV_OPTIMUM`, and `PV_MAXIMUM`.
Surplus joins the shared execution path during confirmed lockout continuation;
its ordinary energy selection remains intact outside that context. Existing
SoC freshness and target calculations remain authoritative. During lockout
continuation, equality with the existing target permits charging, and a valid
SoC below that target stops charging. The change creates neither another target
nor a hidden reserve. Ordinary profile hysteresis remains unchanged outside the
fallback. A configured SoC channel remains mandatory and fresh during the
fallback. Surplus also supports its existing optional battery-free
configuration: when no SoC channel is configured, there is no SoC threshold to
invent. The authoritative grid measurement requirement still applies.

Relevant source: `pv_optimum.py:87` (`phase_lockout_continuing`),
`pv_optimum.py:143` (fallback target threshold),
`pv_optimum.py:706` (`pv_continuation_plan`),
`pv_regulators.py:265` and `pv_regulators.py:290` (hard ceiling and import
enforcement), and `pv_surplus.py:549` (executable continuation planning).

## Phase stability, acknowledgments, and retries

Phase selection now observes the regulator's existing acknowledgment/physical
response window specifically after a phase-changing acknowledgment, tracked by
`phase_change_pending`. While that change is awaiting its physical response, a
solve selecting another phase or a soft-energy OFF may hold the current physical
minimum through the same safety checks. The guard uses the existing 20-second
`SETTLING_SECONDS` observation window and its response-completion evidence.
Each accepted corrective setpoint starts the existing response-observation
window for that setpoint. Observed response, OFF, lost confirmed state, or
expiry clears the pending-phase context, preventing an unrelated later current
change from rearming an old phase guard. Genuine hard reductions and safety
stops remain eligible.

Learned phase restrictions remain scoped to connection generation, authority
revision, and physical mode. Ordinary replanning does not repeatedly send the
known blocked transition. The existing task-scoped probe and 60-second retry
cadence reconsider the desired point from fresh measurements. A retry deadline
does not itself prove that station lockout expired. Acceptance of a different
phase, changed physical evidence, or a changed connection/authority context
updates the restriction through the existing mechanisms. Execution-only
`profile_modes` restrictions are excluded from the all-mode desired solve, so a
minimum hold cannot hide an eligible desired transition from a later probe.
Desired-policy evaluation uses a copy of the regulator: its different minimum
and import allowance cannot mutate the executable continuation's import grace
or restart state.

The adapter continues to classify only the explicit `PhaseSwitchLockout` reason
as `PHASE_SWITCH_LOCKOUT` in the phase-mode area. Generic rejection, including
restart-lockout responses, remains `busy`; unsupported operations, stale command
fences, authority loss, and hardware failures retain their existing paths. The
protocol-neutral fallback uses the existing command and confirmation contracts;
the OCPP 1.6J, 2.0.1, and 2.1 implementations are not modified.

Relevant source: `pv_regulators.py:107` and `pv_regulators.py:127`
(acknowledgment and `response_settling`), `pv_surplus.py:564`
(phase-selection guard), `control/runtime.py:231` and `control/runtime.py:257`
(restriction and probe), `control/runtime.py:301` (desired mode scope),
`pv_optimum.py:643` (desired-policy state isolation),
`control/runtime.py:472` (confirmed-point context),
`control/runtime.py:1130` (specific rejection and same-mode fallback), and
`protocols/ocpp/v21/adapter.py:338` (reason classification).

## Validation

The focused regression module is
`tests/test_pv_phase_lockout_continuation.py`. Its coverage includes:

- The incident sequence `1p/19A → 3p/7A → rejected 1p/10A → 3p/6A`, with no
  lockout-caused OFF, preserved permission/session, and rejected-phase state
  never becoming confirmed.
- All three PV profiles; the exact 50% allowance boundary; transient excess
  import followed by a bounded pause; exact-target SoC continuation and stopping
  below target.
- Unavailable and stale grid, SoC, and EV measurements; independent battery
  budget enforcement; authority, ownership, transaction, disable, and connection
  generation fences; device current limits, grid steps, and measured voltage.
- Bounded periodic probing using fresh desired operating points and recovery
  after the station accepts the transition.
- Zero measured EV power during the incident's phase response; immediate reverse
  prevention and release after fresh physical response across all three
  profiles; strict SoC stopping with unknown physical phases; conflicting
  measurements; and Surplus continuation without a discharge mapping while
  still requiring the grid channel contract.

Existing phase-lockout, command lifecycle, PV policy, physical response,
authority, connection, and session-ledger tests remain part of validation.

| Check | Result |
| --- | --- |
| New continuation regression module | 68 passed |
| Existing PV/phase-lockout regression tests | Passed in the complete suite |
| Complete Python test suite | 2040 passed in 112.98 seconds; 5 dependency deprecation warnings |
| Frontend card tests | Passed; frontend source unchanged |
| Ruff lint | Passed |
| Ruff formatting | Passed; 177 files already formatted |
| Final diff review | Passed; only four controller/runtime files, related tests, and documentation changed |

Validation commands:

```text
.venv/bin/pytest -q tests/test_pv_phase_lockout_continuation.py
.venv/bin/pytest -q --tb=short --show-capture=no
.venv/bin/ruff check .
.venv/bin/ruff format --check .
node --test tests/test_wallbox_card.cjs
git diff --check
```

Home Assistant fixture setup stalled in the sandbox; Python integration tests
completed outside it using local fixtures and simulated OCPP peers. The final
suite includes the beta.10 ledger regressions and OCPP 1.6J, 2.0.1, and 2.1
compatibility tests. No diagnostic log or pre-existing untracked file was staged.

## Limits and release restrictions

Continuation cannot override a real electrical or battery hard limit, unavailable
critical measurements, revoked permission/ownership/authority, changed command
context, or emergency/physical hardware protection. A station that supplies only
generic rejection does not provide enough evidence for phase-lockout fallback.
The local retry cadence can probe station availability but cannot infer the
station's internal expiry when no deadline is supplied. Missing or invalid
configured grid or SoC measurements cannot authorize positive continuation;
Surplus's optional battery-free configuration retains its existing lack of a
SoC policy. No hardware validation has been performed for this fix.

This report covers implementation and automated validation. No merge into
`develop`, tag, prerelease publication, Home Assistant deployment, wallbox
firmware change, or hardware charging test is authorized by this delivery.
Preparing another prerelease remains pending review of this report.
