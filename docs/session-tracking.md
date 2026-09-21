# Session tracking and persistence

A charging session follows the reported transaction lifecycle, independently of
socket lifetime, boot generations, connector occupancy and power. A connected
vehicle drawing 0 W can remain in the same active session indefinitely.
**Disconnect, BootNotification, integration reload and HA restart are not session
ends.** Charging controls, authorization services and vehicle identification are
outside this feature.

## Boundaries and identity

Adapters translate OCPP messages into immutable `core.sessions.SessionEvent`
values. `Runtime.session_event()` fences obsolete connection/boot generations
before updating its protocol-independent `SessionLedger`. The immutable
`ChargingSession` stores an internal UUID, separate external transaction ID,
station/EVSE/optional connector scope, aware timestamps, meters, energy, power,
maximum power, state and end reason. `active` derives from `ended_at`; duration
uses the start and end timestamps, or the current clock while active.

The UUID survives reconnect/reload/restart. Runtime incarnation IDs never enter
session identity. Matching requires exact scope and transaction ID. Missing
connectors stay EVSE-scoped; no connector 1 is invented and no readings are
projected across EVSEs/connectors/stations. Omitted entire EVSE information on a
2.x event can use an existing unambiguous station transaction match. A later omitted connector can reuse an already explicit connector on the same
EVSE. Conflicting explicit scope changes are ignored for session lifecycle; they
do not duplicate or move the existing transaction. A first EVSE-only event remains
EVSE-scoped. Protocol changes do not alias 1.6 connector
identities with 2.x EVSE identities.

## Protocol mapping

| Version | Start/update/end | Meter endpoints and response |
| --- | --- | --- |
| 2.0.1 / 2.1 | `TransactionEvent` Started, Updated, Ended; transactionInfo.transactionId, chargingState, EVSE/connector, timestamp and stoppedReason (fallback triggerReason) | Independently use genuine versioned call/result schemas. Tokenless responses remain empty; supplied idToken receives Unknown, without credential authorization. Embedded registers are normalized for session accounting. |
| 1.6J | `StartTransaction` and `StopTransaction`; connector N maps to EVSE `connector-N`, connector `N` | Allocate a persistent entry-wide positive integer transaction ID; exact same-scope/start-time StartTransaction replay returns the original. Return Accepted for recording the station-initiated transaction. StopTransaction supplies ID, final meter and reason; its response is empty. No Authorize or remote-control endpoint is added. |

1.6 MeterValues carrying transactionId can update only that matching active
session. Unlabelled meters can update the active session at their exact scope.
StatusNotification may update charging state but cannot start or stop a session.
2.x offline events may update the chronological session ledger without becoming
live metering/operational state. Existing lifetime-meter entity normalization and
freshness rules remain unchanged.

Timestamp order determines lifecycle progress; sequence numbers order 2.x events
with equal timestamps. Equal-time events without a sequence are ignored except
that Ended may close a zero-duration session. Duplicate starts/updates/ends are
idempotent. Completed records act as transaction tombstones, so later replay cannot
reopen them. An older event cannot replace a newer lifecycle state. A later new
transaction on the same exact scope conservatively completes the old one with
`end_reason="superseded"`; its missing final meter stays unknown.

If the first observed event is Updated or Ended, the session is retained with its
first-seen timestamp and `start_known=false`. That timestamp is **not** a claim to
know the actual transaction start. Start meter and charged energy stay unknown;
duration covers only the observed interval. A missing-scope event with no prior
unambiguous match is acknowledged without creating a fabricated scope. Late
Started does not rewrite newer state. Transaction IDs are expected to be unique
within a scope; completed IDs are not recycled by this ledger.

## Energy and power

Session energy is the explicit total Energy.Active.Import.Register endpoint minus
the start register, in exact Wh internally. HA displays kWh. The start register
must accompany the known start timestamp; a later first reading does not pretend
to measure the whole session. During charging the endpoint is the latest matching
register. At completion an endpoint at the end timestamp is needed for a final
energy value. Missing/invalid endpoints yield unknown, never fabricated zero.

Any decreasing, invalid or conflicting same-time register makes session energy
unknown for the remainder of that session, even if later readings rise above the
original start. This avoids counting through a meter reset. Valid raw start/end
snapshots remain available. A new session can establish a new baseline. There is
no power integration, phase summation, inferred total or reset offset.

Transaction Begin/End register contexts may be used for the session endpoints
only. The same samples remain excluded from lifetime energy entities, preserving
the existing lifetime-meter interpretation. Unit/multiplier/phase validation uses
the shared normalization path. Devices with inconsistent boundary/periodic
register bases cannot provide trustworthy energy; reset/conflict handling is
conservative but cannot detect every plausible-looking device reporting error.

Power uses only explicit total active-import power at the exact session scope;
phase powers and broader EVSE/station measurements are not attributed to a
connector session. Track the maximum reported total for history. Active power
becomes unknown when its live reading expires, disconnects or a new generation
invalidates observations. Completed sessions always display 0 W. Saved power is
historical and cannot become a live reading merely through restoration.

## Home Assistant

The first session at a scope creates nine sensors and one binary sensor:

- Session ID and Session Active.
- Session Started and Session Ended (native timestamp sensors).
- Session Duration (seconds, updated while active).
- Session Charging Power (W, power measurement).
- Session Energy, Start Meter and End Meter (kWh; no total_increasing class).
- Session Charging State (existing protocol-independent enum).

Completed values remain visible, even offline, until the next session replaces
them. The next start clears ended time and starts a fresh duration/accounting
interval. Unknown/unseen scopes create no session entities. Stable unique IDs
combine entry ID, station, EVSE, optional connector and entity key; they contain
neither session UUID nor runtime generation. English and German names include
scope. There are no per-history-record entities. Entity subscriptions and active
time/power refresh timers are removed on platform unload.

## Persistence and query contract

Home Assistant `Store`, version **1**, atomically writes
`.storage/wallbox_manager.<entry_id>.sessions`. It contains every completed record,
active records, the per-scope current/last display index and the next 1.6 ID.
Records include final duration, start/end meter, charged energy, maximum power,
reason, lifecycle/measurement ordering timestamps and reset/incomplete-start flags.
Aware timestamps are ISO strings and exact numeric fractions are strings. There
are no credentials or raw OCPP objects in this document.

Load and validate the entire document before opening the listener or creating HA
entities. Writes are coalesced by one second through Store; graceful unload and
shutdown flush the latest ledger after transport tasks stop. Sudden power loss
can lose the last pending write, as with other delayed HA storage. Schema versions
are explicit for future migration; there is no migration from a previous session
schema. Invalid records fail load rather than partially replacing the ledger.
History is currently unbounded and rewritten as one document; retention, archival,
UI/WebSocket endpoints and large-history optimizations are future work.

The read-only internal query surface is:

```python
runtime.sessions.latest  # tuple of current/last immutable records
runtime.sessions.get(scope)  # current/last record at an exact scope
runtime.sessions.history()  # all completed records in end-time order
runtime.sessions.history(scope)  # completed records at one exact scope
```

History queries return immutable snapshots and never create HA entities. Session
persistence remains separate from discovered wallbox capability, EV acceptance,
control ownership and live observations.
