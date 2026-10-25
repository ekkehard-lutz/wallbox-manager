# v0.4.0-beta.6

This GitHub **prerelease** packages the reviewed Idle display and session-meter
corrections from `7004510259c01378b9d48b9de45601c5dcf96a72` and
`1335a9eadabc3441574c4ae9ebc722a5bd68c588`, based on beta.5/develop
`b1c663ae6b98330bb9e68c7837d532c6cf61539f`.

## Changes

- The charging-state sensor can display **Idle / Inaktiv** when the current OCPP
  registration is confirmed, Connector 1 reports Available, and no open or
  unresolved session exists. This is explicitly a WBM display projection, with
  distinct provenance; no synthetic OCPP observation, transaction or timer is
  created. Genuine applicable charging-state evidence retains priority.
- Old authentic Idle no longer carries across an Available-to-Occupied transition
  or a new transaction. Idle during an active/unresolved session or contradictory
  connector state is suppressed. Authentic Idle after Ended, including an occupied
  connector after an explicit remote stop, remains valid where appropriate.
  Occupied is never interpreted as Charging. Display and provenance changes
  publish entity updates.
- Active sessions retain a known `start_meter` and an internal last valid
  `current_meter`; the public `end_meter` and end time remain unknown until Ended.
  Live energy is `current_meter - start_meter`. Suitable Ended evidence sets the
  final end meter and energy. Session identity and final values survive reload,
  HA restart and OCPP re-registration until a new session replaces the display.
- Started/Ended may use the latest eligible embedded SDM630 energy sample at or
  before the event, at most **15 seconds old**, within the current generation.
  This accommodates the roughly 5–6-second energy acquisition period and the
  station's existing 15-second validity bound. Original acquisition and event
  timestamps remain separate and are exposed on the boundary sensors.
  **Older boundary meters are bounded approximations, not exact event-time
  measurements.** Later measurements never reconstruct missing start meters or
  become retroactive exact boundaries. No event delay or extra Modbus query is
  introduced.
- Faster live-energy updates can use regular EVSE 1 MeterValues only after the
  registered `wallbox-stationary` manufacturer/model, Connector 1 transaction
  binding, and an identical energy sample with identical acquisition time in both
  channels establish the supported absolute-counter mapping. The proof must be
  renewed for each generation/session. Other channels remain separate.
  Ordering, invalid-value, replay and monotonicity guards remain in place.

## Persistent data and compatibility

The existing session store keeps its HA storage version and adds document
`schema: 2`. For old active sessions, the formerly provisional `end_meter` moves
into `current_meter`, while the public end meter becomes unknown. Completed
session values, session IDs and closed transaction IDs are retained. The legacy
ledger is not deleted or rewritten. Historically missing meters and acquisition
times are not invented.

Entity identities, configuration versions, dependencies and the existing OCPP 2.1
protocol remain unchanged. Wallbox-side whole-Wh normalization and WBM's existing
unit conversion are preserved. This release has no active WBM charging control;
physical/electrical safety remains station-owned. The existing read-only discovery
and permission polling are unchanged. PV V3 remains DRAFT overall.

## Validation and limits

The integrated release candidate is validated with the complete Python suite
(**158 tests**), frontend suite (**8 tests**), Ruff lint/format, JavaScript syntax,
Python compilation, Git whitespace and manifest/HACS/source-scope consistency
checks. Five existing HA/backoff dependency deprecation warnings remain.
Publication requires successful GitHub Actions checks on the exact final develop
commit and on the annotated tag; local tests alone do not satisfy that gate.

Without a matching shared sample or the supported identity, live-energy channel
association stays disabled and transaction samples remain authoritative. Unknown
or contradictory states stay conservative. WBM cannot infer a physical reconnect
when no qualifying status or transaction event arrives. An Idle timestamp exactly
on an invalidating lifecycle boundary does not renew authentic evidence.

Compatibility and persistence are supported by automated tests and source review,
including the wallbox-stationary contract at
`222d3329a76a972e9ac7cec5030bee5b3071320e`. No new hardware acceptance is claimed.
See [session boundaries, migration and Idle lifecycle rules](../session-meter-boundaries.md)
for the detailed acceptance rules and regression evidence.

## HACS installation boundary

Select **v0.4.0-beta.6** through the established HACS prerelease-version workflow.
After the separately authorized installation and Home Assistant restart, verify
integration manifest version **0.4.0-beta.6**. GitHub's tagged source archive is the
integration source; no separate integration ZIP is required.

This publication does not install files on Home Assistant, restart services or
access the live wallbox. The operator performs HACS installation and physical
validation separately. `main`, existing tags and stable releases remain unchanged;
this release is explicitly Prerelease and is not marked Latest or Stable.
