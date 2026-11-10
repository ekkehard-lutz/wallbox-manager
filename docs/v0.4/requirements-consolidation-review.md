# v0.4 requirements consolidation review

## Exact baselines and validation

Phase A merged the independently reviewed Batch 4 tip
`bfe579315ec264f8f33dd665c68e85e8fa8106e1` into `develop` with first parent
`26877615c8eb4d912cf13840bfe2f3823de72b16`. Merge
`c14f671263c3399d7e5e65147b1137ce0f78f490` has a tree identical to the
reviewed tip and is the exact Phase B implementation baseline.
[Reviewed tip CI, success](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42677785166) ·
[Exact merge CI, success](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42881643377)

Phase A local validation passed 1,162 Python tests at 95% statement coverage
(4,393 statements, 217 missed), with five dependency deprecation warnings.
Ruff lint and format, JavaScript syntax, 95 card regressions, one headless
Chrome fixture, manifest/HACS checks, 152 localization keys per file, 197
current-document links and anchors, and Git whitespace checks passed. The
broader historical scan found a reference to the removed importer, addressed
below. Remote `main`, tags and release inventory matched the pre-merge
snapshot. Manifest, dependencies and HACS metadata are identical to the
pre-merge baseline. Beta.22 remains the published prerelease.

Phase B is on `docs/v040-requirements-consolidation` for independent review.
Its diff is Markdown only. Python, frontend, tests, entity and service schemas,
configuration, translations, storage formats, manifests and metadata are
unchanged from the exact baseline. No live installation or hardware supplied
validation evidence for either phase.

## Sources reviewed

- [All specification files](../specifications/), the root
  [README](../../README.md) and [AGENTS.md](../../AGENTS.md). Current entity,
  OptionsFlow, service and frontend contracts were checked against their
  definitions and the regressions below. The normal workflow is
  [tests.yml](../../.github/workflows/tests.yml).
- [Architecture/rebuild](passive-rebuild-report.md),
  [compatibility inventory](compatibility-inventory.md),
  [delta compatibility](phase2a-delta-compatibility.md),
  [observation semantics](no-expiry-observations-report.md),
  [Recorder and availability](recorder-availability-report.md),
  [reconnect/runtime audit at 4e54c2d](https://github.com/ekkehard-lutz/wallbox-manager/blob/4e54c2dcbdfa06fd2a48df907d3341db14e1edc3/docs/v0.4/reconnect-runtime-audit.md),
  [F1 rejection certainty](../ocpp-ambiguous-rejection-certainty-review.md),
  [F3 economic recovery](../pv-economic-journal-recovery-review.md),
  [F5 fault intent](../critical-fault-intent-review.md),
  [R1–R3 corrections](runtime-blockers-r1-r3-review.md) and
  [transaction rollover](pv-economic-transaction-rollover-review.md).
- Pinned cleanup evidence from the actual Git objects
  [inventory at 863a6ae](https://github.com/ekkehard-lutz/wallbox-manager/blob/863a6aece4b80e09d2780212eca7cf9d68876743/docs/v0.4/code-parameter-cleanup-inventory.md),
  [Batch 1/2 at e02515d](https://github.com/ekkehard-lutz/wallbox-manager/blob/e02515d60ef85056a42ee44c371636194a5a762e/docs/v0.4/inactive-controls-cleanup-review.md),
  [Batch 3 at fc60fd9](https://github.com/ekkehard-lutz/wallbox-manager/blob/fc60fd958c523c2cd1fcf1cc79f87e4773bae90e/docs/v0.4/entry-registry-cleanup-review.md),
  [Batch 4 at bfe5793](https://github.com/ekkehard-lutz/wallbox-manager/blob/bfe579315ec264f8f33dd665c68e85e8fa8106e1/docs/v0.4/journal-import-cleanup-review.md).
- WBM `v0.3.2-beta.18` at `87963014c9af73416c438807f523a3da2a98bd22`,
  `pv_economy_storage.py` and `session_storage.py`, for historical persistence
  boundaries. The old ledger and economic formats are superseded.
- Actual openWB source at `849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
  `packages/control/chargepoint/chargepoint.py` lines 154–188. The local
  charging eligibility checks and unplug-driven OCPP stop corroborate the
  station-owned boundary as a comparison. The Stationary interface is verified
  through the local source fixture at `279287c3c1d8863e803144eccf773ab3f10889a5`,
  using `domain/wallbox.py` and `ocpp_runtime/device_model.py` with simulated
  hardware. [Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py#L154-L188)
- Published beta.22 [notes](release-notes-beta.22.md) and
  [validation](release-validation-beta.22.md) establish release scope only.
  External PDFs and historical hardware logs referenced in the repository
  were outside the evidence available to this review.

## Hierarchy and editorial disposition

The [current baseline](../specifications/v0.4-current-requirements.md) gives
product scope, navigation and implemented status. Detailed specifications own
requirement text and acceptance criteria. WBM-ARCH-01–12 have precedence, then
later explicit decisions within scope. Decision history and immutable Git
objects retain provenance. Implementation reports, including obsolete
migration/runtime guidance, are historical evidence for their named commits.
PV V3 remains DRAFT overall. Its explicit individual approvals apply.
Automated tests establish TESTED status independently of approval.

| Earlier presentation | Current presentation and supporting decision |
| --- | --- |
| Passive-only WBM, later active foundation still without profiles | V04-03 and the passive limitations in V04-06/07 are marked SUPERSEDED. V04-01/02/04/05/08/09/11/12 retain their applicable scope. Active profile, authority and release requirements use the 21–25 October decisions. |
| Authority-gated station hardware switch and WBM-owned wake-up setting | WBM-CTRL-03/05/08/12 refer to the supported scope and generation fences. WBM-RELEASE-06/14 and the 24 October amendment to WBM-ARCH-02 give independent hardware enable and Stationary-local wake-up. The historical AUTO-WAKE and WBM-WAKE IDs and wire description remain discoverable. |
| NETZ observed-voltage planning, separate SET and next-start-only timing | WBM-GRID-31–40 specify rounded nominal 230 V planning. WBM-GRID-22–30 preserve durable draft/approval rules with superseded SET and migration portions called out. WBM-NETZ-ACTION, WBM-TIME and WBM-ACTION-HW replace action, schedule and disabled-hardware rules. WBM-GRID-07 retains retirement of percentage switching. |
| PV framed as future, with old D, extra smoothing, TTL/proof or old SoC/restart descriptions | The approved 25 October PV-SOC, PV-FAST, PV-BAL and PV-ECO decisions govern the implemented shared path. WBM-ARCH supersedes old safety/freshness clauses. PV-LOCK replaces the automatic PV phase fallback in its stated scope. Overall V3 status is unchanged. |
| New economic scope described as waiting for a matching Ended | PV-ECO-07's explicit 9 November rollover decision governs. Same-ID retention, identity checks and damaged-history inhibition survive. The R1–R3 report retains its then-current finding. |
| Observed-only NETZ reserve, redundant display caption and sparse-only PV logs | WBM-HS-UI-SYNC/NETZ, WBM-HS-LAYOUT/POWER and action color decisions govern presentation. PV-DIAG-01–14 include the bounded unchanged active sample, pause context and execution trace. WBM-PV-TARGET retains canonical target identity. |
| Beta.17-era migration, historical RestoreEntity adoption, subentry reparenting, retired controls and historical storage imports | The current surface reflects Batches 1–4 and V04-04/05. The documents distinguish observed runtime behavior and cleanup scope from formal approval of any new recovery policy. Schema-2 session recovery and S04 are described as implemented. |
| Index led with dated authorization and release announcements | Current scope and status now lead. The [decision history](../specifications/decision-history.md) links later decisions, published evidence and the pinned pre-consolidation index. |

The detailed specs retain WBM-ARCH, V04, WBM-CTRL, WBM-RELEASE,
WBM-NETZ-ACTION, WBM-ACTION-SAFE/HW, WBM-PV-RELEASE, WBM-GRID, WBM-TIME,
WBM-DISP, WBM-MSG, WBM-HS, WBM-PV-TARGET, WBM-LOG-NETZ, PV-ARCH, PV-SOC,
PV-PLAN, PV-FAST, PV-BAL, PV-PHASE, PV-OCPP, PV-ECO, PV-STEP, PV-RESP,
PV-TUNE, PV-LOCK, PV-DIAG, PV-QA and PV-PROC identifiers. Historical IDs
and original wording remain traceable through the supporting specifications,
supersession references and the Phase A Git tree.

## Implementation and automated evidence map

| Family and detailed authority | Approval / current evidence | Source and regression coverage | Acceptance boundary |
| --- | --- | --- | --- |
| [WBM-ARCH](../specifications/architecture-responsibility.md), [V04](../specifications/passive-v0.4.md), [PV-OCPP](../specifications/pv-regulation.md#6-technical-minimum-phase-restrictions-and-ocpp) | APPROVED in surviving scope, IMPLEMENTED and TESTED for canonical observations, lifecycle and supported bootstrap | [ocpp.py](../../custom_components/wallbox_manager/ocpp.py), [store.py](../../custom_components/wallbox_manager/store.py), [meter.py](../../custom_components/wallbox_manager/meter.py), [protocol](../../tests/test_protocol.py), [observations](../../tests/test_observations.py), [availability](../../tests/test_passive_availability.py), [delta](../../tests/test_delta_compatibility.py) | Full physical station baseline and trusted-LAN commissioning OPEN. |
| [WBM-CTRL](../specifications/active-ocpp-control.md), [WBM-RELEASE and explicit actions](../specifications/charge-release.md) | APPROVED, IMPLEMENTED and TESTED for takeover, independent permission, release and scoped command outcomes | [control.py](../../custom_components/wallbox_manager/control.py), [grid.py](../../custom_components/wallbox_manager/grid.py), [active control](../../tests/test_active_control.py), [safe actions](../../tests/test_safe_charge_actions.py), [release recovery](../../tests/test_release_recovery.py), [F1](../../tests/test_ocpp_rejection_certainty.py), [F5](../../tests/test_critical_fault_intent.py) | Real permission/takeover and applied point evidence OPEN. |
| [WBM-GRID and WBM-LOG-NETZ](../specifications/grid-profile.md), [WBM-TIME](../specifications/operational-display-and-timing.md) | APPROVED, IMPLEMENTED and TESTED for nominal planning, schedule/editor and reserve policy | [grid_point.py](../../custom_components/wallbox_manager/grid_point.py), [settings.py](../../custom_components/wallbox_manager/settings.py), [grid_battery.py](../../custom_components/wallbox_manager/grid_battery.py), [nominal steps](../../tests/test_grid_nominal_steps.py), [NETZ lifecycle](../../tests/test_netz_lifecycle.py), [lease](../../tests/test_timing_editor_lease.py), [R1](../../tests/test_netz_resume.py), [reserve recovery](../../tests/test_grid_battery_restart.py) | Real Start/Apply/Stop, phase/current, reconnect and reserve restoration OPEN. |
| [PV-ARCH/SOC/PLAN/FAST/BAL](../specifications/pv-regulation.md), [PV-STEP/RESP/TUNE/LOCK](../specifications/pv-operating-point-response.md) | Individually approved clauses and agreed directions, DRAFT overall. IMPLEMENTED and TESTED shared operating path and canonical planner. | [pv.py](../../custom_components/wallbox_manager/pv.py), [pv_soc.py](../../custom_components/wallbox_manager/pv_soc.py), [pv_plan.py](../../custom_components/wallbox_manager/pv_plan.py), [pv_core.py](../../custom_components/wallbox_manager/pv_core.py), [core](../../tests/test_pv_core.py), [planning](../../tests/test_pv_planning_release.py), [runtime](../../tests/test_pv_runtime.py), [phase restrictions](../../tests/test_pv_phase_lockout.py) | Actual battery/grid/EV response and FAST stability OPEN under PV-BAL-05 and PV-QA-03. |
| [PV-ECO](../specifications/pv-regulation.md#7-unified-economic-50-wh-grid-import-policy) | Approved private optimization and rollover, IMPLEMENTED and TESTED for prospective intervals, strict reset, persistence and STOP recovery | [pv_economics.py](../../custom_components/wallbox_manager/pv_economics.py), [pv_journal.py](../../custom_components/wallbox_manager/pv_journal.py), [F3](../../tests/test_pv_journal_recovery.py), [R3](../../tests/test_pv_permission_intervals.py), [rollover](../../tests/test_pv_transaction_rollover.py), [checkpoint](../../tests/test_pv_review_regressions.py) | Real import timing, STOP/restart and changing Stationary transaction scope OPEN. |
| Session identity and current storage under [V04](../specifications/passive-v0.4.md) and [Batch 4](journal-import-cleanup-review.md) | IMPLEMENTED and TESTED current schema-2 replay and reconfirmation. Recovery policy concern OPEN. | [session.py](../../custom_components/wallbox_manager/session.py), [entry lifecycle](../../custom_components/wallbox_manager/__init__.py), [boundaries and replay](../../tests/test_session_boundaries.py), [R2 teardown](../../tests/test_runtime_teardown.py) | Real session rollover, corrupt-history audit and long-term history growth OPEN. |
| HA configuration and [V04 identities](../specifications/passive-v0.4.md) | APPROVED entry/identity boundaries, IMPLEMENTED and TESTED current configuration and registry contract | [binding.py](../../custom_components/wallbox_manager/binding.py), [config_flow.py](../../custom_components/wallbox_manager/config_flow.py), [number.py](../../custom_components/wallbox_manager/number.py), [services](../../custom_components/wallbox_manager/services.yaml), [current entry](../../tests/test_current_entry.py), [identity](../../tests/test_identity_migration.py) | Supported setup and reload on the intended installation OPEN. |
| [WBM-HS](../specifications/lovelace-home-battery-display.md), [WBM-DISP/MSG](../specifications/operational-display-and-timing.md), [WBM-PV-TARGET](../specifications/pv-target-soc-sensor.md), [PV-DIAG](../specifications/pv-decision-diagnostics.md) | APPROVED, IMPLEMENTED and TESTED current UI, publication and bounded logging scope | [sensor.py](../../custom_components/wallbox_manager/sensor.py), [card](../../custom_components/wallbox_manager/www/wallbox-manager-card.js), [card regression](../../tests/test_wallbox_card.cjs), [browser fixture](../../tests/test_wallbox_card_layout.cjs), [target](../../tests/test_pv_target_sensor.py), [pause logs](../../tests/test_pv_pause_diagnostics.py), [benchmark](../../tests/test_passive_benchmark.py) | Field UI, source timing and sustained HA/Recorder impact OPEN. |

## Open session journal recovery

**Current behavior.** `session.load()` accepts a schema-2 document
with its session and persisted closed IDs, resets the live generation, and
rejects unsupported payload schema. If HA `Store.async_load()` returns
`None`, setup proceeds with the initial empty replay set. The installed HA
2026.8.0 implementation in `homeassistant/helpers/storage.py`,
`Store._async_load_data()`, can quarantine malformed JSON as
`.corrupt.<timestamp>`, create a repair issue and return `None`. The WBM
session loader has no file/quarantine check like `pv_journal.load_journal()`.
Some other I/O errors may propagate instead. The ambiguity is specifically
the successful `None` return.

**Consequence.** If recovered history is empty after a formerly present file
is lost or quarantined, WBM can lose the durable closed-ID rejection fence.
A delayed event carrying one of those IDs may then be accepted if it passes
the remaining current-generation, scope, time and sequence checks. A later
save could persist the new empty/incomplete history.

**Evidence and missing cases.** Batch 4 tests reject unsupported payload
schemas, verify both schema-2 snapshot variants, require reconfirmation for
live attribution and cover delayed/duplicate replay across repeated restarts
with valid storage. They do not establish end-to-end behavior under corrupt,
quarantined, unreadable or lost session storage on HA restart.
[Session boundary tests](../../tests/test_session_boundaries.py) ·
[Contrast with PV recovery cases](../../tests/test_pv_journal_recovery.py)

**OPEN before stable release.** Independently audit the HA Store load paths,
simulate missing versus quarantined/lost history with realistic replay, and
seek an operator decision on the recovery policy and acceptance criteria.
The full closed-ID set remains the implemented replay fence. Long-term
growth is a measurement question, without an approved pruning rule.
[R4 history](https://github.com/ekkehard-lutz/wallbox-manager/blob/4e54c2dcbdfa06fd2a48df907d3341db14e1edc3/docs/v0.4/reconnect-runtime-audit.md#r4--p2--terminal-transaction-ids-accumulate-across-reconnect-and-restart)

## Other open decisions and acceptance

- **PV-TUNE-09:** The active-budget edit behavior is implemented. Its
  earlier final-review qualification remains OPEN as a policy decision.
  **PV-TUNE-10:** The original removal wording included old stored keys.
  Current settings preserve unrelated inert keys. Any requirement to delete
  those keys needs a separate decision on the scope of historical storage
  cleanup. This editorial reconciliation documents the difference.
- **PV-OCPP-06 / V04-09:** WBM initiates supported synchronization and marks
  unavailable fields pending. Complete initial and reconnect observations
  depend on actual Stationary reporting, including unchanged measurements.
  Full physical bootstrap evidence is still missing.
- **PV-QA-03 / PV-BAL-05:** Quantitative tolerances and final FAST/BALANCE
  acceptance remain OPEN. The later PV phase continuation decision accepts
  battery-limited FAST oscillation. It does not establish installation
  performance. Any further FAST optimization requires its own evidence and
  decision. [Existing FAST investigation](../pv-fast-power-diagnostics.md)

| Outstanding physical acceptance | Evidence to capture |
| --- | --- |
| Stationary OCPP and network | Real bootstrap/device model, Local/Remote takeover and hardware permission/readback, reconnect and unchanged initial telemetry, LAN/VPN reachability and absence of public exposure. |
| NETZ and actual EV | Start/Apply/Stop, delay/duration and reconnection, current/phase/lockouts, confirmed offer versus actual response, same/new transaction behavior, reserve write/readback/recovery after reload and external edits. |
| PV and sources | FAST/BALANCE and battery-free SURPLUS as configured, real economic STOP/restart, same-ID persistence and new-ID rollover, measured EV uptake, Fronius/BYD source signs, units, event timing and forecast behavior. |
| HA runtime and Recorder | Longer alternating enabled/disabled CPU measurements under V04-12, Recorder and state-publication behavior through real steady operation and reconnect/reload. |

Before RC, independently review the documentation and recovery concern, obtain
decisions on the open policy clauses, and schedule authorized installation
acceptance against the exact candidate with separate physical evidence.

## Documentation changes and checks

Added this review and the current baseline. Updated the root README,
specification index, architecture, active/passive lifecycle, charge release,
NETZ, timing, Lovelace, PV V3, operating-point, target and diagnostic specs,
wake-up scope and decision history. Updated the diagnostics guide and
updated frontend registration guidance. Added historical notices in
`docs/architecture.md`, `docs/grid-profile.md`, `docs/metering-runtime-state.md`
and `docs/session-tracking.md`. Corrected historical source pointers in
`docs/beta9-measurement-synchronization-investigation.md`,
`docs/pv-economic-journal-recovery-review.md` and
`docs/pv7-fast-economic-corrections.md`. The README
keeps a practical card/connection example and distinguishes published beta.22
from development. Published beta release notes, validation reports and
historical results retain their original text and scope.

Phase B local validation passed 1,162 Python tests at 95% statement coverage
with the companion Stationary source fixture and five dependency warnings.
Ruff lint and formatting, JavaScript syntax, all 95 frontend regressions,
one headless Chrome fixture, manifest/HACS checks, localization parity
(152 keys per file) and Git whitespace checks passed. The repository-wide
relative-link and anchor scan passed (533 links across 126 Markdown files). Historical developer-local references
are retained as paths, and the old F3 importer link now points at its
actual source revision. The scoped diff contains only Markdown files.
