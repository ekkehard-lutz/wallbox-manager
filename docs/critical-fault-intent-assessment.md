# Critical-fault intent assessment

**7 November 2026 · F5 · Recommendation: CHANGE REQUIRED for pending WBM policy intent.**

The reviewed Stationary can report `Faulted` without revoking Remote authority,
turning off hardware permission, ending the transaction or replacing the OCPP
connection. WBM publishes the fault but leaves the command epoch valid. In a
simulated replay, a queued positive proposal reached the wire after that
report, and a valid positive readback captured before the fault could be
processed as confirmation after it. This demonstrates a policy-intent
invalidation gap. Physical or electrical behavior was outside the replay.

The assessed WBM baseline is develop at
`4998bdf480e2ddd057f0e68b4384980ed4889cbe`, the exact F3 merge with successful
[merge CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42405718288).
The Stationary checkout was read at
`279287c3c1d8863e803144eccf773ab3f10889a5`.

## Normative scope

[WBM-ARCH-01–12](specifications/architecture-responsibility.md) takes
precedence. Stationary owns contactors, CP, electrical fault protection,
safe recovery and acceptance of every physical proposal. WBM owns policy
intent, authority, transaction and command scoping, and truthful
request/ACK/confirmation distinctions. Ordinary observations are last-value-held
within their generation. The supersession register removes manager-side
physical proof, per-dispatch safety checks and freshness requirements while
retaining intent fencing.

[PV-ARCH-07](specifications/pv-regulation.md#pv-arch-07--immediate-safety-and-asynchronous-ocpp-execution-approved)
calls for prompt invalidation independent of the PV tick when a critical
fault is reported. Its precedence paragraph explicitly retains prompt
authority, connection and intent fencing. On that narrow reading, an explicit
fault that represents Stationary's `EV_FAILURE` should invalidate the pending
positive WBM decision and its result context. An existing observation can
remain historical evidence. A fault report does not prove 0 A, end a session,
revoke authority or change hardware permission. The historical safety wording
in PV-QA-02 row 16 remains superseded. Neither the older wording nor the
November LAN decision expands WBM's physical responsibilities. This
assessment leaves the requirement text intact.

## What Stationary reports

| Signal | Verified meaning and transition |
| --- | --- |
| `StatusNotification(Faulted)` for EVSE 1, connector 1 | `domain/wallbox.py:365–381` derives the fault flag from the EVSE's `EV_FAILURE`. `ocpp_runtime/station.py:75–86,341–345,364–373` maps it to `Faulted` and reports connector-status changes. A positive fault takes precedence over phase-switch status. This is the explicit connector-level signal available on the inspected interface. |
| `StatusNotification(Unavailable)` | The same mapping also uses `Unavailable` for unknown fault state, phase switching and missing connection information. This is an ambiguous availability report. |
| `TransactionEvent(Updated)` or no event | `services/evse_service.py:272–312` feeds independent connection evidence into `state/connection.py:18–33`. Fault/unknown states break READY debounce and can retain the connected cable. `ocpp_runtime/transactions.py:97–108,132–173` can then report `Updated/EVConnected` under the same transaction if prior charging changes to false. If effective charging state stays the same, it can send no event. An active transaction ends on observed departure or an existing ending reservation. |
| Authority and permission | `domain/wallbox.py:369–381` sources permission and authority from fields independent of the fault flag. `ocpp_runtime/station.py:647–661` reports their own changes as hard-wired observations via `ocpp_runtime/authority_notifications.py:12–23`. Faulted status does not itself write either field or reset transport. |
| Command-specific failure | `ocpp_runtime/station.py:559–608` returns reasons such as `PhaseSwitchLockout`, `ReenableLockout`, `RestoreFailed` or other phase-service reasons, and can return generic `Rejected`. These are outcomes of a particular request. `services/phase_switch.py:63,91–145,148–196` covers several distinct failure stages. F1 already treats ambiguous outcomes conservatively. A rejection or lack of readback is insufficient to classify a connector-level critical fault. |
| Composite readback during an observed fault | `ocpp_runtime/station.py:505–545` rejects with `StateUnavailable` when the acquired snapshot has a true or unknown fault. The same reason covers other states. An earlier healthy response can already be in flight. `domain/wallbox.py:303–320` fences the composite observation against ongoing commands and uncertain phase feedback. |
| Emergency input, CP/phase observations and meter errors | `services/evse_service.py:277–285` samples the emergency register separately from EV state. `domain/wallbox.py:825–831` uses that state in local recovery. The inspected `ocpp_runtime` emits controller/phase `NotifyEvent` payloads and does not provide a dedicated emergency or granular fault notification. An unknown phase, failed acquisition, or command-specific failure does not imply the `Faulted` observation. |

`Station._matches_transaction()` checks active/non-ending transaction, ID and
confirmed cable connection (`ocpp_runtime/station.py:499–503`).
`SetChargingProfile` also checks Remote, passes a live transaction guard to
the Domain and maps returned errors. Neither the transaction predicate nor
the transport handler makes a connector fault a transaction-end signal.
`domain/wallbox.py:252–274,665–742` separates capability validation, the
command lock, readiness, authority and transaction checks. This audit cannot
infer actual contactor or EVSE behavior from those paths.

A pure source probe used Stationary's `VehicleConnection`,
`observe_connector()` and `TransactionObserver` with an in-memory store.
It retained the cable on `EV_FAILURE`, produced `Faulted`, and yielded
`Updated/EVConnected` for the same transaction after previously charging.
Repeating the failed state yielded no event. Remote and permission remained
on in that snapshot. The probe performed no Stationary I/O.

## What WBM does

- [OCPP StatusNotification](../custom_components/wallbox_manager/ocpp.py#L354)
  supplies a scoped, timestamped observation. The
  [canonical Store](../custom_components/wallbox_manager/store.py#L111)
  rejects stale/wrong-scope evidence and
  [publishes an active `wallbox_fault` message](../custom_components/wallbox_manager/store.py#L210).
  Connector presentation and the separate transaction charging-state
  projection do not synthesize a permission, authority, transaction or
  physical-current transition.
- [Grid subscriptions](../custom_components/wallbox_manager/grid.py#L72)
  route the message to `publish()`, not `invalidate()`. OCPP also publishes
  discovery on a connector change, but the
  [fingerprint](../custom_components/wallbox_manager/grid.py#L761) excludes
  connector and charging status, so `changed()` returns if those alone
  changed. [Eligibility and validity](../custom_components/wallbox_manager/grid.py#L736)
  use registration, adapter, Remote, transaction and epoch. The
  [wire guard](../custom_components/wallbox_manager/grid.py#L1096) also checks
  release, timing, applicable phase-lockout scope and observed permission for
  a positive point. A `Faulted` report alone changes none of those fences.
- [PV lifecycle](../custom_components/wallbox_manager/pv.py#L269) tracks
  profile, release, observation generation and transaction. Its
  [post-persistence revalidation](../custom_components/wallbox_manager/pv.py#L877)
  uses the same Grid validity. A fault-only report can therefore leave a
  previously calculated decision valid across that await. The simulated
  dispatch result below uses the shared executor used by NETZ as well.
- [Outcome processing](../custom_components/wallbox_manager/grid.py#L1311)
  rechecks the epoch after the command response. It separately records ACK,
  reads the phase and composite offer, and
  [checks validity again](../custom_components/wallbox_manager/grid.py#L1454).
  An ACK alone never creates applied current. An earlier matching valid
  composite result can still complete the old intent after a fault-only
  report because its epoch is unchanged. A late ACK can also reach
  `PV.accepted()` while the old intent remains valid. That method can
  commit an authorized economic restart if one is pending
  (`pv.py:540–550`). The economic restart side effect was outside the replay.
- [Permission loss](../custom_components/wallbox_manager/grid.py#L208)
  invalidates pending approvals and dispatch. Remote-to-Local, transaction
  lifecycle, profile/release changes and transport/boot generation changes
  also fence old results. [`control.py`](../custom_components/wallbox_manager/control.py#L106)
  separately handles authority and permission intents through their own
  readbacks. A connector fault does not modify those independent intent
  contracts.

## Simulated reproduction

The replay ran from an isolated archive of the exact develop baseline, using
the existing `PhaseStation` and `peer` fixtures. It sent a scoped OCPP 2.1
`StatusNotification(Faulted)` on localhost. It controlled response ordering
at the existing adapter request boundary. The replay was temporary and no
test files were added to or edited in the branch. All 15 combinations of
three pause points and five observation sequences reproduced the outcomes
below.

1. Establish a confirmed `1p/20 A` point with PV release on, observed hardware
   permission true, Remote authority, active transaction and stable boot
   generation. Queue a `1p/18 A` proposal.
2. Pause the adapter in one of three places. Pause before the positive
   SetChargingProfile reaches its wire guard, after the peer's ACK has been
   received but before WBM processes it, or after a matching healthy
   GetCompositeSchedule response has been received but before WBM consumes it.
3. Deliver `Faulted`. Assert `wallbox_fault` is published while the epoch,
   transaction, generation, hardware permission and release still match.
   In one variant also deliver same-transaction `Updated/EVConnected`.
   For comparison, deliver a permission loss, Local authority observation
   or transaction `Ended` after the fault in separate variants.
4. Resume the pending await. For the pre-wire case the fake station rejects
   any subsequently transmitted proposal. For post-ACK cases a new
   composite request is unavailable. The already acquired composite in the
   last case represents the pre-fault snapshot.

| Paused point | Fault only or fault plus same-transaction update | Fault followed by a separate control/lifecycle transition |
| --- | --- | --- |
| Before the positive write | The old proposal passes the wire guard and is submitted. Stationary's simulated rejection is reported truthfully. | The old positive write is refused. |
| After receipt of ACK | WBM records accepted-unconfirmed. The subsequent rejected read supplies no applied point. | The delayed ACK cannot advance the old intent. |
| After receipt of a matching pre-fault readback | The old epoch can consume the result and publish `confirmed` at 18 A after the fault. | The delayed readback cannot confirm the old proposal. |

The replay confirms a missing fault-only policy fence. It does not
demonstrate a faulty stationary readback, an ACK substituting for readback,
or a hardware protection failure. The ordering is simulated. The assessment
makes no claim about the frequency of that ordering in an installation.
The policy expectation under the narrow PV-ARCH-07 reading is to prevent
the pending positive intent from being issued or completed after WBM has
accepted a scoped critical-fault report, regardless of whether the station
also emits an authority, permission or transaction transition.

Existing WBM tests
[`test_active_messages.py:156`](../tests/test_active_messages.py#L156)
check warning precedence and clearing.
[`test_netz_lifecycle.py:79`](../tests/test_netz_lifecycle.py#L79),
[`test_active_control.py:439`](../tests/test_active_control.py#L439),
[`test_protocol.py:483`](../tests/test_protocol.py#L483),
[`test_ocpp_rejection_certainty.py:222`](../tests/test_ocpp_rejection_certainty.py#L222)
and [`test_beta22_profile_integration.py:161`](../tests/test_beta22_profile_integration.py#L161)
cover permission, authority, replaced generation, transaction/profile and
late-outcome fencing. They do not establish fault-only invalidation.
Companion tests at the pinned revision cover status/permission independence
(`tests/test_ocpp_station.py:24–60,142`), composite fault rejection
(`tests/test_ocpp_composite_schedule.py:133`), and a profile failure that
preserves transaction identity (`tests/test_ocpp_charging_profile.py:420`).

The historical WBM tag `v0.3.2-beta.18`
(`87963014c9af73416c438807f523a3da2a98bd22`),
`docs/phase-lockout-protocol.md:16–30`, separates station-side hardware
guarantees from WBM interpretation of protocol outcomes. The openWB source
cross-check used `849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
`packages/control/chargepoint/chargepoint.py:126–175,177–188`, for local
charging eligibility and the separately triggered transaction-stop path.
Its fault/transaction contract does not govern this Stationary interface.
[Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py)

## Branch validation

The existing `python -m pytest -q` suite passed with **1,061 tests** and five
dependency deprecation warnings. Ruff lint/format, JavaScript syntax, **95**
frontend tests, and the headless Chrome layout/action fixture passed. The
documentation review checked 120 local links and anchors across the six changed
documents, including the approved network decision and its commissioning
references. Git diff whitespace checks passed. A byte comparison against
develop verified that every existing non-documentation file is identical.

## Recommendation and operator decision

**CHANGE REQUIRED.** Subject to separate approval, use the existing Grid
command epoch to supersede the pending positive proposal, any positive retry
of that proposal and its ACK/readback completion on the transition into a
valid, current-generation, correctly scoped `Faulted` observation. Keep
existing independent observations, explicit Stop, required 0 A policy,
hardware permission, Remote authority, economics, release state and NETZ
business deadlines. Avoid using the generic change path in a way that
immediately resubmits the old proposal or cancels an active NETZ expiry.
Add simulated ordering coverage at the pre-wire and post-response boundaries.
The fault edge itself sends no command and adds no polling or safety timer.

Before implementation, the operator should confirm that this Stationary
`Faulted` mapping is the WBM policy trigger under PV-ARCH-07. Separately
decide whether a later independent positive decision should wait while
`Faulted` is held, and how it becomes eligible after an explicit clear.
This assessment establishes the gap for already pending intent and provides
no evidence for automatically revoking release, synthesizing a physical
pause, imposing a recovery delay or reacting to `Unavailable` as a critical
fault. Stationary retains exclusive physical and electrical safety
responsibility.
