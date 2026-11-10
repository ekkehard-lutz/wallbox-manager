# Wallbox Manager v0.4 release-readiness audit

## 1. Executive summary

- **P1 confirmed:** HA 2026.8.0 can quarantine corrupt session JSON and return
  `None`. WBM then starts with empty replay history. A localhost OCPP replay
  demonstrated that a retired ID with a fresh, correctly scoped Updated event
  became WBM's active and command-eligible transaction. Intact history rejected
  the same event. Reload kept manager release OFF. No electrical action or
  physical safety failure was demonstrated.
- **P1 confirmed boundary:** A schema-2 document missing
  `closed_transactions` also loads without replay history. A valid but stale
  snapshot omits any closure not yet persisted. These are distinct from a
  quarantined file and require different evidence.
- **Beta.23:** Suitable only for controlled field acceptance with a verified
  current journal or documented first setup and a recoverable backup. Repair
  detectable damaged-history handling first if an existing installation's
  history cannot be established. **Stable v0.4.0:** Hold for a recovery policy
  and its regressions, explicit PV decisions, and physical acceptance.

## 2. Exact audit baseline

Phase A merged `2389d8e0f66e9a10d60c050757437104eb76de28` into
`c14f671263c3399d7e5e65147b1137ce0f78f490` using a two-parent merge.
The authoritative audit baseline is
`a132326b4b373e6e955ca8e04efa758f32bd93ef`, with the exact reviewed tree
`24729f1fa35d4558b139cc014f34aca1bac9a2bd`. The merge changed only 27 Markdown
files. Phase B started after the exact merge CI succeeded.
[Reviewed documentation tip CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42883964346) ·
[Develop merge CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42895899630)

## 3. Reviewed sources and tests

The [current baseline](../specifications/v0.4-current-requirements.md) and
[consolidation review](requirements-consolidation-review.md) identify applicable
requirements and evidence. WBM-ARCH-01–12 take precedence. The detailed
[architecture](../specifications/architecture-responsibility.md),
[active control](../specifications/active-ocpp-control.md),
[release](../specifications/charge-release.md), [NETZ](../specifications/grid-profile.md),
[timing](../specifications/operational-display-and-timing.md),
[passive milestone](../specifications/passive-v0.4.md),
[PV V3](../specifications/pv-regulation.md) and
[operating-point response](../specifications/pv-operating-point-response.md)
define their respective scope. PV V3 remains DRAFT overall. Existing individual
approvals and tested implementation are tracked separately.

Runtime evidence is the pinned Python implementation, its HA configuration and
entity services, card, and the tests mapped in section 12. Earlier evidence was
cross-checked against current source. [F1](../ocpp-ambiguous-rejection-certainty-review.md) ·
[F3](../pv-economic-journal-recovery-review.md) ·
[F5](../critical-fault-intent-review.md) · [R1–R3](runtime-blockers-r1-r3-review.md) ·
[PV rollover](pv-economic-transaction-rollover-review.md) ·
[Batches 1/2](inactive-controls-cleanup-review.md) · [Batch 3](entry-registry-cleanup-review.md) ·
[Batch 4](journal-import-cleanup-review.md)

Source comparisons:

- Stationary `279287c3c1d8863e803144eccf773ab3f10889a5`,
  `ocpp_runtime/station.py` lines 261–304 and
  `ocpp_runtime/transactions.py` lines 44–95, 132–174 and 214–237.
  Registration precedes transaction tasks. New transactions use UUID4, and
  active state reuses its ID with a durably reserved advancing sequence.
  Ended is reserved before transmission and clears Stationary active state
  after the call succeeds. A failed call can leave a sequence gap.
- WBM `v0.3.2-beta.18` at `87963014c9af73416c438807f523a3da2a98bd22`,
  `custom_components/wallbox_manager/session_storage.py` lines 1–36.
  The retired ledger used a one-second delayed save, final flush and
  `atomic_writes=True`. It does not supply missing schema-2 replay history.
- openWB/core `849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
  `packages/control/chargepoint/chargepoint.py` lines 154–188. Actual
  upstream source shows local interlocks and an unplug-triggered OCPP stop.
  This is a responsibility comparison. Stationary defines the actual
  interface used by WBM. [Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py#L154-L188)

## 4. Session journal investigation

`async_setup_entry()` loads `wallbox_manager.<entry_id>.passive_session`
before constructing the listener. The HA Store wrapper uses major 1 and
minor 1. The payload uses schema 2. A valid document restores the current or
last session and the full set of retired transaction IDs. Live session
generation is invalidated. New snapshots omit it and retain separate energy
metadata. Unsupported payload schema raises `ConfigEntryError`.
`session.load()` interprets `None` as an initial empty state and accepts
missing `closed_transactions` as an empty list. A missing `session` key
instead raises. WBM does not distinguish which cause produced `None`.
[Setup](../../custom_components/wallbox_manager/__init__.py#L76) ·
[Session loader and serializer](../../custom_components/wallbox_manager/session.py#L280)

Each accepted session change schedules a one-second delayed write. Graceful
teardown fences Grid, stops the listener and attempts final persistence even
after an earlier cleanup error. Session Store uses the default non-fsync
write and has no explicit disk verification. This leaves a window between
accepted events and the durable snapshot. It also means HA can log a write
failure without raising it to the integration. This is separate from the R2
teardown regression and from corrupt-read handling.
[Teardown](../../custom_components/wallbox_manager/__init__.py#L45) ·
[Session change publication](../../custom_components/wallbox_manager/session.py#L59)

## 5. HA Store behavior and version evidence

`requirements-dev.txt` pins **Home Assistant 2026.8.0**. The probes ran its
installed package on Python 3.14.7. Source inspected in
`homeassistant/helpers/storage.py` at `Store.async_load`,
`_async_load_data`, `async_delay_save`, `_async_handle_write_data` and
`_write_prepared_data`, plus `homeassistant/util/json.py:61–81` and
`homeassistant/util/file.py:46–79`. These findings apply to the tested
environment. The integration manifest does not pin the operator's HA Core.
[Repository dependency pin](../../requirements-dev.txt)

| Input / operation | HA 2026.8.0 result and WBM consequence |
| --- | --- |
| Never-existing file | Disk helper returns `{}`, Store returns `None`. Normal empty startup. Known absence in the preloader may also return `None`. |
| Malformed JSON with successful quarantine | Decode failure is wrapped in `HomeAssistantError`. Store renames the file to `.corrupt.<timestamp>`, creates a repair issue and returns `None`. WBM starts empty. Quarantine rename failures propagate. |
| Existing quarantine with no live file | Store reads the exact live path and returns `None`. It does not consult the sibling artifact. WBM starts empty. |
| Existing literal `{}` or version-1 wrapper with `"data": null` | Both can produce `None` on a cold disk load. Neither is a valid WBM schema-2 snapshot. The literal empty-object probe used the ordinary file load, not HA's optional preloaded-cache path. |
| Missing `version`, missing `data`, or array wrapper | `KeyError`, `KeyError` or `TypeError` respectively in the tested forms. Entry setup fails. |
| Wrapper version 2 or 0 with otherwise valid payload | Newer major raises `UnsupportedStorageVersionError`. Older major raises `NotImplementedError` with the default migration method. Entry setup fails. |
| Same major, different minor | The tested wrapper 1.2 falls back to its payload and is rewritten as 1.1. HA can do this before WBM sees the payload. Missing minor defaults to 1. |
| Unsupported WBM payload schema in current wrapper | Store returns the data. WBM rejects it with `ConfigEntryError`. The wrapper and payload versions are independent. |
| Inaccessible path / read I/O error | HA wraps non-missing, non-JSON I/O failures in `HomeAssistantError` and re-raises. A directory at the file path reproduced `IsADirectoryError` as the cause. A separate permission probe used file mode 000 under the unprivileged fixture user. |
| Interrupted write | Default writes stage a same-directory temp file and use `os.replace`, without the fsync path. A loss before replacement can leave the previous live snapshot or no first snapshot. Store may log `WriteError` and return. An unflushed-event probe verified the stale durable snapshot. Real power-loss/filesystem behavior was unverified. A torn live JSON was supplied as input to test recovery, without attributing its origin to HA's normal rename path. |

## 6. Session replay reproduction results

The tests used a temporary HA config directory, real HA Store, WBM entry
setup/unload and a localhost OCPP 2.1 peer. No runtime or permanent test was
modified. The equivalent reproduction below uses the existing fixtures
`conftest.ha`, `test_protocol.peer/boot`, `test_grid_profile.GridStation` and
`test_pv_transaction_rollover.report`.

1. Seed the entry-specific live file with
   `{"version":1,"minor_version":1,"key":"<current Store key>","data":{"schema":2,"session":null,"closed_transactions":[A,B]}}`.
   Use UUIDs `11111111-1111-4111-8111-111111111111` and
   `22222222-2222-4222-8222-222222222222` for A and B. For the damaged
   variant replace those bytes with `{"version":1,"data":`. For the
   incomplete variant use schema 2 with `session:null` and omit the ID list.
2. Install the entry, connect to `/Wallbox01` with `ocpp2.1`, boot, and wait
   for simulated Remote readback. Verify the malformed file became a
   quarantine and the entry has no recovered session or closed IDs.
3. Send `TransactionEvent Updated` with `TxResumed`, ID A, sequence 8,
   EVSE 1/connector 1, `offline=false`, charging state EVConnected and
   timestamp after the new boot epoch. Compare `state.session`,
   `Grid.transaction()`, `Grid.eligible()` and `charge_release`.
4. Send a valid new Started with ID C
   (`33333333-3333-4333-8333-333333333333`), sequence 0, newer timestamp.
   Close the peer, unload the entry and inspect the actual disk payload.
   Set up a fresh runtime, boot, send C Updated sequence 1, then B Updated
   sequence 8 with a fresh timestamp.

| Scenario | Result |
| --- | --- |
| Valid empty/current/complete schema-2 history | Valid empty session retains A/B. Active C restores as historical until reconfirmation. Completed C restores with ended state and A/B/C closed IDs. Valid repeated restart retains replay history and rejects retired IDs while accepting legitimate C. |
| Malformed JSON and quarantine | Cold load starts empty. A correctly scoped Updated with fresh time enters as active A with unknown start and becomes WBM's eligible transaction. New C still succeeds and retires A. Disk then contains C and A only. B is accepted on the next restart despite the new valid snapshot. Pre-quarantined input reproduces the empty load in the Store matrix. |
| Schema 2 missing closed-ID list | The same wire and persistence result as malformed JSON, without a quarantine. |
| Valid stale snapshot containing B only | A is accepted, C replaces it, both A and B are in the resulting list. B remains rejected after restart. The independent two-HA-instance probe read a valid old disk snapshot while the newly closed A was pending the one-second save. This models abrupt loss before write without killing a live HA process. |
| Controls | Intact history rejected the fresh A event. For all variants an A event timestamped 1 ms before the boot epoch, an event for connector 2 and an unknown-ID Ended failed to establish a session. Connector 2 produced the library's `InternalError` reply and no state mutation. New C and same-ID C Updated were accepted under the ordinary current-scope rules. |

**Eligibility chain.** The listener enforces protocol and station path,
replaces the old adapter and advances the connection generation. Boot clears
writable inventory and advances boot generation and timestamp epoch.
`Adapter._handle_call()` checks live connection/generation, and the OCPP
library decodes and validates the CALL. `Adapter.transaction()` resolves the
configured EVSE/connector and can inherit scope only from a known same ID.
`reported_time()` requires a timezone and rejects a timestamp more than
five seconds ahead of receipt. Offline frames are skipped. `session.event()`
requires current generation/scope and time at or after the epoch, rejects
every ID in `closed_transactions`, and for a known same ID requires matching
scope, an open record, increasing sequence and nondecreasing timestamp.
A different ID needs a newer timestamp than any extant session and cannot
start with Ended. Started or Updated can establish it. An Updated without
Started does not manufacture a start boundary. Retirement on Ended or
replacement adds the ID to the closed set.
[Listener and adapter](../../custom_components/wallbox_manager/ocpp.py#L103) ·
[Transaction handler](../../custom_components/wallbox_manager/ocpp.py#L398) ·
[Event fence](../../custom_components/wallbox_manager/session.py#L68) ·
[Timestamp parsing](../../custom_components/wallbox_manager/meter.py#L33)

**What the reproduction establishes.** A normally delayed frame still carries
its original timestamp and can be rejected by the new epoch. The successful
case needs a fresh timestamp carrying the old ID, or a timestamp that passes
the remaining gates. No normal Stationary UUID reuse was found. Stationary's
legitimate same-ID resume uses a fresh timestamp and advancing sequence for
its existing active record. WBM cannot independently tell whether a fresh
event for an unknown ID is a new/resumed transaction or a retired ID whose
history was lost. The probe supplies that event synthetically. It does not
show ordinary Stationary sending it.

**Consequences.** Presentation can show a false active session and accept
eligible new meter attribution under that identity. Ended and reconnect still
use the remaining timestamp, scope and sequence checks. A reconstructed
transaction can satisfy `Grid.transaction()` and, with registration and
Remote authority, `Grid.eligible()`. Positive NETZ requires separate release,
hardware permission, schedule and intent gates. Release-OFF reconciliation
may still propose zero when its own evidence permits. In PV an accepted
different ID can enter the approved new-scope budget path when the separate
PV journal and all policy gates permit it. The same ID keeps its valid PV
budget. Damaged PV history inhibits automatic positive PV. The probes
demonstrated transaction eligibility and persistence, not an actual replayed
positive NETZ write, PV budget rollover on a corrupt-session replay, or
physical charging. [Grid eligibility](../../custom_components/wallbox_manager/grid.py#L745) ·
[PV budget binding](../../custom_components/wallbox_manager/pv_economics.py#L77)

## 7. Minimal recovery alternatives

PV already checks the exact live file and its quarantine siblings when HA
returns `None`. Its sentinel distinguishes first setup from known damaged
history. PV then handles economic scope, conservative inhibition and verified
persistence. Reuse the presence/error distinction where practical. Economic
STOP/reset or operator Stop semantics cannot authorize resetting session IDs.
[PV journal loader](../../custom_components/wallbox_manager/pv_journal.py#L11)

| Alternative | Guarantee, complexity and compatibility | Recovery and tests |
| --- | --- | --- |
| A. Preserve current behavior | No change. All current schema-2 documents and first starts behave as today. Remaining fences reject old epochs and wrong scope, but cannot preserve retired-ID rejection after an ambiguous empty load. | Restore a trustworthy backup manually. Keep positive and negative replay controls. Demonstrated mismatch if durable replay protection is expected after detectable damage. |
| B. Fail closed with durable history-existed evidence | A separately durable, binding-aware marker or equivalent trusted record could detect loss of the journal alone and distinguish provisioned entries from first use. More persistence, consistency and provisioning rules. Existing schema 2 can remain, but marker adoption needs a separate decision for entries with no remaining evidence. Losing both records defeats this distinction. Blocking only dispatch leaves false presentation/attribution possible. | Refuse setup or explicitly isolate transaction-sensitive behavior until an operator restores verified history. Test atomic update ordering, missing marker and journal permutations, backup/restore, write failure and first setup. |
| C. Check existing artifacts and validate schema 2, then fail closed on detectable damage | Narrow startup-only file/quarantine check on `None`, controlled failure on unreadable data and explicit required-field validation. Retains current schema 2 and accepts real first setup. Catches detectable damage and incomplete payloads without a new Store. A completely absent journal with no evidence and a well-formed stale snapshot still look valid for their apparent state. | Report entry, Store and reason through a bounded HA setup/repair diagnostic. Recover the correct complete snapshot from an operator-controlled backup and verify with replay. Test no file, corrupt/quarantined, invalid wrappers, incomplete payload, current schema variants and unchanged replay/reload. |
| D. Only strengthen writes or closure flush | Useful for the unflushed window, but cannot identify corrupt reads or reconstruct earlier IDs. A successful pre-ack terminal checkpoint changes event/write ordering and may need Stationary retry/response design. Evaluate separately if required durability exceeds the current one-second window. | Test delayed saves, write failure, crash boundaries and no duplicate attribution or response. Avoid extra writes on ordinary unchanged measurements. |

## 8. Recommended session recovery decision

Prefer C for the demonstrated readable/quarantined failure. Keep first setup
working when there are genuinely no artifacts. For detectable lost or invalid
history, prefer a clear entry setup failure before listener creation and
before any new session write, using HA's existing diagnostic mechanism.
A passive-only degraded mode would need separate publication and execution
semantics. A startup presence check and schema validation do not require
changing the payload version or adding a periodic task.

Expected implementation scope is `session.py`, possibly a narrowly shared
presence helper from `pv_journal.py` if it preserves existing PV behavior,
and focused session/teardown tests. Expected cases are the load matrix,
read-error cause, no mutation of corrupt evidence, no writes or listener on
failure, supported historical generation field, intact closed-ID replay over
restart, same-ID current reconfirmation and legitimate new IDs. Keep exact
read/write evidence distinct.

Operator decisions remain necessary for setup failure versus a defined
degraded mode, how to restore trusted history, whether protection must
detect total disappearance or stale snapshots, and how durable closure must
be before acknowledging it. A backup restores only the IDs it contains.
After irrecoverable loss, the complete retired-ID fence cannot be inferred
from UUID, sequence, a station's current active record, or a timestamp
watermark. Any exceptional recommissioning with lost guarantees needs its
own decision. Arbitrary pruning or reset is outside this audit.

## 9. PV-TUNE-09 active economic budget edits

The policy remains **OPEN for formal approval**. `Settings._set()` and the
OptionsFlow update listener update the validated current threshold without
granting release or clearing PV state. `PV.tick()` captures configuration
once before persistence and evaluates `Budget.exhaust(limit)` on the next
eligible ordinary tick. The lower threshold can therefore trigger at the
first tick using it, including at equality. A tick already holding the old
snapshot completes under that snapshot. An edit does not enqueue a PV tick.
[Number setting path](../../custom_components/wallbox_manager/settings.py#L503) ·
[Options listener](../../custom_components/wallbox_manager/__init__.py#L141) ·
[PV tick](../../custom_components/wallbox_manager/pv.py#L586) ·
[Budget state](../../custom_components/wallbox_manager/pv_economics.py#L96)

The temporary replay measured 20 Wh from 1,800 W over 40 s with simulated EV
activity. A Number edit from 50 to 75 Wh left 20 Wh and emitted no command.
An OptionsFlow-style options update to 10 Wh left 20 Wh and emitted no
command until the next tick, which latched STOP and sent zero. The probe
held the actual simulated zero response before WBM consumed it, raised the
threshold to 100 Wh during the pending response, then lowered it to 5 Wh
after consumption. STOP and 20 Wh remained, with no extra proposal from the
edits. Reload retained the 5 Wh threshold and stopped 20 Wh scope, reset
qualification was false, and release was OFF. A valid same-ID Updated
reconfirmed the transaction and retained its budget.

Existing tests cover same-ID recovery and valid new-ID rollover with or
without prior Ended. A genuinely new eligible ID starts a fresh budget at
the current threshold. The strict `>15 s` continuous qualification,
confirmed zero and zero EV consumption, SoC and positive restart evidence
remain independent. An edit cannot substitute for any gate. A qualified
reset can zero accumulated Wh under ordinary rules while STOP remains
latched. A late zero ACK remains subject to epoch/adapter/transaction
fencing. Late restart ACK/checkpoint regressions cover supersession by
operator Stop. No discrepancy with the documented edit behavior was
found in these cases. Separate operator approval is still needed.
[Budget edits](../../tests/test_pv_runtime.py#L299) ·
[Permission gaps](../../tests/test_pv_permission_intervals.py) ·
[Rollover](../../tests/test_pv_transaction_rollover.py) ·
[Late checkpoints](../../tests/test_pv_review_regressions.py)

## 10. PV-TUNE-10 inactive storage

`NUMBERS`, `SELECTS`, `REGULATION` and the current OptionsFlow section
define accepted controls. `Settings.__init__()` selects known profile keys,
then applies current regulation options. Save merges known fields while
preserving unrelated data. Current policy reads only current fields. Retired
`pv_start_delay`, `pv_stop_delay`, `power_smoothing_window`,
`optimum_max_discharge_w` and `pv_approximation` do not expose active
editors or drive regulation, authority, release or precedence. An old HA
registry row may remain unavailable. No migration is required to use the
current controls. Current-entry and PV-runtime tests verify inert options,
form/entity removal and continued current settings. No operational effect
from retaining obsolete keys was demonstrated. Prefer preservation. The
operator must decide whether to accept inert historical storage or authorize
a separate cleanup. The approved removed-controls scope and overall
DRAFT PV V3 status stay separate.
[Settings and whitelist](../../custom_components/wallbox_manager/settings.py#L107) ·
[OptionsFlow](../../custom_components/wallbox_manager/config_flow.py#L84) ·
[Current entry tests](../../tests/test_current_entry.py) ·
[Obsolete controls tests](../../tests/test_pv_runtime.py#L327)

## 11. Other confirmed technical findings

**RR-03, pre-registration session mutation.** A simulated correctly routed
peer sent Started before Boot. `Adapter._handle_call()` checks live transport
generation, while `Adapter.transaction()` lacks a registration check.
The event created a current session in the pre-Boot generation. Grid could
project its transaction ID, but `manager_ready()` and execution remained
false. Boot fenced its live use. A subsequent legitimate new Started
retired the pre-Boot ID. This is a confirmed behavior for a nonconforming
peer. The supported Stationary starts transaction tasks only after
registration, so the probe does not demonstrate a normal Stationary fault.
A narrow registration guard is a candidate hardening measure if the
operator wants session attribution restricted to registered traffic.
[Adapter lifecycle](../../custom_components/wallbox_manager/ocpp.py#L326) ·
[Transaction eligibility](../../custom_components/wallbox_manager/grid.py#L745)

Other critical contracts reviewed in section 12 showed no new confirmed
defect in the focused scope. R4 terminal-ID growth remains an acceptance and
measurement question. Complete retained history is deliberate. There is no
approved pruning rule. The damaged-session outcome is separate from F3
economic-journal recovery and R2 resource cleanup.

## 12. Requirement-to-test evidence map

| Requirements | Current implementation and automated evidence | Acceptance gap |
| --- | --- | --- |
| WBM-ARCH-01–12, V04-08/09, PV-OCPP-05/06 | `Listener.accept`, `Adapter.boot/initialize` and canonical `State` enforce routing, generation fencing, supported inventory/reads and held observations. Protocol, observation, delta and passive-availability suites cover bootstrap, missing fields and explicit invalidation. [Protocol](../../tests/test_protocol.py) · [Observations](../../tests/test_observations.py) · [Delta](../../tests/test_delta_compatibility.py) | Real Stationary inventory and unsolicited initial/reconnect fields, including unchanged reports. |
| WBM-CTRL-01–12, WBM-RELEASE, WBM-ACTION-SAFE/HW, PV-ARCH-07 | `Control` separates explicit takeover and independent hardware permission. Grid scopes wire dispatch and ACK/readback. Ambiguous rejection preserves uncertainty. The scoped Faulted edge fences positive intent and preserves required zero direction, without duplicating Stationary safety. [Authority](../../tests/test_active_control.py) · [Stationary source fixture](../../tests/test_stationary_control_contract.py) · [F1](../../tests/test_ocpp_rejection_certainty.py) · [F5](../../tests/test_critical_fault_intent.py) · [Actions](../../tests/test_safe_charge_actions.py) | Actual permission under Local and Remote, accepted versus applied offer and fault handling. |
| WBM-ARCH-07/11, V04-09, current session contract | `session.event/meter/load/document` and `Adapter.transaction` preserve scope, identity, current-generation energy link and full stored replay list. Session boundaries cover reordering, same-ID reconfirmation, energy provenance and multiple restarts. [Sessions](../../tests/test_session_boundaries.py) | RR-01/02 recovery and RR-03 registration findings. Real station resume and attribution. |
| WBM-GRID-01–40, WBM-TIME, WBM-NETZ-ACTION | `grid_point.py`, `Settings` and `Grid` resolve legal nominal 230 V points and 0 A caps, keep drafts separate from durable approvals, retire release on reload and preserve one-shot deadlines through a transport loss. Same-ID resume requires matching or valid corrective readback except for independently eligible actions/windows. Recognized Stationary restrictions control retry. [Nominal steps](../../tests/test_grid_nominal_steps.py) · [Timing and leases](../../tests/test_live_netz_timing.py) · [Lease](../../tests/test_timing_editor_lease.py) · [R1](../../tests/test_netz_resume.py) · [Reserve](../../tests/test_grid_battery_restart.py) | Real timing, phase/current, both lockouts and compare-before-restore. |
| PV-ARCH/SOC/PLAN/FAST/BAL, PV-STEP/RESP/TUNE/LOCK | `PV.tick`, planner, SoC and controller use one scheduler, battery-free SURPLUS, shared FAST/BALANCE, nine regulation parameters, phase-aware response gates and scoped continuation at a confirmed offer. [Core](../../tests/test_pv_core.py) · [Runtime](../../tests/test_pv_runtime.py) · [Planning](../../tests/test_pv_planning_release.py) · [Phase lockout](../../tests/test_pv_phase_lockout.py) | Fronius/BYD source timing, actual EV uptake and real applied phase. PV-QA-03 performance tolerances remain open. |
| PV-ECO-01–09 | `Budget` handles prospective measurement, strict reset, STOP/restart and same/new-ID scope. `PV.persist` serializes and verifies the journal. Missing/damaged distinction and permission gaps are covered. [F3](../../tests/test_pv_journal_recovery.py) · [Rollover](../../tests/test_pv_transaction_rollover.py) · [R3](../../tests/test_pv_permission_intervals.py) · [Checkpoints](../../tests/test_pv_review_regressions.py) | Measured economic intervention, real reconnect/rollover and installation evidence. |
| V04-01/02/04/05/12, WBM-ARCH-12, WBM-DISP/HS, WBM-PV-TARGET, PV-DIAG | Explicit binding and current registries fail before mutation on conflict. Settings, three entity services, state projection and reload/shutdown are covered. PV planning publication is sparse, session duration uses its separate cadence, and Level 2 eligible-cycle traces do not imply Recorder writes. [Binding](../../tests/test_current_entry.py) · [Registry](../../tests/test_identity_migration.py) · [R2](../../tests/test_runtime_teardown.py) · [Recorder proxy](../../tests/test_passive_benchmark.py) · [Target](../../tests/test_pv_target_sensor.py) · [PV diagnostics](../../tests/test_pv_pause_diagnostics.py) · [Card](../../tests/test_wallbox_card.cjs) · [Chrome](../../tests/test_wallbox_card_layout.cjs) | Actual HACS installation, mobile UI, sustained HA CPU and Recorder behavior. |

## 13. Outstanding physical acceptance matrix

Use a separately authorized HACS candidate, a full restorable backup,
confirmed compatible Stationary and appropriate mappings, a controlled
private LAN and an operator present. Record exact candidate and Stationary
versions, entry scope and observed conditions. Only use existing supported
operator controls and Stationary diagnostics. Capture release, proposal,
acknowledgement, confirmed offer and actual power separately. For the audit
baseline, **every row has physical evidence outstanding**. Historical
operator observations describe earlier conditions and do not close these
gates. Stable requirements apply where the relevant feature is configured.

| ID / requirement | Prerequisite and operator action | Expected result and pass/fail evidence | Simulated coverage | Physical evidence | Stable gate |
| --- | --- | --- | --- | --- | --- |
| PA-01 · V04-01/04/05, WBM-RELEASE-12 | Verified current binding, supported journals and backup. Install the authorized version through HACS and perform one controlled HA integration reload. | Confirm manifest, entry/registry identities, preserved settings, one listener, clean teardown and release OFF. Unexpected reparenting, old listener or unrequested release fails. Record HA setup and reload diagnostics. | Yes, setup and reload; package install separate | OPEN | Yes |
| PA-02 · V04-09, PV-OCPP-06 | Compatible Stationary. Connect, boot, reconnect and allow ordinary reporting with some values unchanged. | Capture inventory/readbacks, generation and missing fields. Supported observations initialize from new evidence and unavailable fields remain explicit. A stale field reported as current fails. | Yes, including missing baseline | OPEN | Yes |
| PA-03 · WBM-CTRL-02/03, WBM-RELEASE-06 | Stationary Local, supported permission endpoint, controlled charging conditions. Change hardware permission under Local and Remote. Use explicit Take Control. | Confirm permission Actual independently of authority and release; Remote starts only on explicit action. Verify ACK/readback and station state. An implicit takeover or optimistic state fails. | Yes, including Stationary source fixture | OPEN | Yes |
| PA-04 · WBM-NETZ-ACTION, WBM-ACTION-HW | Confirmed permission and supported point. Stage draft; use Start, Apply and Stop. With hardware disabled verify safe Stop over pending drafts. | Check requested/applied/actual and WBMGR NETZ transitions. Only approved drafts enter execution, and Stop withdraws release without approving pending settings. Unexpected positive command fails. | Yes | OPEN | Yes |
| PA-05 · WBM-TIME-05–13/25–28, WBM-GRID-13 | Valid NETZ schedule and operator-controlled connection interruption. Observe delay, edit within the lease, reconnect in-window and after expiry. | Timestamp absolute deadlines and readback. Same-ID resume reconciles, expired release does not restart, and a second run is explicit. Shifted or reused deadline fails. | Yes, fake clock and OCPP | OPEN | Yes |
| PA-06 · WBM-GRID-08/31–39, PV-LOCK | Valid station caps and supported phases. Use nominal steps, disable one mode with 0 A, and observe Stationary's supported phase/re-enable lockout handling during ordinary operation. | Record capability, limits, proposals, lockout result, confirmation and measured current. Requests stay in policy caps and honor atomic reject/wait. Inferred application or repeated wrong-mode commands fail. | Yes | OPEN | Yes |
| PA-07 · WBM-ARCH-07/11, current session contract | Valid journal, EV and Stationary. Observe plug, charging, pause, disconnect, legitimate resume and unplug. | Same-ID continuity where Stationary resumes, authentic Ended, no invented start/meter or attribution across scopes. Record session fields, time and registered generation. | Yes | OPEN | Yes |
| PA-08 · PV-ARCH-02 | Battery-free profile configuration, grid import/export and valid EV observation. Select SURPLUS and explicitly Start under ordinary export and reduced export. | Verify selection independent of battery and forecast, controlled steps and zero pause with release retained. Capture signed grid and measured EV power. An invented battery dependency fails. | Yes | OPEN | Yes where used |
| PA-09 · PV-SOC-01–03, PV-PLAN-01–04 | Valid reserve/SoC and forecast/capacity for OPTIMUM. Inspect each configured battery profile before Start and through ordinary SoC/forecast changes. | Compare canonical T, SoC, mode and availability. Verify independent planning and approved strict/inclusive transitions. A stale or fabricated target fails. | Yes | OPEN | Yes where used |
| PA-10 · PV-FAST/BAL, PV-RESP, PV-BAL-05 | Valid signed sources and responding EV. Observe normal FAST/BALANCE transitions and both directions of response in controlled conditions. | Capture Level 2 source context, phase, proposal, acceptance, offer and EV settling. Verify causal direction and pacing against requirements. Operator must agree any quantitative performance thresholds separately. | Yes for policy and simulated timing | OPEN | Yes |
| PA-11 · PV-ECO-03–09 | Authorized PV run and suitable ordinary site import conditions. Observe economic STOP, low import and an eligible restart. | Record measured import, threshold, Wh, elapsed reset and confirmed zero/EV zero. STOP is an approximate intervention. No restart at or below 15 seconds, or without other eligibility. | Yes | OPEN | Yes |
| PA-12 · PV-ECO-07, PV-OCPP-06 | Valid journal and current transaction. Use normal reconnect with same ID, then a legitimate new Stationary transaction. | Compare identifiers locally and durable Wh/STOP. Same ID retains history, new valid ID begins fresh on the first eligible cycle. Wrong scope must not roll over. | Yes, including missing prior Ended | OPEN | Yes |
| PA-13 · WBM-GRID-15, WBM-HS-UI-NETZ | Writable mapped reserve and SoC. Inspect preview, start an eligible NETZ charge, stop, and verify recovery after controlled reload and normal external edit. | Record original, planned, applied and restored values. Preview is read-only, ownership persists, external edit wins and only owned values restore. | Yes | OPEN | Yes where used |
| PA-14 · PV-ARCH-03, PV-BAL-07 | Actual Fronius/BYD entities with known units/sign and normal availability changes. Compare captured source events with controller snapshots. | Positive charge/negative discharge interpretation and both-positive neutral policy agree. Required missing input stays unavailable. Silence alone does not expire held data. Record actual source timing without adding sampling. | Yes for rules; actual cadence separate | OPEN | Yes where used |
| PA-15 · WBM-HS, WBM-DISP, WBM-PV-TARGET | Current Lovelace resource and entry-bound card. Inspect desktop/mobile, language, status, actions and target after installation/reload. | Verify correct instance, accessible directions, one/two-decimal formatting, enabled state, warnings and no overflow. Record screenshots and resource digest. | Yes, 95 regressions and Chrome | OPEN | Yes |
| PA-16 · WBM-ARCH LAN decision | Trusted LAN and authenticated VPN. Operator verifies listener reachability from intended networks and inaccessibility from untrusted/public networks. | Document topology and reachability, including station-path routing. Public listener exposure fails the supported boundary. | Partial, loopback routing only | OPEN | Yes |
| PA-17 · V04-12, WBM-ARCH-12, PV-DIAG | Comparable longer enabled/disabled HA intervals, representative steady and reconnect traffic, normal Recorder filters. | Record CPU and actual state/Recorder behavior. Passive average increase at most 0.1 percentage points per V04-12. Check identical observations do not create redundant records. Automated call counts are a proxy only. | Partial, publication counters | OPEN | Yes |

## 14. Beta.23 readiness

**Conditional GO for a controlled HACS field-testing prerelease**, subject to
separate authorization and an exact candidate review. No P0 behavior was
demonstrated. RR-01 matters for existing installations with questionable
session history. Before installation use a documented first setup or verify
the expected current schema-2 journal, absence of unresolved quarantine,
and a restorable complete backup. If those prerequisites cannot be met,
resolve RR-01 before that installation. A schema check alone cannot certify
historical completeness. Provide the operator with the limitation and
rollback boundary. Resolve how to handle abrupt-loss exposure before any
testing that requires replay protection across that condition. The PA matrix
is the purpose of controlled field testing.

**Established release procedure, for a separate authorized task.** Pin the
reviewed candidate base, verify release refs and authorized scope, prepare an
isolated candidate branch, and review the manifest version and release notes.
Run the full suite and static/frontend/docs checks, then verify exact
candidate CI. Reconcile authorized `develop` and `main` history without
dropping reviewed parents and confirm their trees match the verified
candidate. Gate each exact pushed SHA before an annotated tag and exact tag
CI. Only then publish a GitHub prerelease from the standard tagged source
archive. HACS selection, backup, package/manifest/card verification and
installation/restart belong to the operator. Retain the pre-upgrade backup
and matching release. A package downgrade cannot recreate data lost or
changed after the backup. The beta.22 validation and published release
record candidate/develop/main/tag gates, and GitHub lists v0.3.1 as latest
stable. Beta.23 is a proposed version only.
[Workflow](../../.github/workflows/tests.yml) ·
[Beta.22 validation](release-validation-beta.22.md) ·
[Published beta.22 and exact gates](https://github.com/ekkehard-lutz/wallbox-manager/releases/tag/v0.4.0-beta.22) ·
[Stable release](https://github.com/ekkehard-lutz/wallbox-manager/releases/tag/v0.3.1)

## 15. Stable v0.4.0 readiness

**NO-GO.** Resolve RR-01, explicitly decide durability and total-loss
expectations in RR-02, record the two PV policy decisions, and close the
applicable physical gates against the exact release candidate. Accept or
harden the confirmed pre-registration behavior independently. Define
quantitative FAST/BALANCE tolerances only with operator approval and field
evidence. Measure long-running replay-set cost before considering any change
to its complete retention. No HACS package or stable readiness claim follows
from CI alone.

## 16. Prioritized findings and operator decisions

| ID / class / confidence | Requirement and evidence | Consequence and preconditions | Minimal action / decision | Beta blocker | Stable blocker |
| --- | --- | --- | --- | --- | --- |
| RR-01 · P1 · high | WBM-ARCH-07/11, current session replay contract. `session.load:280`, HA quarantine and wire probes in sections 5–6. Permanent tests cover valid replay and unsupported schema. | Detectable damage or incomplete payload erases the replay fence. A fresh scoped retired ID can re-enter session and transaction eligibility. | Approve artifact-aware validation and fail-closed recovery or a precisely defined degraded mode. Restore verified history. | No for verified controlled scope; yes for installations with unresolved journal state | Yes |
| RR-02 · P1 exposure / OPEN durability policy · high for window, unverified field crash outcome | WBM-ARCH-11, current persistence contract. `session.load:296`, `EntryRuntime._close:45`, HA write implementation and two-instance probe. Graceful lifecycle tests exist. | Total disappearance or a valid old snapshot can lose history even after RR-01 detection. Requires loss of all evidence or a pending/non-durable save. | Decide supported crash/restore guarantees. If closure durability is required, design and test the minimum terminal checkpoint or external evidence; preserve ordinary sparse writes. | No for controlled testing outside lost-history/crash acceptance; undetermined otherwise | Yes, resolve durability scope |
| RR-03 · P2 · high | WBM-ARCH-07/11 and V04-09. `Adapter._handle_call:326`, `Adapter.transaction:398`, pre-Boot probe. | Nonconforming peer mutates pre-Boot session history. Current Grid command gate still blocks. Normal Stationary register order avoids the probe condition. | Decide whether to reject transaction attribution before registration. If approved, use one narrow guard and a wire regression. | No | Undetermined, explicit disposition |
| RR-04 · OPEN · high for implemented behavior | PV-TUNE-09 and PV-ECO-04/07/08. `PV.tick:586`, `Budget.exhaust:116`, runtime and temporary budget probes. | Edits preserve Wh and latch. Lower limit acts at the next eligible tick; raising does not withdraw pending STOP. | Approve or revise the documented policy. If approved, add dedicated regressions for pending STOP ACK plus edit/reload. | No | Yes, policy sign-off |
| RR-05 · OPEN · high | PV-TUNE-10, V04-04/05. `Settings.__init__:107`, `_save:883`, current-entry and PV-runtime tests. | Inert keys persist in some current records. No operational consequence demonstrated. | Accept preservation or explicitly authorize separate removal. | No | Yes, dispose of the historical deletion clause |
| RR-06 · P2 acceptance dependency / OPEN evidence · high | V04-09/12, PV-OCPP-06, PV-QA-03 and the PA matrix. Source and automated evidence mapped above. | No complete physical acceptance for this baseline. This includes network and Recorder commissioning. | Execute the separately authorized PA plan and record gaps. | No, purpose of controlled beta; LAN and backup prerequisites still apply | Yes |
| RR-07 · ACCEPTED · high | PV-ECO-07/09, WBM-ARCH-01/05. `Budget.bind/sample`, current rollover and permission tests. | Private optimization allows approximate Wh detection and a new budget for a valid new transaction, including after interrupted scope. | Keep the approved limitation. Validate field timing without inventing a billing or safety promise. | No | No |
| RR-08 · OPEN · high for retained-set semantics, unknown installation cost | WBM-ARCH-12, V04-12 and current complete replay contract. `session.document:300` sorts and saves every retired ID. Repeated-restart tests check retention. | Journal and in-memory set grow with retired IDs. No measured unacceptable installation cost was found. | Measure after sustained use. Any storage change must retain the approved replay fence. | No | Undetermined, include in PA-17 |

## 17. Recommended minimal follow-up tasks

1. Independently review RR-01 and agree its operator recovery path. Implement
   narrowly scoped detectable-damage handling with schema-2 and replay
   regressions on a separate branch.
2. Decide RR-02 durability scope and RR-03 pre-registration attribution.
   Keep any additional implementation tied to the agreed guarantee and
   evidence. Do not import old journals or reconstruct retired IDs.
3. Record the PV-TUNE-09 and PV-TUNE-10 decisions. If the observed behavior
   is accepted, preserve runtime behavior and add only the targeted
   acceptance evidence needed by that decision.
4. Separately review and authorize a candidate and PA evidence collection
   for the intended installation. Keep physical acceptance and publication
   gates separate.

## 18. Validation results and limitations

Phase A local `python -m pytest -q` passed **1,162 tests** at **95%**
statement coverage (4,393 statements, 217 missed). Five deprecation warnings
came from HA HTTP and Backoff. Ruff lint and format, JavaScript syntax,
95 frontend regressions, headless Chrome, manifest/HACS, localization parity
(152 keys per file), 533 local links/anchors and Git whitespace passed.
Exact merge CI succeeded.

Phase B focused selection passed **416 tests** with the same five warnings.
It covered session boundaries, OCPP protocol and observations, active control,
F1/F5, PV recovery/rollover/permission intervals, teardown, NETZ resume and
release/approval, binding and registry. The 25 temporary checks passed with
five dependency warnings. They include cold Store cases, real permission
denial in a temporary directory, prior replay observations, pre-Boot scope,
unflushed persistence and the budget edit scenario. The transient fixtures
were kept outside the repository. The injected delayed response models WBM
consuming an actual simulated zero reply late. It does not model all physical
transport delays.

Phase B full `python -m pytest -q` passed **1,162 tests** at **95%**
statement coverage (4,393 statements, 217 missed) with the same five warnings.
The full run used the local Stationary source fixture. Ruff lint and format,
JavaScript syntax, all 95 frontend regressions, the headless Chrome layout
fixture, manifest/HACS checks, localization parity (152 keys per file), 609
local links and anchors across 127 Markdown files, and Git whitespace checks
passed.

No physical installation, hardware log, real power-loss test or field clock
skew test supplied evidence. A valid-looking stale journal cannot establish
the completeness of earlier history. The audit adds this Markdown report
only. Runtime, permanent tests, requirements, release metadata, Stationary
and deployed systems remain as they were at the baseline.
