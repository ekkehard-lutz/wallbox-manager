# Live NETZ timing, active warnings and session presentation

Implementation branch `feature/live-netz-timing-and-status` starts at approved
requirements commit `0e2c54ea9117d1ae466a31b7f7a4e1f9430da31b`, extending
beta.14 develop `b46832404a478c1f2588822b70acaf7af46a080e`.
Scope: WBM-DISP-01–03, WBM-MSG-01–07, WBM-TIME-01–24 under ARCH-01–12.

## Runtime paths and intended behavior

- Settings owns durable draft/approval changes, schedule epoch/start/end and
  revision-fenced timing-edit ownership/expiry. Start/Apply persist business
  schedules independently of vehicle/transaction eligibility. Ordinary power
  equivalence and initialization provenance remain intact.
- Grid retains its existing event-driven deadline handle and fenced OCPP path.
  Business deadlines must run without an eligible transaction or live transport;
  current/phase proposals still require confirmed Remote, hardware permission,
  transaction and valid capabilities. Duration expiry retires release and retries.
  Duration-only Apply retains planned start; delay Apply establishes a new epoch.
- Existing Text/Button entity identities remain. A connector/runtime/revision
  scoped editing service coordinates both timing fields and the 15-second window;
  timing-only rollback must preserve independent power drafts and approval state.
- Canonical active warning conditions are distinct from diagnostic log history.
  Sources explicitly raise/resolve stable identities, newest active condition wins,
  and generation/intent guards prevent stale resurrection. No log polling/history.
- The existing card renders canonical deadlines locally, shows drafts across both
  time fields during interaction, and projects one current warning/error. Session
  energy formatting alone changes to one decimal; raw measurements stay unchanged.

The later approved operational amendment supersedes conflicting next-start-only
timing semantics. Business scheduling does not authorize electrical execution.
Reload still cannot resume a previous release automatically. Stationary alone
owns CP and all physical protection. No new OCPP variables are needed.

## Implemented scheduling and editing contract

`Settings.grid_request` now contains `epoch_at`, `start_at`, nullable `end_at`,
and its schedule `revision`. Every explicit NETZ Start creates it, including a
Start with no vehicle, Local authority, disabled hardware or disconnected transport
when a valid positive numeric selection exists. This is business permission only.
The existing `Grid.schedule_timer` selects the nearest planned start/end or edit
expiry. It uses HA-owned tasks rather than an adapter task that disappears when
transport closes. No second scheduler or OCPP timing variable was introduced.
The current transaction, Remote, hardware, capability, physical phase and wire
intent guards still protect commands. Positive dispatch also checks the business
phase at the actual wire boundary. Ordinary EV arrival never re-arms deadlines.

Duration-only Apply keeps the original start and changes/removes the end. A changed
delay creates a new epoch at durable approval acceptance and requests authorized
0 A while waiting. Power/phase and time changes approve in the same locked durable
document. The old deadline remains scheduled during a delayed or failed Apply;
expiry fences late approval and cannot become an implicit Start. An in-flight
waiting/zero reconciliation cannot swallow planned start: its completion launches
a fresh decision if the business phase changed. A duration which
has already elapsed ends release immediately, including explicit `00:00` at the
planned start. Normal 0-A pauses preserve hardware permission and transaction.

`grid_timing_revision` fences both Text entities as one editing area. The new
`wallbox_manager.edit_timing` entity service accepts `begin/update/end`, owner,
expected timing revision and runtime incarnation. `begin` takes ownership;
`update` changes only a validated draft; `end` records an absolute 15-second
`expires_at` for meaningful timing edits while ON. The durable
`grid_timing_edit` also holds active-focus state, owner, timing revision and
schedule revision. Direct HA `text.set_value` is supported and takes a fresh
revision with an immediately running edit window. Any new owner, direct write,
Apply or runtime replacement fences stale browser callbacks.

At edit expiry, Settings rechecks release, both revisions and the old business
end under its existing lock, then restores **only** the timing drafts. Power and
phase drafts, approved revision and schedule remain intact. A failed rollback
raises a storage warning and is not busy-loop retried; business expiry is still
enforced, including while rollback itself awaits storage. A post-write retirement
check compensates a rollback crossed by Stop/expiry so it cannot resurrect release
or discard the retained user drafts. Duration expiry has priority, retires release and clears the window
while retaining drafts. It deliberately does not invalidate a legitimate in-flight
time write finishing as an OFF draft; subsequent Start/Apply advances the timing
revision and fences that old editor. Reload resets release/schedule/editor ownership
and preserves pending drafts and `grid_power_initialized` provenance.

The card serializes editor operations and waits for canonical revision/value
acknowledgement, not merely service completion. Both fields show drafts during
focus and the pending window, including across cards. Moving focus onto Apply
preserves the first click. A local cancellable rendering timer projects absolute
timestamps with floor-minute `HH:MM`; no HA entity update, storage write or OCPP
request is generated by a rendering tick. No armed schedule displays `--:--`
except meaningful pending drafts or active editing. Actual duration expiry and
release decisions remain exclusively in the backend.

## Active warning lifecycle

`State.messages` holds a bounded map of **active conditions**, not a log/history.
Stable identities include connection, individual control variables, capability
context, grid outcome/limitation, storage, setting validation, battery reserve and
station fault. Each condition carries source/severity/code, sequence, raised time,
runtime and optional network generation. Identical notifications do not reorder
warnings. Newest active sequence wins; clearing it reveals an older still-active
condition. Generation replacement removes old network conditions; late callbacks
cannot raise or clear messages for the replacement. Runtime-local storage failures
survive an unrelated connection change. Diagnostic logs and command entities remain.

Verified bootstrap resolves a lost-connection warning. Current successful or
superseding control intent resolves command errors. Lockout warnings belong to the
current blocked intent and clear on retry success/cancel/supersession. Current valid
capabilities resolve capability warnings; confirmed non-faulted status resolves a
station fault; durable successful save resolves storage failure. Ordinary waiting,
pending and temporary unconfirmed state alone raise nothing. Battery reserve error
projection observes existing recovery behavior without changing its writes.

The card displays one canonical warning, translated for English/German. Its bounded
local service/validation failures resolve on retry, confirmed field replacement,
runtime/entry replacement or successful operation, then reveal any older canonical
warning. Local browser communication failures cannot be shared with a different
browser before a backend request exists; backend failures are canonical across cards.

## Migration, compatibility and operational limits

- Version remains `0.4.0-beta.14`; this branch is not an installable release approval.
  Entity unique IDs, service `step_power`, resources, HACS packaging, sensors,
  meter/ledger/statistics and OCPP inventory are preserved.
- Existing saved time selections remain drafts/approved values. The obsolete
  `grid_next_timing` next-transaction mechanism and arming path are removed. Any
  legacy field is preserved losslessly in storage but ignored. Saved old deadlines
  never resume release on reload; a new explicit Start establishes fresh instants.
- The approved operational amendment supersedes old next-start-only timing tests.
  Transport loss is not a confirmed Local takeover: an already active business
  schedule continues, while genuine release OFF still reconciles a retained
  positive offer to zero at the next authorized transaction. Reload/profile change
  and actual Remote-to-Local loss cancel release. Local hardware charging remains
  outside WBM current/phase authority.
- Refresh cached Lovelace JavaScript when installing after independent review. The
  new card requires its matching WBM backend editor service; stale revisions are
  rejected rather than guessed. Existing direct HA Text automation remains valid,
  with the new 15-second draft-only window while a schedule is active.
- Browser blur/pagehide/disconnect sends editor end. Abrupt browser/process/network
  loss cannot guarantee delivery of that final service; a focused ownership record
  remains pending until a fresh editor, explicit action, duration expiry or HA reload
  supersedes it. It never alters the accepted schedule or grants new charging.
- Offline/Local expiry retires manager permission immediately; physical zero can
  only be sent after normal Remote/transaction eligibility returns. No hardware
  claim is inferred from the business deadline. Wall-clock changes are observed
  at canonical events/deadlines; no additional clock polling was added.
- Stationary `feature/disabled-cp-keep-awake` at
  `c32e9c935601b8b9271200f1dc3f669dde43275e` remains the coordinated companion.
  Existing OCPP 2.1 `ChargingEnabled`, authority, current/phase and transaction
  semantics are unchanged. A manager 0-A pause while hardware stays enabled still
  permits Stationary keep-awake; hardware disable stops it in the companion change.

## Reference behavior inspected

- WBM `v0.3.2-beta.18`, peeled commit
  `87963014c9af73416c438807f523a3da2a98bd22`:
  `custom_components/wallbox_manager/grid_timing.py` (deadline persistence/arming)
  and the prior battery reference. Its old runtime and next-start controller were
  not restored.
- Actual `openWB/core` commit `849340ec8d91f4e7cca08a7664cf9ec0187fb496`:
  `packages/control/chargepoint/chargepoint.py`, `_is_manual_lock_inactive`,
  `_is_ev_plugged`, `is_charging_possible`, `_is_phase_switch_required`.
  Eligibility and physical phase/lock sequencing were cross-checked; no openWB
  polling/control loop was copied into WBM.

## Validation

Deterministic fake wall clocks, UI timers and scheduling/persistence barriers cover
DISP-01–03, MSG-01–07 and TIME-01–24: EV arrival at each stage, no-EV/Local/disabled
Start, offline expiry, both lockouts outliving duration, immediate/past/extended/
removed duration, delay Apply during charging (including charging begun while
editing), active focus/grace expiry, retained drafts/new Start, timing-only rollback
with independent power draft, ownership/revision/runtime conflicts, HA entity
services, failed/delayed persistence and no per-second Recorder/OCPP activity.
The full existing electrical, energy, reserve/recovery, phase-step and combined
Stationary OCPP contract suites remain required. Final counts and CI evidence are
reported with the implementation commit. No merge, release, installation or
production operation is performed by this task.

Local final validation: **502 Python tests passed** (including the hardware-mocked
companion Stationary/WBM contract), **53 frontend tests passed**, Ruff lint/format,
JavaScript syntax, translation leaf parity, service/manifest/HACS metadata,
39-file candidate ZIP content verification and whitespace checks passed. Four
forced deadline/ownership races were repeated in three rounds (4/4 each). The
subsequent full run includes the real business timer firing during blocked rollback
storage. The eight unrelated untracked files retain their baseline SHA-256 hashes.
CI uses no companion checkout and therefore skips that single combined contract;
its exact-commit result is reported separately. Stationary validation and its
pre-existing quality baseline are recorded in the companion implementation report.
