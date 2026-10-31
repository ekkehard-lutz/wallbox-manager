# Safe charging actions, state publication and NETZ reserve preview

Approved correction: 31 October 2026. Implementation and simulated validation
only. Independent review, merge, release, installation and hardware acceptance
are separate. No live hardware validation is claimed.

## Baseline and source evidence

Base: `origin/develop` `d5cb55b16b1e684e241e12e94326a310f303f0c1`.
Fetched remote refs before branching. `origin/main`
`84790e42932aff7de0c4e6fdc55392dcb481bba0`, develop and beta.20 have the
reviewed tree `daeae5026e41109021cbdd2d4240ada022377edb`.
Branch: `fix/charge-action-state-and-reserve-preview`.
Unrelated local investigation documents and `pv2.log` were preserved.

Inspected actual openWB/core checkout revision
`4e6b2224420fc25ab87aa7534201fcf9e250cdfa`:

- `packages/main.py`, `HandlerAlgorithm.handler10Sec()`: snapshot acquisition,
  configured regulation cadence, one algorithm invocation and changed-value
  publication. This supports retaining WBM's single clocked PV decision path;
  no openWB acquisition or control code was imported.
- `packages/control/bat_all.py`, `get_charging_power_left_diff()` and adjacent
  active battery-control methods: storage priority/SoC policy and physical
  battery actuation are distinct. These are comparison evidence, not a substitute
  for WBM's approved reserve journal or Fronius interface.

Inspected historical WBM `v0.3.2-beta.18`, peeled revision
`87963014c9af73416c438807f523a3da2a98bd22`: `pv_soc.py` and
`pv_optimum.py` (shared SoC state, remaining-day energy formula, daily latch and
300-second planning). Historical inclusive lower/medium thresholds are explicitly
superseded by the current strict boundaries. Reused no historical runtime,
freshness manager, discharge-budget controller or legacy event loop.

## Root causes and resulting behavior

1. **Delayed action display:** the eligible branch of `Grid.changed()` launched
   control without publishing. PV intentionally does not dispatch without an
   explicit clocked `pv_point`; therefore accepted release could remain invisible
   under persistent SoC STOP. `changed()` now publishes through the existing
   attribute equality check after launch, independently of electrical execution.
2. **Toggle race:** `button.press` interpreted whichever action was current when
   the backend handled it. A browser's old Start could therefore become Stop.
   The card now sends an explicit direction and expected context. Obsolete
   requests fail; a Start is never reinterpreted as Stop.
3. **Unplugged Start:** readiness reused transaction-bound electrical eligibility.
   `manager_ready()` now retains registered live Remote control prerequisites;
   `eligible()` additionally requires a transaction. PV Start also retains the
   hardware permission check. Authorization can be armed without a transaction;
   only an ordinary PV tick can subsequently request a positive point.
4. **Stale target:** the mandatory SoC STOP and other exits bypassed the final
   Grid publication although the dedicated target sensor received its narrow
   notification. The regulation block now publishes in `finally`, using the
   same exact canonical attributes and equality deduplication on every exit.
   The card's Select compatibility projection therefore follows the sensor,
   without a second planner or frontend formula.
5. **NETZ discrepancy:** the card previously showed observed reserve only.
   `BatteryReserve.plan()` now contains the existing exact Fraction/Number-step
   arithmetic; `desired()` remains the diagnostic physical-write consumer.
   `preview()` uses that same operation without mutating diagnostics, ownership
   or storage. `GridBattery` publishes a narrow preview change and the Select
   exposes `planned_grid_reserve`. The pair shows actual SoC → planned reserve;
   tooltip and accessible label separately identify observed minimum reserve.

Example: original 20%, NETZ setting 40%, SoC 65% previously displayed
`65.0% → 20.0%` until an override. It now displays `65.0% → 40.0%` before
Start, with observed 20% separately labeled. Nothing writes reserve for preview.
A valid active owned override uses its durable original. Recovery, pending,
unconfirmed/mismatched ownership and failed restoration yield unavailable preview.
After the existing coordinator relinquishes external changes, the current actual
reserve becomes the baseline. Actual readback and restoration remain intact.

## Explicit entity-service API and compatibility

Service: `wallbox_manager.charge_action`, targeting the existing connector
`button.charge_action` entity. Required fields:

| Field | Meaning |
| --- | --- |
| `action` | `start`, `stop` or `apply`, captured before asynchronous editor completion |
| `profile` | Current charging-profile Select option |
| `revision` | Current button attribute `charge_action_revision`, opaque string |
| `incarnation` | Current button attribute `runtime_incarnation` |

Example service data (replace entity and context with current HA values):

```yaml
entity_id: button.wallbox_charge_action
action: start
profile: PV_OPTIMUM
revision: "<charge_action_revision>"
incarnation: "<runtime_incarnation>"
```

Revision combines a minimal runtime action generation with existing profile,
release, draft/approval revision, OCPP generation/transaction, authority readiness,
hardware permission and power-policy settings. It is neither a security token nor
a persisted schema. The generation prevents ABA replay after Start→Stop even
when the current boolean matches an old snapshot. Browser editor Begin/End/renew
alone changes no approved/draft intent and does not invalidate this revision;
actual timing edits still advance the existing draft revision.

Start/Apply serialize across cards and the compatibility button. Validation runs
again after acquiring the settings persistence lock. Existing NETZ approval
pre/post-persistence epoch checks remain; PV Start now has corresponding checks
and durably revokes a superseded write before reporting an error. Stop bypasses
the Start/Apply queue, immediately fences pending dispatch/approval and uses the
existing release-OFF persistence path. Failed persistence cannot grant positive
intent. The existing fail-closed Stop semantics revoke in-memory electrical intent
before persistence; a failed Stop save reports an error rather than success.

The card does not optimistically change release or action caption. Service errors
stay visible in the existing warning surface; callbacks from a replaced profile,
entry or runtime cannot raise warnings in the new context. Multiple cards can
send the same old Start; the first can commit and subsequent requests are rejected
without a zero-current operator Stop. Explicit Stop remains available through the
service even while NETZ drafts make the visual action Apply; the single visual
button retains the approved two-click Apply-then-Stop workflow.

**Compatibility limitation:** generic HA `button.press` remains a state-dependent
legacy interface, with existing simultaneous-call coalescing. Sequential ambiguous
presses cannot express the caller's intended direction. Safe automations must use
the explicit service. No existing entity is deleted or renamed; no migration or
new public sensor is required. English/German service translations and the service
schema document the new API. Upgrade of served card resources follows ordinary
HA frontend cache handling; this task does not deploy or refresh a live browser.

## Normative changes and traceability

The dated amendments preserve original requirements as historical text and
explicitly list the narrow supersession. Architecture WBM-ARCH-01–12 continues
to take precedence. New groups are approved by this task, not inferred from logs.

| Requirement | Implementation and automated evidence |
| --- | --- |
| WBM-ACTION-SAFE-01–06 | Explicit entity API; `test_two_cards_stale_start_stop_apply_and_revision`, `test_pv_start_revision_cannot_replay_after_start_stop_aba`, `test_stop_supersedes_inflight_start_persistence`, `test_profile_switch_during_start_persistence_fences_old_context`; card captured-direction/two-card/service-failure tests |
| WBM-ACTION-SAFE-07/08 | `test_explicit_service_start_under_soc_stop_publishes_durable_intent`; existing generic-button Start/Apply/Stop/coalescing tests retained |
| WBM-RELEASE-PREP-01–05 | All three profiles in `test_pv_preparation_without_transaction_then_normal_cycle`; Local/offline/hardware-disabled restrictions; existing authority/recovery/reload and clocked PV tests |
| WBM-HS-UI-SYNC-01–04 | Exact 67.559→73.619 STOP replay, sensor and Select states; 300 unchanged target/Select cycles; existing passive planning, phase, invalidation/recovery and sensor identity tests; frontend 67.6→73.6 presentation |
| WBM-HS-UI-NETZ-01–06 | Pure preview arithmetic/ownership/storage assertions; four requested examples, fractional step/nonzero minimum, invalid ranges, absent inputs, external edit, pending/recovery; projection and 300-repeat dedup; EN/DE planned-versus-observed tooltip, multiple cards and Chrome layout |
| WBM-RELEASE-04 | Only transaction absence superseded for armed PV manager authorization; no authority or physical dispatch exemption |
| WBM-RELEASE-05/09; WBM-PV-RELEASE-01–05 | Explicit, current-profile intent and prompt authoritative projection clarified; computed zero remains authorized pause |
| WBM-NETZ-ACTION-01–13; WBM-TIME-01–24 | Business approval/deadline behavior preserved, service direction fenced; existing NETZ/timing/editor lease suites |
| WBM-HS-UI-05/09/10/15 | Canonical target freshness and one-decimal presentation clarified; NETZ labels/examples superseded only as identified |
| WBM-HS-UI-07 | Observed-only right-hand display superseded; actual observed reserve still read and labeled separately |
| WBM-GRID-15 | Pure shared calculation and preview added; physical charging prerequisites, journal ownership, confirmed write and restoration suites retained |
| WBM-PV-TARGET-01–06 | Existing sensor unchanged; compatibility projection notification clarified; original ID, precision, availability and change-only tests retained |

## Validation

All final checks passed on the frozen source, 31 October 2026:

| Check | Result |
| --- | --- |
| Complete Python suite | **882 passed, 0 skipped**, 5 existing dependency deprecation warnings, 181.00 seconds |
| Statement coverage | **93.58%**, 3,893 / 4,160 statements covered; 267 missing (beta.20: 93.27%) |
| New backend regression cases | **24 passed**, real HA state/persistence and simulated localhost OCPP peers |
| Complete frontend suite | **83 passed**, 0 failed (beta.20: 80) |
| Real headless Chrome | **1 passed**, both languages at 640/400/320/280 px; alignment, stacking, overflow, caption and accessibility |
| Ruff lint and format | Passed; 176 Python files formatted |
| JavaScript syntax | `node --check` passed |
| Manifest | JSON/domain valid; version remains `0.4.0-beta.20` |
| Translations | English/German/strings leaf-key parity passed, including service fields |
| Documentation | **51 local links/anchors passed** in task-modified documents |
| Whitespace | `git diff --check` passed |
| Source invariance | **10 exact Git blob comparisons passed**: core, SoC, planner, economics, point solver, control, OCPP, target sensor, settings and manifest |

Commands: `.venv/bin/pytest --cov=custom_components.wallbox_manager
--cov-report=term-missing --cov-report=json:/tmp/wbm-task-final-coverage.json -q`,
`.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`,
`node --check custom_components/wallbox_manager/www/wallbox-manager-card.js`,
`node tests/test_wallbox_card.cjs`, `node tests/test_wallbox_card_layout.cjs`,
manifest/translation/local Markdown assertions and `git diff --check`.
Localhost peers and Chrome required the environment's network/process permission;
no connection to live HA, Stationary or Fronius was used.

Recorder evidence: **300 unchanged active SoC-STOP cycles produced zero target
sensor, charging-profile Select and action-button writes**. The planner was
called exactly 300 times, with no extra regulation pass. **300 unchanged reserve
preview publications produced zero Select writes**. Pure preview repetition
changed neither reserve diagnostics nor its journal. Changed NETZ preview values
now intentionally produce one narrow Select state change, including availability;
this supersedes the historical observed-only test expectation.

Race evidence includes concurrent cards, stale Start/Stop/Apply, old profile and
runtime, ABA Start→Stop replay, power draft revisions, and Stop/profile change
during delayed Start persistence for both NETZ and PV. A deliberately blocked
rollback keeps release false and a PV tick sends no positive point. Apply arriving
at NETZ expiry is rejected without approving drafts or becoming Stop; independent
business expiry retains its established behavior.

Compared with beta.20, all 858 existing tests remain represented, with the NETZ
preview publication assertion updated to the explicitly approved new display
contract. The 24 new cases bring the total to 882. No control output difference
was introduced beyond explicit action direction, cancellation of superseded
Start and the approved transaction-independent PV authorization boundary.

## Algorithm invariance and remaining acceptance

Source comparison with beta.20 leaves `pv_core.py`, `pv_soc.py`, `pv_plan.py`,
`pv_economics.py`, `grid_point.py`, OCPP/control/Stationary interfaces, defaults
and manifest unchanged. The only PV runtime change is lifecycle publication;
SoC comparisons, FAST/BALANCE choices, optimum target formula, economic STOP,
configured cadence and physical dispatch guards remain unchanged. Battery write
and restoration branches retain their ownership prerequisites and calculations;
the diagnostic caller delegates to the extracted pure calculation. Preview never
calls the writer, store, OCPP or regulator. NETZ inputs remain optional for power.

Recommended later separately authorized hardware acceptance:

- Start PV_OPTIMUM under 42.5/95 SoC STOP and verify immediate Stop caption at 0 A;
  repeat a stale Start from another browser and confirm release stays ON.
- Pre-arm each PV profile unplugged; connect and observe the first ordinary tick,
  including SoC STOP and permitted FAST/BALANCE cases. Stop before connection.
- Observe moving canonical target during STOP and with release OFF, plus source
  invalidation/recovery and OCPP transport loss.
- NETZ 20/40/65 shows planned 40 before Start and observed 20 in tooltip. After
  actual confirmed charging, verify existing override/readback/restoration and
  external ownership changes, including reload with a durable reserve journal.
- Exercise delayed service/state updates, two browsers, profile switches and NETZ
  timing Apply/expiry using actual HA permissions and frontend resource caching.

Simulated localhost peers and Chrome are software acceptance only. No station or
Fronius source changes, live configuration, HA reload/restart, deployment, version
change, merge, release or tag is included.

## Changed files

- `custom_components/wallbox_manager/battery.py`
- `custom_components/wallbox_manager/button.py`
- `custom_components/wallbox_manager/grid.py`
- `custom_components/wallbox_manager/grid_battery.py`
- `custom_components/wallbox_manager/pv.py`
- `custom_components/wallbox_manager/select.py`
- `custom_components/wallbox_manager/services.yaml`
- `custom_components/wallbox_manager/strings.json`
- `custom_components/wallbox_manager/translations/de.json`
- `custom_components/wallbox_manager/translations/en.json`
- `custom_components/wallbox_manager/www/wallbox-manager-card.js`
- `docs/charge-action-state-reserve-preview-review.md`
- `docs/specifications/README.md`
- `docs/specifications/charge-release.md`
- `docs/specifications/grid-profile.md`
- `docs/specifications/lovelace-home-battery-display.md`
- `docs/specifications/operational-display-and-timing.md`
- `docs/specifications/pv-target-soc-sensor.md`
- `tests/test_home_battery_display.py`
- `tests/test_safe_charge_actions.py`
- `tests/test_wallbox_card.cjs`
- `tests/test_wallbox_card_layout.cjs`
