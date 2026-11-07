# NETZ timing, readiness and diagnostics review

## Baseline and Recorder evidence

The feature starts from `origin/develop` at
`824a60004b98cd72eec9bae3020101ef697149d9`. The annotated
`v0.4.0-beta.21` tag resolves to
`a1c71bb8758733b886f8e87c3d1738749af7c429`, the verified `origin/main`.

On 7 November 2026, the Wallbox01 Recorder history supplied by the operator
showed the following local times in Europe/Vienna.

| Event | Local time | Observed result |
| --- | --- | --- |
| Select NETZ | 11:10:47 | Manager release OFF, hardware disabled |
| Start 1 | 11:11:28 | Planned 11:12:28–11:14:28 |
| Start 2 | 11:17:31 | Planned 11:19:31–11:22:31 |
| Start 3 | 11:24:54 | Planned 11:26:54–11:29:54 |
| Select PV_OPTIMUM | 11:35:21 | Hardware still disabled |
| Hardware enabled | 11:36:46 | `ChargingEnabled=true` |
| PV_OPTIMUM Start accepted | 11:39:54 | Manager release ON |
| PV positive offer confirmed | 11:39:59 | 6 A requested and confirmed |

The three NETZ windows reached their scheduled start and retired release at
expiry. Remote authority and the OCPP execution-ready attribute were present.
`ChargingEnabled=false` explains why requested and confirmed current remained
at 0 A. The reported transitions establish that business timing worked.

## Before and after

| Area | Beta.21 | Feature |
| --- | --- | --- |
| Action readiness | PV Start checked hardware. NETZ could arm while the hardware denied positive execution. | Every Start/Apply requires observed hardware permission. Existing NETZ and PV profile checks still apply. |
| Disabled hardware with active NETZ drafts | The single control could present Apply while release was revocable. | The control presents Stop. Revocation preserves pending edits and the separate hardware permission. |
| Timing retirement | An ended schedule left approved and matching draft times reusable. The card hid some matching values while idle. | Backend retirement clears consumed times. Any independently pending field remains a draft. With both times unset, the next Start is immediate and unlimited. |
| Timing edit | Focus replaced both countdown displays with configured times and acquired a lease. | Focus leaves both displays and backend state alone. A meaningful edit captures the visible field and begins the lease. Each unedited field continues its projection. |
| Button styling | A fixed theme-primary background served all actions. | Backend Start/Stop/Apply use green/red/blue. Disabled uses grey and native disabled semantics. |
| Logs | There was no dedicated sparse NETZ history. | WBMGR emits bounded NETZ action, scheduling, eligibility and execution transitions. |

## Authorization and persistence

`Grid.action_state()`, the explicit connector service and the legacy button
share the hardware gate. The explicit service keeps action direction, profile,
runtime and opaque revision fences. A permission transition away from true
invalidates a captured positive action, including when permission returns
before storage completes. Active Stop bypasses positive readiness and can
supersede an outstanding Apply. It retires the manager schedule and follows
the existing transaction-scoped zero path. An accepted NETZ business schedule
can still wait for transport, Remote authority or a transaction. PV Start
continues to allow a preparatory release without a vehicle when its existing
readiness checks pass.

The existing store contains the selected numeric draft, approved values, and
`grid_request` with absolute start and optional end. Timing values in an
approved running schedule belong to that run. The shared retirement path
clears matching consumed fields and their approval. It retains independent
drafts, including edits that happen to match the configured numeric value
but carry a pending countdown adjustment. Power and phase draft values are
separate. Retirement invalidates electrical intent synchronously and persists
a single release/schedule/timing snapshot under the Settings lock. Approval
and draft revisions change when consumed timing is retired. Natural expiry
allows an already captured editor update to finish as a draft until a new
approval changes the timing revision.

Storage verification precedes publication of a candidate approval. If the
permission, schedule or runtime fence changes during storage, Settings keeps
the current runtime state and rewrites the superseded candidate. An in-flight
normal draft write crossing retirement saves its draft on the retired snapshot.
An in-flight rollback crossing retirement retains the pending draft. Runtime
close drains the Settings lock before a replacement runtime reads the store.
Reload always starts with release OFF and retires timing for a saved run.
For old beta.21 idle records with a nonzero approval revision and no new
timing metadata, an approved matching time is treated as consumed because the
old store does not distinguish a completed run's matching values. A differing
draft and pre-approval migrated values are retained.

A failed retirement write leaves runtime release revoked. Reload reads any
older persisted schedule as retired. If both candidate storage and its
corrective write cannot be reconciled because storage becomes unavailable,
the error remains visible and recovery still starts disarmed. A durable
candidate can survive that outage, so an indistinguishable matching time
may be retired on reload. After a successful later store write, the current
safe snapshot is durable.

## Editor state and deadline conversion

1. **Projection.** The card derives remaining time from backend timestamps.
   Focus alone performs no service call and keeps the displayed fields.
2. **First edit.** A native spinner, keyboard input or Clear changes one field.
   The card captures the previous rendered value, preserves the other component
   subject to carry/borrow, and serializes Begin and Update against the observed
   timing revision and runtime. Per-field draft flags tell both cards which
   inputs to project and which to show as drafts.
3. **Editing.** The card renews an active owned lease. Direct HA edits or a
   newer card revision supersede stale acknowledgements. Apply retains its
   explicit direction while any editor End completes. A hardware-disabled
   Stop can bypass a pending editor service.
4. **Completion.** End or lease abandonment starts the 15-second window.
   Timeout rolls back pending time drafts only while the old schedule is
   alive. Old-window expiry wins, removes the lease and retains edited values
   for an intentional later Start.

For a running timed duration, the backend adds the difference between the
entered and displayed whole minutes to the old absolute end. It preserves
the remainder under one minute. For example, with 01:25 displayed, pressing
minute UP adds 60 seconds to the old deadline. Spending another 10 seconds
before Apply leaves the same adjusted deadline and 10 fewer seconds in the
window. A further edit adjusts the pending endpoint. Clear removes the end.
Explicit zero ends at planned start. Entering a duration from an unlimited
countdown anchors its end to the backend edit time or planned start,
whichever is later. Direct HA Text writes retain total-duration semantics
from the planned start. Delay changes rearm relative to Apply. If duration is
untouched, the previous exact window length follows the new planned start.
The backend stores the exact schedule separately from the displayed draft
and reports its total length as the approved duration. A direct Text write
of the same number as the last countdown edit can still change the total.

Button styles use `--success-color`, `--error-color` and `--info-color` where
automatic foreground contrast is available. Older engines use high-contrast
action defaults. Themes can supply `--wbm-action-{start,stop,apply,disabled}-background`
and corresponding `-foreground` overrides.

## Diagnostics

Each `WBMGR subsystem=netz` line carries a sorted JSON object with the
station/EVSE/connector, runtime and observation generation, event and reason,
profile, release, hardware permission, authority and transaction availability.
Schedule timestamps and revision appear when applicable. Execution records can
add the requested/confirmed amps, electrical status and dispatch epoch.
Profile transitions also include the previous profile. Per-event deduplication
and phase/deferral transition tracking use bounded runtime state.

| Level | Events |
| --- | --- |
| 1 | Rejected action, hardware execution block, positive rejection and communication or unsupported outcome |
| 2 | Profile change, Start/Apply acceptance, arm/adjust, delay elapsed, active window, permission change, readiness/deferral, positive request/acceptance/confirmation, Stop, expiry, timing retirement |
| 3 | Includes the Level 2 history with the integration's existing opt-in diagnostics |

Logs use existing action, observation, deadline and command callbacks. One
browser countdown tick produces no HA write or diagnostic.

## Normative traceability

[WBM-ARCH-01–12](specifications/architecture-responsibility.md) retain precedence.
The amendments supersede reusable timing and focus-driven editing, and give
hardware-disabled Stop priority over Apply-then-Stop.

| Requirements | Regression evidence |
| --- | --- |
| [WBM-ACTION-HW-01–04](specifications/charge-release.md#approved-hardware-readiness-amendment--7-november-2026) | `test_netz_lifecycle.py` tests false/unknown across all profiles, explicit and legacy service entry points, permission loss in storage, active Stop, re-enable and runtime close. `test_safe_charge_actions.py` covers direction and PV preparation without a transaction. |
| [WBM-TIME-25–28 and WBM-TIME-UI-01–02](specifications/operational-display-and-timing.md#approved-one-shot-timing-and-editor-amendment--7-november-2026) | `test_netz_lifecycle.py` tests consecutive starts, Stop, expiry, profile, authority, reload, delayed editor storage and projected deadlines with elapsed edit time. `test_live_netz_timing.py` and `test_timing_editor_lease.py` test stale callbacks, old expiry, direct HA edits, rollback, lease recovery and failures. `test_wallbox_card.cjs` tests focus, refresh, first change, clear, carry/borrow and multi-card races. |
| [WBM-LOG-NETZ-01–04](specifications/grid-profile.md#approved-netz-diagnostics-amendment--7-november-2026) | `test_november_recorder_sequence_and_bounded_diagnostics` verifies three disabled attempts, delayed positive execution, hardware loss during the window, expiry, event order, 300 unchanged observations and Levels 0–2. |
| [WBM-UI-ACTION-COLOR-01–02](specifications/lovelace-home-battery-display.md#approved-charge-action-colors--7-november-2026) | `test_wallbox_card_layout.cjs` checks real Chrome geometry, enabled/disabled action colors, theme overrides, contrast, captions and focus across profiles. |

## Source cross-check and invariance

- WBM `v0.3.2-beta.18`, commit
  `87963014c9af73416c438807f523a3da2a98bd22`,
  `custom_components/wallbox_manager/grid_timing.py`, `grid_arm`,
  `grid_consume` and `grid_phase`. The precedent was clearing consumed
  timing and executing via separate absolute deadlines. The current
  revisioned approvals and business scheduler remain in use.
- Actual openWB/core at
  `849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
  `packages/control/chargepoint/chargepoint.py`. Inspected
  `_is_manual_lock_inactive`, `_is_ev_plugged`, `is_charging_possible`
  and their use by `update_ev` and `update`. The source distinguishes
  manual lock and plug state checks before proceeding with chargepoint
  control. WBM uses observed hardware permission for action readiness
  while keeping business scheduling and Stationary electrical ownership
  distinct. [Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py)

## Validation

Results are recorded against the beta.21 local baseline of 895 Python tests,
93.51% statement coverage, 92 frontend tests and one Chrome test with 96
layout combinations.

| Check | Candidate result |
| --- | --- |
| Complete Python suite | **925 passed**, five existing dependency warnings, 235.94 s |
| Statement coverage | **93.68%** (4,079/4,354 covered, 275 missing), compared with 93.51% at beta.21 |
| Frontend suite | **95 passed** |
| Real headless Chrome | **1 passed** with 96 layout and 48 action/theme combinations |
| Static checks | Ruff lint and format, production and test JavaScript syntax, whitespace |
| Manifest and HACS | Byte-identical to beta.21 with valid manifest JSON and dependency pins |
| Translations | 159 matching leaf keys in strings/en/de, with timing service field parity |
| Specification and review links | 61 local links and anchors across six Markdown files |
| Protected source | 37 integration files exactly match beta.21 outside the eight-file feature allowlist. This includes `pv.py`, `pv_core.py`, `pv_plan.py`, `pv_soc.py`, `pv_economics.py`, `grid_point.py`, `grid_battery.py`, `battery.py`, `ocpp.py`, `control.py`, `store.py` and `sensor.py`. |

## Later physical acceptance

Automated HA/OCPP simulation and browser checks do not establish station,
vehicle or inverter behavior. In a separately authorized acceptance session,
repeat the Recorder test with disabled hardware and verify zero positive
dispatch. Deliberately enable hardware and verify a timed Start requests
current at its planned start, followed by a confirmed zero on expiry where
authorized. While a run is active, disable hardware, leave a pending draft,
verify Stop stays available, and check that re-enable alone cannot resurrect
a retired schedule. Compare the real Recorder and Level 2 log sequence,
including a one-minute extension and an immediate unlimited subsequent Start.
