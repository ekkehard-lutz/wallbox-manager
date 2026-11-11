# v0.4 session recovery and registration review

## Baseline

Phase A merged reviewed audit tip `353cca5a9f299321cd642fba7a7ff4ede6814314`
into `develop` using a two-parent merge. The first parent was
`a132326b4b373e6e955ca8e04efa758f32bd93ef`. The exact merge SHA was
`59b6c234dcde4e2eb27bc330abc5c2244800e254`. Its only added file was the original
audit, including its findings and reproductions. Phase B started at the exact
merge SHA on `fix/v040-session-recovery-registration`. [Original audit](release-readiness-audit.md) ·
[Reviewed tip CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42898260057) ·
[Exact develop merge CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/43003147539)

## Findings and approved dispositions

| Finding | Original observation | 11 November operator disposition |
| --- | --- | --- |
| RR-01 | A missing, incomplete or damaged session journal could silently lose replay history or fail setup. | Preserve the damaged bytes, recover trustworthy schema-2 information and continue after verified normalization. Lost history can reduce replay protection and informational session-statistics accuracy. |
| RR-02 | Abrupt power loss can leave no file or a valid older snapshot. HA Store can also log an ordinary write failure without raising it. | Accept the existing one-second delayed save and graceful teardown flush. Session energy is informational household data. |
| RR-03 | A correctly routed peer could mutate session state before BootNotification registration. The command gate still blocked positive dispatch. | Require current adapter registration for transaction attribution. |
| RR-04 / PV-TUNE-09 | Existing budget edits preserve Wh and STOP intent. Formal policy approval was open. | Approve the existing algorithm, including edits while the zero-current acknowledgement is pending. |
| RR-05 / PV-TUNE-10 | Stored obsolete keys were inert. The earlier physical deletion clause was open. | Keep harmless stored keys inert, with current scoped settings and precedence. |
| RR-08 / R4 | The complete known closed-ID collection grows with use. | Retain all known IDs without size or age pruning. |

## Session journal recovery

The entry still uses `wallbox_manager.<entry_id>.passive_session`, HA Store
wrapper version 1 and payload schema 2. Current snapshots contain the latest
session or `null` and the sorted set of known closed IDs. An old serialized
`generation` is accepted and invalidated in memory. Current snapshots omit
it. Historical meter generation metadata stays readable and supplies no
live boundary evidence on a new generation.

| Startup state | Result |
| --- | --- |
| No current file or quarantine | Ordinary first setup with no historical session, empty closed history, release OFF and no recovery warning. Complete loss of all artifacts has the same observable state. |
| Valid current schema 2 | Restore the record and every known closed ID. The startup read leaves the bytes intact. Older archives are historical artifacts. |
| Incomplete schema 2 | Preserve valid closed IDs and usable session fields. Unknown measurements stay unknown. A consistent end timestamp, scope and external ID can recover a closure even if the record cannot be displayed. Discard sessions whose structural fields cannot support truthful attribution. Archive the input and save normalized schema 2. |
| Damaged JSON or invalid HA envelope | Preserve the original and write an empty current schema-2 snapshot. If HA already quarantined the file and the live name is absent, retain that existing artifact. |
| Unreadable source, unsafe archive or failed initial save | Log an entry-scoped error and fail setup before the listener starts. The live source and any already created archive remain available if the replacement write fails. |
| Unsupported payload schema | Fail explicitly and preserve the source. The retired `.sessions` ledger and schema 1 remain outside the supported path. |
| Valid but stale snapshot | Load normally. Its structure provides no evidence of recent unsaved events or missing closed IDs. |

The startup reader inspects the envelope through an executor using HA's JSON
decoder. This avoids HA Store's ambiguous missing result and a minor-version
rewrite before evidence is preserved. WBM archives a live file with a
same-directory hard link named `<key>.corrupt.<UTC timestamp>.<UUID>`. The
unique filename prevents clobbering earlier evidence. A retry reuses an
existing link to the same source inode. A filesystem that cannot create the
archive fails setup with the source intact. HA Store replaces the live name
using its existing same-directory temporary-file write. WBM reads the current
snapshot back once before logging success, since `Store.async_save()` can
return after a write error. The success warning identifies the entry, reason
and artifact and describes possible loss of replay and statistics history. A
subsequent valid startup ignores old artifacts. Archives are retained.

The existing one-second delayed save, complete known-ID serialization,
graceful teardown save and sparse entity publication remain in effect.
Recovery performs its extra filesystem work at startup. A lost closed ID
can re-enter through the ordinary registered transaction path if it passes
the current scope and time checks. Known same-ID transactions remain subject
to their sequence checks. History loss leaves unknown IDs and measurements
unavailable. An open restored record stays historical until
valid current-generation evidence confirms it.

## Registration and charging policy

Before this change, a pre-Boot Started or Updated could create or replace a
session, and Ended could alter closed history for a current session. The
handler now returns the ordinary TransactionEvent result before reading or
changing transaction state when the adapter generation has yet to register.
Bootstrap, subsequent registered events and same-ID resume follow the existing
path. Reconnect and BootNotification still advance generation and fence older
evidence.

Reload begins with manager release OFF, which blocks positive NETZ and PV
proposals. An explicit Take Control action is required to request Remote
authority. Observed authority, hardware permission, transaction and command
fences, and the current NETZ/PV
eligibility rules continue to govern requests. Stationary owns electrical and
physical protection. [Architecture](../specifications/architecture-responsibility.md) ·
[Charge release](../specifications/charge-release.md)

PV budget edits retain accumulated Wh and leave pending or latched economic
STOP intact. A lower threshold can cause STOP on the next eligible ordinary
tick. Edits grant no release and trigger no extra PV cycle. The existing
reset, restart, same-ID recovery and new-ID rollover rules still apply.
Harmless retired keys may remain stored and are inert in decisions. PV
Regulation V3 remains DRAFT overall. [PV-TUNE-09 and PV-TUNE-10](../specifications/pv-operating-point-response.md)

## Source cross-check

- HA Core **2026.8.0** as installed by the development dependency pin.
  Inspected `homeassistant/helpers/storage.py` (`Store._async_load_data`,
  `async_save`, `async_delay_save`, `_async_handle_write_data`,
  `_write_prepared_data`), `homeassistant/util/json.py` (`json_loads`,
  `load_json`) and `homeassistant/util/file.py` (`write_utf8_file`). Tests
  exercise real Store quarantine and the swallowed replacement failure.
- WBM `v0.3.2-beta.18` at `87963014c9af73416c438807f523a3da2a98bd22`,
  `custom_components/wallbox_manager/session_storage.py` lines 1–36. The
  historical ledger used a one-second delay and a teardown flush. The
  current lifecycle retains these behaviors.
- Wallbox Stationary `279287c3c1d8863e803144eccf773ab3f10889a5`,
  `ocpp_runtime/station.py` lines 261–304 and
  `ocpp_runtime/transactions.py` lines 44–95, 132–174 and 214–237.
  Stationary starts transaction tasks after accepted registration and
  reserves sequence numbers before sending. Reconnect can resume the same
  UUID with a higher sequence.
- openWB/core `849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
  `packages/control/chargepoint/chargepoint.py` lines 154–188. The upstream
  charge point evaluates its local interlocks and initiates OCPP stop on
  unplug. This comparison corroborates the station-side responsibility
  boundary. Wallbox Stationary supplies WBM's actual OCPP interface.
  [Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py#L154-L188)

## Files and regression evidence

- [Session runtime](../../custom_components/wallbox_manager/session.py)
  validates and recovers the current journal at setup.
  [Journal recovery regressions](../../tests/test_session_journal_recovery.py)
  cover real Store evidence preservation, restart, errors, release fencing
  and resumed/new/replayed transactions. [Legacy ledger regression](../../tests/test_identity_migration.py)
  checks that the retired Store stays inert through both read paths.
- [OCPP adapter](../../custom_components/wallbox_manager/ocpp.py) applies
  the registration fence. [Wire regression](../../tests/test_protocol.py)
  covers pre-Boot Started, Updated and Ended on initial connect and
  reconnect, subsequent registration, energy attribution and stale events.
  Existing direct adapters in [session boundary](../../tests/test_session_boundaries.py),
  [delta](../../tests/test_delta_compatibility.py) and
  [observation](../../tests/test_observations.py) tests now establish their
  registered generation explicitly. The duplicate-Boot regression waits for
  bootstrap read tasks to drain before verifying that only the current
  permission poll remains.
- [PV regression](../../tests/test_pv_runtime.py) holds a real synthetic
  SetChargingProfile zero response while changing the threshold and checks
  STOP, Wh, threshold and scope after reload.
  [PV recovery](../../tests/test_pv_journal_recovery.py) and
  [transaction rollover](../../tests/test_pv_transaction_rollover.py)
  retain same-ID history and establish the fresh new-ID budget.
- [Current requirements](../specifications/v0.4-current-requirements.md),
  [V04-09](../specifications/passive-v0.4.md), the
  [tuning clauses](../specifications/pv-operating-point-response.md) and
  [decision history](../specifications/decision-history.md) record the
  approved dispositions.

## Validation

Phase A local Python ran 1,161 tests successfully and skipped the companion
checkout contract. The companion contract passed separately with its source
path configured and hardware mocked. Ruff lint and format, JavaScript syntax,
95 frontend regressions, headless Chrome, manifest and HACS metadata, 152
matching localization leaves and service fields, 609 local links and anchors,
and Git whitespace passed. Exact merge CI passed.

Phase B passed **1,195 Python tests** with **94.94% statement coverage**
(4,295 of 4,524 statements) on Python 3.14.7 and HA Core
2026.8.0. The run included the mocked Stationary source contract and collected
33 additional test cases over the Phase A baseline. Five dependency
deprecation warnings were reported. Focused runs passed 207 session,
registration, reconnect, entity identity and teardown cases, and 248 NETZ
release, PV recovery, budget edit and transaction rollover cases.

Ruff lint and formatting, production and test JavaScript syntax, all 95
frontend regressions, the headless Chrome fixture, manifest/HACS metadata,
152 matching localization leaves and service fields, 633 local links and
anchors across 126 Markdown files, and Git whitespace all passed. The
original audit, integration version, HACS metadata and PV runtime files have
identical content to the Phase A merge.
[Feature branch workflow](https://github.com/ekkehard-lutz/wallbox-manager/actions/workflows/tests.yml?query=branch%3Afix%2Fv040-session-recovery-registration)

## Remaining acceptance

The audit's physical acceptance matrix still applies. Physical checks include
Stationary registration and readbacks, actual NETZ release and stopping,
vehicle disconnect and same-ID resume, PV response and economic restart,
station-side enforcement, network exposure and sustained HA/Recorder
performance. All verification here used isolated HA, synthetic OCPP peers and
mocked hardware. The implementation dispositions are resolved within the
approved scope. [Physical acceptance matrix](release-readiness-audit.md#13-outstanding-physical-acceptance-matrix)
