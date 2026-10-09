# Physical status recovery and the missing station capability

## Investigation

Inspected wallbox-manager's `protocols/ocpp/common/inventory.py`,
`protocols/ocpp/v21/{adapter,enabled}.py`, `runtime.py` and the HA observation
entities. Also inspected the local wallbox-stationary checkout at
`436c4afe384ebfc45e608a71aafd410417d11fc9` (read only):

- `ocpp_runtime/station.py::_status_notifications` samples each second but sends
  StatusNotification only when connector status changes or registration restarts.
- `ocpp_runtime/transactions.py::TransactionObserver.observe` sends transaction
  lifecycle, resumed and charging-state-change events, not periodic state snapshots.
- The station routes GetVariables, SetVariables and GetBaseReport, but has no
  TriggerMessage handler. Its Device Model exposes ChargingEnabled, authority,
  physical phase feedback and electrical capabilities, not current physical
  connector presence/charging state.
- Heartbeat proves transport liveness; MeterValues proves electrical measurements;
  ChargingEnabled proves permission. None proves current physical presence.
  Occupied alone does not establish Charging versus EVConnected.

The manager publishes StatusNotification and live TransactionEvent observations
synchronously to HA. A failed permission query followed by ON previously created a
false CP epoch, invalidating status that arrived before the read. This is fixed by
tracking the last confirmed permission separately. Real OFF → ON fences remain.
Additionally, live TransactionEvent updates omitting optional EVSE information now
refresh the physical charging channel using the transaction’s previously explicit
scope, as session tracking already did. Unknown transaction IDs create no scope.
The remaining missed-event recovery cannot be implemented entirely in the manager
with this station interface. No speculative polling or cached timestamp refresh
has been added. Existing OCPP 1.6J/2.0.1 handling remains unchanged.

## Required wallbox-stationary change (not implemented here)

The smallest interoperable option is a station-driven periodic report in addition
to existing change events, approximately every 60 seconds and after registration:

1. Acquire a **fresh hardware observation**, including permission, CP validity,
   vehicle presence and charging activity. Use its acquisition time as the event
   timestamp. An old domain snapshot with a new wall-clock timestamp is not enough.
   Failed reads, interrupted CP, phase switching and uncertain presence must not
   produce newly confirmed Occupied/Available or EVConnected/Charging evidence.
2. For the actual EVSE/connector, emit StatusNotification even if unchanged:
   `{"timestamp":"<sample time>","connectorStatus":"Occupied",
   "evseId":1,"connectorId":1}` (or Available when departure is freshly known).
3. For an existing active transaction, also emit TransactionEvent with
   `eventType:"Updated"`, `triggerReason:"MeterValuePeriodic"`, `offline:false`,
   fresh `timestamp`, `evse:{id:1,connectorId:1}`, and
   `transactionInfo:{transactionId:"<existing id>",chargingState:"Charging"}`
   (or EVConnected from fresh evidence). Include the regular periodic meter sample
   with this periodic event. Allocate/persist a new monotonic `seqNo` using the
   existing serialized transaction sender. Do not restart a transaction or invent
   Started/Ended events merely to report status. Normal genuine departures retain
   their existing Ended semantics.

These event shapes already enter the manager's existing fast path; their sample
time, connection generation, channel ordering and CP fence determine freshness.
The journal/session IDs and charging controls do not participate in resync.
With no active transaction, Available can update an already-supported charging
channel to Idle. For occupied-but-suppressed transactions, StatusNotification can
refresh connector occupancy only; the station must not invent a transaction just
to refresh a charging sensor. A separate physical-state Device Model contract
would be needed for full charging snapshots outside transactions.

An optional request-driven alternative is to implement the OCPP 2.1 TriggerMessage
capability, verified against the installed OCPP schema:

- Request `{"requestedMessage":"StatusNotification","evse":{"id":1,"connectorId":1}}`.
- Request `{"requestedMessage":"TransactionEvent","evse":{"id":1,"connectorId":1}}`
  when a real transaction exists.
- Respond with `{"status":"Accepted"}` only when the corresponding new physical
  sample/report can be produced; send the freshly sampled event after CALLRESULT.
  A triggered TransactionEvent uses `triggerReason:"Trigger"` and a new `seqNo`.
  `Rejected`/`NotImplemented`, request timeout, or Accepted without a subsequent
  fresh event establishes **no physical evidence** and refreshes no timestamp.

Only after that capability exists should the manager add a session-owned ~60 s
read-only resync task, with capability gating/backoff and cancellation on disconnect
or reboot. It must be independent of electrical recovery, never toggle permission
or authority, and never use the TriggerMessage acknowledgement itself as evidence.
Station-driven reporting is sufficient for the active-transaction case and needs
no new manager polling task.

## Diagnostics and verification

The `diagnostic_level` setting gates `physical_state` transition
records (evidence source/time, freshness, permission and CP epoch) and
`battery_reserve` confirmation transitions. Normal unchanged cycles stay quiet.
Tests cover initial read/event ordering, transient unknown readback, real OFF →
unknown → ON, stale derived OFF state, reordered evidence, reload/reconnect and
immediate HA publication. No hardware validation of this change is claimed.
