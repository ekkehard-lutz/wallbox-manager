# Independent final review of passive v0.4

Reviewed starting commit `e190afa97015983bd093482bf57ea91b4ef01ab9` on
`rewrite/passive-v0.4`; compatibility reference is immutable beta.18 commit
`87963014c9af73416c438807f523a3da2a98bd22`. This is a source, real local HA
registry/entity-platform and simulated OCPP2.1 peer review. No live installation,
wallbox or external station project was accessed. Manifest remains beta.18.

## Release blockers found and corrected

- Transaction ordering allowed a delayed different ID to displace the current
  transaction, and a superseded active ID could return. Reject older/equal
  replacements and tombstone superseded IDs. When scope changes from EVSE to
  connector, publish the retired scope unavailable and stop its duration timer.
- Session accounting could subtract a transaction-relative baseline from an
  unrelated lifetime/parent register. Pin accounting to its first actual stream;
  incompatible streams cannot change that delta. Missing baselines stay unknown.
  A legacy baseline without stream provenance cannot support new live accounting.
  Ended without an exact final endpoint now clears provisional final values;
  a valid endpoint restores them, a decreasing/conflicting register stays unknown.
  Older power on another channel cannot displace newer canonical power. Embedded
  transaction measurements later than their event are rejected.
- A missing explicitly configured card entity silently selected another entry
  when only one entry remained. Explicit unresolved bindings now stay unbound.
- A legacy EVSE-scoped registry entity for another EVSE did not block migration.
  All scoped registry evidence, including disabled entities, now participates in
  the pre-write binding check. Multiple subentries, including duplicate targets,
  require a migration decision rather than silently choosing the first.
- Inventory could expose conflicting duplicate currents, zero step, duplicate
  phase counts, wrong units or malformed scope as verified capability facts.
  Retain strict EVSE/connector identities, ampere units and positive numeric
  values. A single inventory acquisition timestamp lets canonical conflict
  handling invalidate duplicate disagreement, including a third repeated row.
  Completed reports ignore later pages; ambiguous Actual attributes degrade.
- Failed profile persistence could leave central options changed despite a
  reported failed edit. Save first, then commit options/in-memory settings.

Closed adapters also reject queued inbound CALL processing and variable updates.
No replacement abstraction, regulator, policy engine or background framework was
introduced. No release blocker remains identified after the corrected checks.

## Independent evidence and compatibility

The seven new defect-specific Python tests were copied into an isolated archive
of the original e190afa source and all seven failed there (22 deselected). The
new frontend missing-binding test also failed against the original card. These
are counterexamples, rather than assertions written only around the corrections.
The additional restoration test passes, verifying the existing restore behavior.
Existing migration coverage was extended rather than adding a historical suite.

The corrected maintained suite has 29 Python and eight JavaScript tests. Python
covers real local WebSocket/schema framing, repeated boot, late old-generation
readback, reconnect, missing initial measurements, invalidation, held readings,
permission expiry, one five-second polling task, unload cleanup, session ordering,
energy stream separation, final endpoints, retired-scope timer cleanup, failed
storage, instance isolation and actual Number/Select/Text edits. Frontend tests
cover explicit/missing entry binding, two-entry isolation, edits, disabled physical
buttons, forbidden service domains and unknown measurements.

Public IDs were compared with beta.18's station/observation/control/session/
capability constructors. They retain the compact JSON scope strings and original
physical device identifier `(wallbox_manager, entry_id + ':' + station)`.
Electrical energy remains kWh / energy / total_increasing; power remains W /
power / measurement, voltage V / voltage / measurement and current A / current /
measurement. Session energy retains no state_class and cannot replace lifetime
energy. Capability current units remain A. Energy Dashboard registry identities
and statistics metadata are preserved; no Recorder/statistics operation occurs.
This does not prove an existing Recorder database migration end to end.

Actual HA registries preserve historical entity_id, registry ID, device ID,
user device name and a USER-disabled entity through detach/removal and platform
reload. Ambiguous connector and foreign EVSE cases fail before changes to entry
or registry. Manual Number/Select preferences restore under historical entity IDs,
including the exact rational current attribute `19/3` rather than rounded display.
Legacy profile unknown fields and full session storage remain unchanged on load.

For a separate rollback check, the immutable beta.18 source was exported locally
and its actual `station_config.setup_station_configuration` plus original
`validate_reference_options` were imported under an isolated package name. Given
an actually migrated entry/registry, that helper recreated the wallbox subentry
and retained the historical disabled energy registry ID, device ID and schema3.
The one-off check passed. It did not start the legacy controller or connect a
wallbox. The maintained migration test checks the same retained reference layout;
it does not depend on a historical checkout at CI time.

The only outbound OCPP CALL actions are GetBaseReport and GetVariables, enforced
at `_send`. Startup, boot/reconnect, configuration edits and HA actions have no
physical control command implementation. No custom WebSocket command/service
registration exists. Permission switch actions and takeover explicitly fail;
card buttons have no action callbacks. Local preferences cannot invoke a removed
ownership, PV, economic or charging-control subsystem.

## Important nonblocking issues and remaining test gaps

- Same-valued acquisitions update public timestamps and still write HA state.
  The deterministic baseline is unchanged: initial 26 accepted / 39 HA writes;
  2,000 repeated power samples / 2,000 writes; 100 changed / 100 writes; idle and
  effects on another entry zero; reconnect 26 accepted / 75 writes / 52 changes.
  Ordinary power updates have zero unrelated writes. This is not measured CPU
  improvement and does not establish that Activity/Logbook load is fixed.
- Permission polling is one five-second loop; evidence has up to two one-shot
  timers; only the current confirmed session has a one-second duration timer.
  Session projections recompute a small view for entity reads; no broad selector
  fan-out, duplicated live telemetry snapshot or passive regulation loop remains.
  Closed/superseded transaction ID tombstones are durable and unbounded; very
  long deployments could eventually need retention work. No speculative retention
  framework was added in this review.
- Unsupported/missing readbacks remain unknown. A failed bootstrap read can end
  that bounded initialization attempt before later endpoints are read. Discovery
  and missing-field diagnostics expose this; no retry engine was added.
- Old translation labels/historical architecture documents remain as assets and
  history; they do not import or execute old subsystems. Runtime role strings and
  stored preferences with old control/PV names are compatibility data only.
- Live HA native layout/version-specific behavior, a restored real Recorder DB,
  a complete beta.18 restart and long-running station interoperability remain
  untested. A separately backed-up migration/rollback trial is still required.
  No fault-injection matrix for every HA registry persistence failure or damaged
  store schema, and no exhaustive OCPP report permutation/fuzz suite is claimed.

## Explicitly deferred station-side initialization

The current station does not guarantee a complete initial unchanged-value push,
particularly per-phase zero values during CP pauses. Those values stay missing;
no zero, authority, permission or operating point is fabricated. Phase readback
expires after five seconds without a new source report; permission expires after
15 seconds. There is no periodic metering/phase query, TriggerMessage experiment,
GetCompositeSchedule implementation or station-side change in this task. A full
initial snapshot and applied operating-point evidence require separately approved
station/interface work. No physical control can use stale evidence because no
physical command implementation exists.

## Validation and exact footprint

Python, frontend, Ruff lint/format, JavaScript syntax and whitespace checks pass.
Python reports five pre-existing third-party deprecation warnings. GitHub Actions
runs the full Python/frontend/static workflow on the pushed correction commit;
its exact result is reported with the review delivery. The independent rollback
helper check is additional local evidence, outside that maintained suite.

Production source, including blank/comment lines: **18 Python modules, 2,456
Python lines; one JavaScript card, 350 lines; combined executable source 2,806
lines**. Compared with e190afa, this is +81 Python lines and zero net card lines.
Tests: 1,527 Python lines including the fixture and 126 JavaScript lines. Exact
per-module counts are in [footprint.json](footprint.json). Static translations,
branding, manifests and documentation are not counted as executable source.

No merge, version commit, tag, prerelease, deployment or charging operation was
performed. Only the rewrite branch is authorized for the correction commit/push.
