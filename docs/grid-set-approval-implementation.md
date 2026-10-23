# NETZ explicit SET approval (WBM-GRID-22–30)

Implementation on `fix/grid-zero-approximation-lockout`, synchronized by fetch
and safe fast-forward to required specification HEAD
`4ed609fff61dc50320d25cffae164c507d07d382` before editing. Existing untracked
workstation files were preserved. No version, merge, release or deployment work.

## Persistent boundary and entities

The existing profile document is still the only configuration store. The
historical `power_kw`, `grid_start_delay`, `grid_duration` fields now contain
editable drafts; existing Number/Text IDs and service methods remain unchanged.
`grid_approved` stores the last approved triple plus its monotonic `revision`.
`grid_draft_revision` orders draft changes independently. The new connector-scoped
`button` role `grid_set` approves all three fields under the existing Settings
lock in one awaited Store write. No in-memory approval, execution notification or
pending-state clearance occurs before that write succeeds. Profile writes use
HA Store atomic/fsync replacement and verify the actual disk document: HA
2026.8.0's `helpers/storage.py` catches/logs WriteError without propagating it,
so awaiting `async_save()` alone is insufficient. The regression injects a real
low-level WriteError rather than only mocking the async API. Shutdown-deferred
writes are refused before approval. Duplicate approvals
are idempotent. Storage/validation failures retain the persisted drafts and old
approval and publish an error. Offline, Local, no transaction and permission OFF
do not inhibit durable approval; all original OCPP execution guards still apply.

The controller consumes only approved numeric power. Draft edits never launch
reconciliation or alter business deadlines. The obsolete one-second power-edit
debounce was removed. Explicit electrical commits invalidate the execution epoch,
fencing queued wire requests, callbacks and late responses. Equivalence/timing
commits preserve recognized retry deadlines while replacing callbacks with the
current epoch. There is no cyclic regulator or new polling loop.

`grid_set_required` and `grid_set_ready` are backend-authoritative, along with
approved values/revision, draft revision and approval error. The card discovers
the connector button through existing entry/role metadata; it never compares SET
visibility to actual hardware. SET is an accessible native button beside the
NETZ editors and remains usable offline/Local. An outstanding lockout cannot
re-show it after approval. Local service writes must settle before approval;
newer backend draft revisions supersede optimistic edits from another card.
Backend errors also appear in the card. Locale numeric input, +/- hold behavior,
optional-time clearing, immediate reserve/approximation controls and concise
actual -> approved desired presentation remain intact.

## Electrical equivalence

A numeric power edit can auto-approve when timing drafts equal approved timing
and the canonical envelope resolves both approved and drafted powers to exactly
the stable approved desired phase/current. This compares against desired intent,
including during a confirmed interim lockout point, rather than physical actual.
Known current-generation confirmation and no unresolved in-flight proposal are
required; absent/uncertain planning context keeps SET required conservatively.
The new numeric value and revision are durable and authoritative for later
approximation, cap changes and transactions. Stable equivalence sends no OCPP
command and retains the original retry deadline. If capabilities change during
the storage await, reconciliation uses the newly committed numeric power rather
than leaving the earlier capability decision authoritative. Ordinary telemetry
alone never recomputes SET visibility or commits drafts.

An approximation edit still reconciles immediately using approved numeric power;
it cannot consume an unapproved numeric or timing draft. Current caps and reserve
retain their immediate paths. Existing acknowledgement/uncertainty/readback logic
continues to distinguish desired, acknowledged, actual and measured values.

## One-shot timing

`grid_next_timing` is consumable scheduling state, separate from the last
approved configuration and active `grid_request` deadlines. Only changed,
approved timing fields populate that state; an unchanged consumed delay is not
rearmed by approving new power or a new duration. Last-approved and draft values
remain available after consumption. Merely pressing SET never changes an active
start/end deadline. Repeated permission ON during an existing session also does
not restart its schedule.

An applicable permission OFF->ON or new eligible transaction consumes pending
approved timing and creates its absolute deadlines. Capturing timing, consuming
it and writing the request use the same persistence lock as SET and draft edits,
so a concurrent start cannot consume a mixed or stale configuration. Failed
schedule persistence retains the start intent and pending timing; a later
legitimate decision must establish the schedule rather than bypassing its delay.
Explicit zero duration remains immediate expiry; empty duration is unlimited.
An explicit confirmed ON also reconciles when an in-flight OFF was superseded
before publishing false; this does not depend on a fabricated permission edge.
OFF cancels the active request through the existing permission path. Business
deadlines survive power approval and restart without relative rearming.

## Migration and source comparison

On beta.10 upgrade, the approved triple is initialized from existing persisted
settings and saved before transport/entities start. Drafts already equal it,
so no surprise SET or schedule rearming occurs. Migration adds only the new
approval/timing fields rather than persisting default manual settings prematurely;
historical RestoreEntity values and foreign profile fields are preserved.
Subsequent reloads retain independent drafts, approval and consumed/active timing.

Normative sources read: complete `grid-profile.md`,
`architecture-responsibility.md`, `active-ocpp-control.md`; WBM-GRID-22–30 replace
automatic edit approval while WBM-ARCH-01–12 retain precedence.
Historical reference: `v0.3.2-beta.18` at
`4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`, especially
`custom_components/wallbox_manager/grid_timing.py`: preserve one-shot timing,
empty/zero distinction and permission-based expiry, without restoring its runtime.
Actual openWB/core source inspected at
`ed5f7038c5c9be525e05429d3a82ec7571680bf7`:
`packages/modules/common/evse.py` and `packages/control/phase_switch.py`.
Its offered-current comparison and physical switching delegation support keeping
approval in WBM and electrical switching/interlocks in Stationary; no openWB
regulator or physical-safety manager was imported.

## Changed files and validation

Production: `settings.py`, `grid.py`, `__init__.py`, `button.py`, `entity.py`,
`www/wallbox-manager-card.js`, `strings.json`, `translations/en.json`,
`translations/de.json` under `custom_components/wallbox_manager/`.
Tests: new `tests/test_grid_set.py`; existing Grid execution tests now explicitly
approve power/timing before testing execution. Identity/migration and card tests
were extended without removing the prior electrical/outcome coverage.

Final maintained checks:

- Complete Python suite: 347 passed, five existing dependency warnings.
- Frontend/card suite: 27 passed.
- Ruff lint and format, Python compileall, JavaScript syntax: passed.
- Manifest remains `0.4.0-beta.10`; metadata/dependencies and HACS JSON valid.
- English/German translation keys and SET labels verified.
- Git whitespace checks and focused diff review: passed.

Regression coverage includes real HA Number/Text/Button services, repeated edits,
SET failure/duplicates, offline/Local/permission states, immediate reserve service
writes, approximation with pending draft, cap changes during durable writes,
atomic timing capture, independent entries/cards, old callbacks/in-flight writes,
SET visibility during PhaseSwitchLockout/ReenableLockout, actual/readback recovery,
restart/upgrade, active deadlines and consumed delay, long press and locale input.

All OCPP peers are schema-valid local simulators. Physical acceptance of the
paired Stationary/EVSE, battery service and real lockout timing remains pending.
Conservative equivalence requires SET when confirmation/context is incomplete.
No Stationary changes, actual HA/hardware interactions, service restarts,
manifest version change, tag, release, deployment or develop/main merge occurred.
