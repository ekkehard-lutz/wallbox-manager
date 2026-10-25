# NETZ initialization must remain an unapproved draft

Correction on `fix/grid-initialization-approval-race`, based on develop
`620de00b5d0e7c13b60403a16638e70555b5bd83`. This implements existing
WBM-GRID-35 together with GRID-22/25/37 and the unified Start/Apply contract;
it does not change nominal planning, hardware authority or approved dispatch policy.

## Root cause and reproduction

The [release-commit CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/40118191999)
passed 449 Python tests with one companion-source skip and 38 frontend tests.
The [tag CI on the same commit](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/40118363598)
failed `test_capabilities_can_make_existing_draft_equivalent[minimum]`:
448 passed, one skipped, one failed; subsequent frontend steps were skipped.

Approved power was 1.4 kW and the unapproved draft was 1.5 kW. Raising
MinimumCurrent from 6 A to 7 A made that draft invalid. Initialization replaced
it with the 1.6-kW/1p technical minimum while initially preserving approval.
Reconciliation still used the old approved request, which NEAREST resolves to
1p/7 A under the new capabilities. Its subsequent equivalence callback could
then adopt the new 1.6-kW draft and advance the approval revision. The existing
Settings lock serialized these operations, but their persisted data did not
distinguish automatic initialization from a user edit. Passing tests could inspect
an intermediate state before the independently scheduled callback finished.

The new simulated-OCPP tests use events around the actual initializer,
reconciliation and durable save. They force both orders without scheduling sleeps:

1. Initialization durably completes while reconciliation is blocked, then the
   reconciliation and delayed equivalence callbacks run.
2. Initialization holds the Settings lock in its pending durable save while
   reconciliation completes, then the save and queued equivalence callbacks run.

Before the production correction, both charging cases failed: approval changed
to 1.6 kW, phase hint changed to 1p and the approval revision advanced. Both
release-OFF controls passed. This reproduces a production approval race rather
than merely an early storage assertion or a missing synchronization wait.

## Minimal correction

Only `settings.py` production code changes. A private boolean
`grid_power_initialized` records draft provenance in the existing profile document:

- Initialization persists `true` atomically with replacement power, phase hint
  and incremented draft revision. It does not alter approved values/revision,
  release, active schedule or next timing.
- The common automatic-equivalence predicate rejects initialized drafts. This
  covers queued reevaluation and timing-edit adoption, regardless of callback order.
- An explicit power edit clears the marker in the same durable transaction and
  resumes ordinary user-edit equivalence adoption. Same-valued explicit edits
  remain observable when clearing initialization provenance. Timing, approximation,
  reserve and capability changes do not clear it.
- Explicit Start/Apply clears the marker together with atomic approval. Storage
  failure leaves the old state intact. A superseded approval's existing compensating
  save restores the prior marker alongside prior approval/timing, preventing a
  later callback from accepting a failed operator action.

Absent markers in legacy stores mean ordinary existing drafts, preserving their
equivalence behavior. New markers survive reload/reconnect in the existing store;
no separate controller, queue, timer, polling loop, OCPP variable or schema-version
bump is added. The existing backend pending state keeps the card on Apply while
released, or Start while release is OFF. Explicit approval remains available.

Initialization itself never sends current/phase commands. Normal capability
reconciliation of the old approved 1.4-kW request remains unchanged: with release
ON, NEAREST can request 7 A under the new minimum. The tests distinguish this
single existing approved-request dispatch from initialization, which adds none.
With release OFF, no positive command is issued. Existing phase hints, zero-release
recovery, lockouts and current/phase transaction/generation guards are unchanged.

## Regression evidence and limits

The new regression matrix covers both forced orders with release ON/OFF,
unchanged approved revision and duration deadlines, explicit Apply/Start, genuine
equivalent user edits, timing edits and delayed callbacks, missing capabilities,
failed initialization, failed/superseded approval, reconnect, reload and phase hints.
The original capability test retains its approval expectation and now explicitly
settles initialization/reevaluation and checks the replacement draft; user_cap,
station_max and phase_modes cases retain their contracts.

Validation on 24 October 2026:

| Check | Result |
| --- | --- |
| Before-fix forced-order controls | Two charging cases failed with 1.6-kW implicit approval; two release-OFF cases passed |
| New initialization/recovery matrix | **14 passed** |
| Complete WBM Python suite | **464 passed**, 78.00 s; five existing dependency deprecation warnings |
| Complete frontend suite | **38 passed** |
| Repeated new matrix plus all four original capability cases | **10 × 18 passed**, 180 executions in fresh pytest processes |
| Full Ruff lint / formatting / JavaScript syntax | PASS / PASS / PASS |
| Python compilation | PASS |
| English/German translations, manifest/services and HACS metadata | PASS; version remains **0.4.0-beta.13** |
| Git whitespace and original untracked-file hashes | PASS |

The local complete suite includes the actual Stationary Domain/DeviceModel with
mocked hardware. GitHub CI without the companion checkout is expected to skip
that one source-dependent test. The exact correction commit and CI outcome are
reported after pushing, without rewriting this document's own source commit.

Already incorrectly approved requests written by the unfixed code have no reliable
origin history from which to reconstruct an earlier approval. This fix does not
invent or silently reverse those values; review saved requests before a separately
authorized later installation/Start. No version change or deployment occurs here.

Reference checks: historical WBM `v0.3.2-beta.18`, commit
`87963014c9af73416c438807f523a3da2a98bd22`,
`custom_components/wallbox_manager/battery.py`, retains compare-before-restore
ownership independently of this approval fix. Actual openWB/core revision
`849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
`packages/control/chargepoint/chargepoint.py` (`_is_manual_lock_inactive`,
`is_charging_possible`), distinguishes user policy permission from physical plug
state. Neither source's control loop or physical runtime is imported.

The annotated beta.13 object remains
`230ca2bb9db4e9b4521467588265f7c37e9e4c13`, pointing to the original develop
baseline. This fix is for independent review, not a merge, retag, release or
installation. Stationary and production services remain untouched.
