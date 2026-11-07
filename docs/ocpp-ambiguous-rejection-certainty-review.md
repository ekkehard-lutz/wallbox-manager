# OCPP rejection certainty review

This branch addresses pre-release audit finding F1 against verified `origin/develop` at `e3b9bae69daa05f3481037b56e6fba87b9913275`, with the same tree as published `v0.4.0-beta.22`. The branch is for independent review.

## Root cause

In beta.22, [`Grid.dispatch_point()`](../custom_components/wallbox_manager/grid.py#L1065) created a chained uncertainty record at the OCPP submission boundary. At beta.22 `grid.py:1174–1182`, every `Rejected` marked its record eligible for unwinding. That could expose the old positive offer as current when the scope and last reported phase still matched. A phase report can lag an operation, and a current-only change need never change the phase. Both cases made the inference unsound. [`Grid.attributes()`](../custom_components/wallbox_manager/grid.py#L606), [PV execution diagnostics](../custom_components/wallbox_manager/pv.py#L365) and [PV regulation](../custom_components/wallbox_manager/pv.py#L640) consume the same uncertainty marker.

## Stationary response contract

Reviewed the read-only Stationary checkout at `279287c3c1d8863e803144eccf773ab3f10889a5`.

| OCPP outcome | Evidence at the pinned Stationary revision | WBM treatment |
| --- | --- | --- |
| Exact `Rejected / PhaseSwitchLockout` | `domain/wallbox.py:201–227,731–742` checks phase lockout before profile hardware or desired-state mutation. `ocpp_runtime/station.py:589–596` emits the specific reason. | The submission's uncertainty can unwind. Existing PV continuation still requires a valid independent positive offer matching the physical phase and available limits. |
| Exact `Rejected / ReenableLockout` | `domain/wallbox.py:679–700,731–742` checks restart lockout before the profile can act. The current-only path rechecks it before applying the limit. `ocpp_runtime/station.py:589–596` emits the specific reason. | The submission's uncertainty can unwind. Existing scoped re-enable retry stays in place. |
| `Rejected / RestoreFailed` and other transition failures | `services/phase_switch.py:115–145,158–195` can confirm relay movement before restoring current, validating the restored operation, or attempting cleanup. `domain/wallbox.py:743–768` can report `RestoreFailed` after the switch. `ocpp_runtime/station.py:597–600` maps the failure to `Rejected`. | Keep the submission unresolved. The reason conveys neither the applied point nor the physical phase. |
| Reasonless or unknown rejection | Early refusal and later exceptions share generic `Rejected` (`ocpp_runtime/station.py:562–604`). For current-only writes, `domain/wallbox.py:424–444,684–707` can fail after attempting the register write or after applying current. | Keep the submission unresolved. No timed retry or fallback follows from the ambiguous reason. |
| Accepted or lost response | An ACK records protocol acceptance. A missing response can follow a submission. | Existing accepted-unconfirmed and communication-failure behavior applies. A scoped readback can establish the actual offer. |

The allowlist uses exact, case-sensitive Stationary reason codes. `additionalInfo` supplies timing detail for the existing lockout policy and carries no independent proof of actuation. Unsupported firmware that returns a generic rejection has an ambiguous outcome from WBM's perspective.

For the historical comparison, WBM `v0.3.2-beta.18` (`87963014c9af73416c438807f523a3da2a98bd22`), `docs/phase-lockout-protocol.md:16–37`, likewise requires an exact pre-actuation reason. The current PV continuation follows PV-LOCK-01–08. The openWB cross-check used `849340ec8d91f4e7cca08a7664cf9ec0187fb496`, `packages/control/chargepoint/chargepoint.py:126,521,557` for manual lock, phase decision and bounded current selection. Stationary source governs this wire contract. [Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py)

## Correction and public behavior

- [The response handler](../custom_components/wallbox_manager/grid.py#L1155) allows the two verified atomic reasons to mark their own submitted record removable. Unwinding walks removable records at the head and stops at the first unresolved submission. A newer readback can already have cleared the head. A newer accepted or ambiguous submission remains a barrier and makes older uncertainty records unnecessary.
- An ambiguous rejection preserves the historical observation internally. The existing `applied_current_a`, `applied_phase_count` and `applied_offered_power_w` projections stay empty while the command is unresolved. The [card](../custom_components/wallbox_manager/www/wallbox-manager-card.js#L609) renders the unavailable applied point as an em dash, with NETZ still able to show the requested point separately. PV Level 2 records the rejected command with no current confirmation or ACK for that proposal. The physically reported phase stays independent. Diagnostic levels, event types and Recorder entity sets stay as before.
- The existing [readback](../custom_components/wallbox_manager/grid.py#L1024) validates the adapter, epoch and transaction, and requires a valid composite schedule consistent with the independently reported phase. A phase observation alone cannot resolve the offer. An unavailable or contradictory read leaves the earlier observation unpromoted. The historical point remains available internally to the existing NETZ planning/recovery path. A held-point match cannot satisfy a new request while uncertainty persists.
- NETZ retains its existing exact-lockout retry. [Interim execution](../custom_components/wallbox_manager/grid.py#L1386) requires a confirmed held offer. If a newer exact lockout exposes an older unresolved command after a missing readback, the original retry stays available without using the old offer as confirmation for an interim write. PV retains its phase deadline, same-phase changes and fresh regular-tick decision.
- WBM-ARCH-01–12 and the approved NETZ, release and PV contracts still assign contactors, current/phase enforcement, CP, failure cleanup and physical safety exclusively to Stationary. The patch changes outcome interpretation in the existing command path. It adds no safety controller, timers, readback polling or hardware procedure. Ordinary unresolved PV ticks can use the existing confirmation path. Explicit Stop and required SoC/economic 0 A retain their existing paths and fences. [Architecture](specifications/architecture-responsibility.md) · [NETZ](specifications/grid-profile.md) · [Charge release](specifications/charge-release.md) · [PV lockout](specifications/pv-operating-point-response.md) · [Diagnostics](specifications/pv-decision-diagnostics.md)

## Regression evidence

| Evidence | Coverage |
| --- | --- |
| [New NETZ and PV certainty tests](../tests/test_ocpp_rejection_certainty.py) | Reasonless, unknown, partial phase change, current restoration failure and current-only failures. Validate suppressed applied values, unchanged rejection/ACK semantics, independent phase observations, missing or contradictory readback and later valid readback. |
| New ordering and lifecycle cases | Late exact and ambiguous responses after newer readback, overlapping wire-result processing, transaction replacement and both profile-change directions. A later exact lockout leaves an earlier unresolved command intact. |
| New Stop and response cases | Normal PV tick with no forced zero, explicit Stop, permission loss with Stop, Local authority loss, SoC and economic Stop, accepted-unconfirmed and response loss in both profiles. |
| [Existing PV lockout suite](../tests/test_pv_phase_lockout.py) and [combined beta.22 regressions](../tests/test_beta22_profile_integration.py) | Independently confirmed exact-lockout continuation, same-phase adjustments, fresh post-deadline decisions, scoped cancellation, late reads, and schedule/draft retirement across profiles. |
| [Existing NETZ suites](../tests/test_grid_profile.py) and [companion contract](../tests/test_stationary_control_contract.py) | Lockout retry and interim behavior, no duplicate writes after confirmation, response timeouts, mocked Stationary Domain and DeviceModel authority, permission and manager Stop. |

Three NETZ test cases encoded the old assumption that a bare `Rejected` or `Busy` meant the proposal was atomic. The updated cases retain the atomic assertions with the verified `ReenableLockout` reason and separately exercise `Busy` as ambiguous. The retry case makes the actual retry reject so it can check that there is no second interim write while the earlier accepted command is unresolved. Existing beta.22 PV and cross-profile test files are byte-identical.

## Validation

The local environment used Python 3.14.7, HA 2026.8.0, Node 24.22.2 and the
pinned read-only Stationary checkout. Python ran as `python -m pytest -q` in
the activated environment with coverage enabled through `PYTEST_ADDOPTS`.
The five warnings are the existing dependency deprecations. The mock-backed
companion test ran locally. CI without the companion checkout skips that test.

| Gate | Fix branch | Published beta.22 baseline |
| --- | --- | --- |
| Full Python suite | 987 passed, five warnings, 247.82 s | 954 passed, five warnings, 222.31 s |
| Statement coverage | 93.83%, 4,153 of 4,426, 273 missed | 93.83%, 4,153 of 4,426, 273 missed |
| `grid.py` coverage | 96.88%, 745 of 769 | 96.88%, 745 of 769 |
| Focused rejection, NETZ, PV and cross-profile run | 150 passed, five warnings | Different test selection |
| JavaScript syntax and frontend regression | Syntax checks passed, 95 tests passed | 95 tests passed |
| Headless Chrome | One test passed, 96 layout and 48 action/theme combinations | Same |
| Ruff lint/format, translation consistency, manifest/HACS, review links and diff whitespace | Passed, 159 translation keys and all three service field sets matched | Release gates passed |

The new reasonless PV rejection case fails against an isolated beta.22 source
archive at the expected assertion because `point_uncertain` is cleared.
Byte comparison against the tag verified that 216 of 218 existing tracked
files, including 44 of 45 integration files, are identical. This includes the
public schemas, frontend, translations, dependency pins, manifest version,
settings, migrations and the reviewed beta.22 PV/NETZ integration suites.

## Changed files and acceptance boundary

- `custom_components/wallbox_manager/grid.py` contains the focused runtime change.
- `tests/test_grid_profile.py` separates exact lockout and ambiguous NETZ fixtures while retaining the confirmation and ordering assertions.
- `tests/test_ocpp_rejection_certainty.py` contains new simulated coverage.
- `docs/ocpp-ambiguous-rejection-certainty-review.md` is the review evidence.

Operator-controlled physical acceptance remains separate. With explicit operator authorization, correlate the wire result, separately observed phase/current and the current-generation readback for an exact lockout and an ambiguous or partial failure. Confirm that a real Stop still supersedes uncertainty and that late observations cannot cross an authority or transaction change. Use station logs or an approved fault-injection procedure for restoration failures. The fake peers establish WBM interpretation of those outcomes. They cannot establish the installed station's physical transition, cleanup or recovery behavior.
