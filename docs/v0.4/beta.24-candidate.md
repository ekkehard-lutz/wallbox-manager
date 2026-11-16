# Wallbox Manager beta.24 implementation candidate

Baseline `a19c457091ab093f4d6012db9cc9805d12a8a43a` on `develop`. Operator evidence and approvals came from the
[beta.23 field acceptance register](https://github.com/ekkehard-lutz/wallbox-manager/blob/a4d5d9cc19a833c8246d4921caab6b8da1a544f0/docs/v0.4/field-acceptance-beta.23.md).

| Item | Candidate change |
| --- | --- |
| F01 | The duration sensor supplies native seconds. Home Assistant can convert the numeric state to an entity-selected duration unit, and the beta.23 card always divided by 60. The card now converts using the exposed unit before flooring complete minutes. Native seconds and the reported value of 1.77574393916667 in hours both render a corresponding 1 h 46 min session as `1:46`. The field log omitted the unit attribute. |
| F02a | A schedule read could be rejected during Stationary startup or fail WBM validation when the separate phase Actual was still unavailable. Beta.23 had no event-driven retry for its `unconfirmed / readback_unknown` outcome on phase or charging-state arrival. WBM now uses the existing scoped phase read when needed and rechecks on new discovery, phase, charging or permission evidence. A missing permission baseline alone sends no zero profile during an active authorized window. A matching offer needs no write. A differing positive offer can be reconciled to the still-authorized request using the confirmed current phase and existing fences. Unknown readback stays unknown. |
| F02b | The reserve controller passed a restoration request when point confirmation disappeared, even with a valid, authorized charge drawing power. The existing owner survives that uncertainty and incomplete lifecycle observations. Actual cessation, explicit Stop, permission or authority loss, profile changes and owner retirement still follow the compare-before-restore path. The first field restoration preceded the boot. The exact predicate values for both restorations are unknown. Level 2 diagnostics now include the restoration decision. |
| F03 | The same backend gate covers the card action, explicit service and legacy button. NETZ Start/Apply require registered Remote authority and observed hardware permission. Local drafts remain editable. Remote NETZ can pre-arm without a transaction. A concurrent authority change invalidates approval, and Stop can retire the release even when dispatch is unavailable. |
| FR-01 | The existing scoped Settings document holds the durable approval and original deadlines across an HA restart. Recovery requires new Stationary observations, including a scoped transaction and supported offer. Expired and explicitly retired releases follow zero reconciliation. Integration reload still retires live release. |
| FR-02 | NETZ uses fixed nearest over legal nominal 230 V offers with confirmed phase retention in overlaps. Exact legal technical navigation hints carry an explicit phase selection. The exact beta.23 scoped select and the legacy `direction` preference are removed. Existing cap, navigation, lockout and draft behavior is covered by the NETZ regressions. |

## Restart behavior and boundaries

- Startup resets browser-editor ownership and retains the approved power and the
  absolute start/end. An unlimited release retains its unset end. An invalid or
  tentative stored release starts OFF. Earlier unmarked records require a new
  explicit Start. The candidate marks eligible approvals in the existing document.
- A previously running window requires a positive scoped offer on the recovered
  transaction. A proven cable absence permits a fresh transaction when the car is
  plugged back in. Before a delayed window first attempts positive dispatch, WBM
  records that window in Settings. An unattempted delayed start can still run
  within its original window when HA was offline at the start, with a current
  zero read and the original transaction or proven cable absence. An attempted
  window with a zero or unknown offer remains blocked absent fresh cable evidence
  or a new operator action. Confirmed Local, disabled permission during recovery,
  or a scoped Stationary Stop retires the saved release.
- Orderly HA stop fences this runtime, closes the OCPP listener and saves the
  session journal. It sends no shutdown profile. Stationary transport cleanup
  cancels pending protocol work. A previously applied hardware offer can remain
  active. Stationary owns actual execution and there is no deadline enforcement
  by WBM during downtime. An explicitly retired release reconciles a confirmed
  retained offer to zero once current authority and transaction permit it.
- Recovery uses verified persisted settings. A storage failure or process loss
  before a revocation reaches durable storage can leave the prior disk snapshot.
  Treat that as a failed retirement and inspect the current Stationary state.
  The existing RR-01 replay/history P1 and RR-02 durability boundaries are
  tracked separately in the [readiness audit](release-readiness-audit.md#16-prioritized-findings-and-operator-decisions).
  NETZ continuation depends on the existing journal for same-transaction proof.

## Source checks

- Home Assistant Core 2026.8.0's installed `components/sensor/__init__.py`,
  `components/sensor/const.py` and `util/unit_conversion.py` show the entity
  unit selection, numeric state conversion and supported duration units.
  `core.py` sets the stopping state before firing the integration stop event.
- Stationary revision `279287c3c1d8863e803144eccf773ab3f10889a5`.
  `ocpp_runtime/station.py` and `domain/wallbox.py` show the instantaneous
  composite offer, physical phase feedback and possible unavailable results
  during startup, faults or hardware work. `ocpp_runtime/runtime.py` and
  `ocpp_runtime/transactions.py` show reconnect, same-ID TxResumed, and the
  RemoteStop Ended/suppression path. `api/rpc.py` and
  `ocpp_runtime/device_model.py` show the local disable button and the shared
  observed hardware permission. Confirm the installed Stationary build before
  drawing conclusions from those source paths.
- openWB/core revision `849340ec8d91f4e7cca08a7664cf9ec0187fb496`.
  Chargepoint plug checks, interlocks and the unplug-triggered OCPP transaction
  stop support the Stationary responsibility boundary. [Pinned chargepoint source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py#L154-L188)
- WBM `v0.3.2-beta.18`, `87963014c9af73416c438807f523a3da2a98bd22`.
  `grid_timing.py` demonstrates one-shot absolute deadlines and
  `session_storage.py` demonstrates orderly journal flush. The candidate
  extends the current runtime and Settings document.

## Automated validation

- `python -m pytest -q` passed 1,188 tests with five dependency deprecation
  warnings and the mocked Stationary source fixture enabled through
  `WB_SOURCE_ROOT`. The final test-only synchronization change also passed
  its focused expiry test.
- `ruff check .`, `ruff format --check .`, card syntax, all 96 card unit
  regressions, and one headless Chrome layout test passed.
- Manifest and HACS metadata, 148 matching translation keys per locale,
  service fields, changed-document local links and Git whitespace passed.

## Operator retest

1. Capture duration state and displayed unit while charging and after Ended.
   Check the card against elapsed time.
2. Reconnect or restart Stationary during an authorized NETZ session. Capture
   transaction ID, generation, composite and phase Actual responses, offered and
   measured current, Fronius reserve and the restoration diagnostics. Compare a
   matching retained offer with an unavailable readback. Verify restoration on
   Stop and that an external reserve edit wins.
3. Start an authorized finite, delayed or unlimited NETZ window. Restart HA
   before the start, within the window, and after a missed end. Compare the
   original timestamps. Exercise Stationary disable, Local and manual Stop
   while HA is offline, then retest Stop with no dispatch path.
4. Check the retired select on the HA device page, 1p/3p overlap and both
   navigation boundaries, the actual Stationary lockouts, and an ordinary
   battery-equipped PV charge. Inspect the iPhone layout after the card reloads.
