# Discovery session investigation

This document records the earlier beta investigation and its evidence at the time.
For v0.1.0, subsequent hardware testing confirmed reconnect after an actual network
black-hole/DROP, preservation of an active charging session, and recovery of
connector state and meter values. This does not retroactively establish the cause
of every earlier disconnect. Metering and session state have since been
implemented; see [metering/runtime state](metering-runtime-state.md) and
[session tracking](session-tracking.md) for the current behavior.

## Sources inspected

- Wallbox Manager beta.2: common transport, discovery, inventory and captured
  session owners; 1.6/2.0.1/2.1 adapters and wire/session tests.
- Installed `ocpp==2.1.0`: `ChargePoint.start`, `route_message`, `_handle_call`,
  `call` and `_get_specific_response`.
- Available wallbox-stationary source at
  `23047fa41b5a13e558c455adcb7f4283b7e07e84`: `ocpp_runtime/station.py`,
  `device_model.py` and `runtime.py`. Its library dependency is also ocpp 2.1.0.
- Pinned lbbrhzn/ocpp `848407c11ff659ce59779a99ce69984bbb0e3ce1`:
  `chargepoint.py` start/run/session ownership and `ocppv201.py`
  `_get_inventory`/`on_report`. This follow-up adds an independently implemented
  cancellation boundary, not additional copied upstream code. Existing MIT
  attribution remains applicable to the surrounding adapted architecture.

## Proven correlation defect

The library has one WebSocket receiver (`start`) which dispatches inbound calls
and queues CALLRESULT/CALLERROR messages. Outbound `call()` is intended to run
concurrently with that reader. A library lock serializes outbound calls; each
call consumes the response queue until its unique ID matches. There is no map
of independently pending response futures and no second WebSocket reader.

On a known reconnect, Wallbox Manager starts discovery even without a new boot.
If BootNotification arrives while that request awaits its response, the adapter
cancels the discovery task and starts another. Previously this cancellation
propagated into the library's `call()`, releasing its lock and abandoning its
response consumer. The next GetBaseReport call consumed the first request's
Accepted response, found a different unique ID, and logged
`Ignoring response with unknown unique id`. The unique ID was valid for the
abandoned request, not an incorrectly generated station response.

A deterministic test holds the first GetBaseReport response on a known reconnect,
sends BootNotification, then releases the response. The original implementation
fails the no-unknown-ID assertion. This reproduces the reported correlation log.

## Fix and ownership

`DiscoveryAdapter.call` spawns the unmodified library call as a session-owned task
and awaits it through `asyncio.shield`. Replacing discovery cancels only its
consumer; the sent library call retains its lock and consumes its own response
(or reaches its existing response timeout). A replacement call waits on the same
library lock. No response routing, IDs, wire schemas, receiver loops or timeout
values are changed. This common boundary applies to all three adapter versions.

The superseded discovery is still cancelled promptly, its report buffer is
cleared, and attempt/session tokens prevent late publications. The old wire
call cannot publish discovery. Session teardown cancels and joins both discovery
and outstanding library calls; shielding does not outlive the captured session.

Boot responses still precede boot-triggered discovery. NotifyReport is handled
and acknowledged by the sole reader while discovery awaits either Accepted or
report completion. Rejection, unsupported requests, missing replies and malformed
or incomplete reports remain discovery evidence outcomes. They do not terminate
a healthy reader. Actual transport closure still invalidates the connection.
The pre-existing discovery exception boundary already isolated these failures;
no change to session exception propagation was needed.

## What this does not prove

The normal BootNotification → GetBaseReport Accepted → NotifyReport exchange
**passes against the original implementation**, using two actual OCPP library
peers. The cancellation-race reproduction also remains connected in the original
implementation despite the unknown-ID log. Consequently, this defect alone does
**not establish the cause of the reported hardware disconnect**.

wallbox-stationary configures a 30-second library response timeout. A timeout in
its supervised operation or report worker propagates through `supervise`, cancels
sibling tasks, and causes its runtime to reconnect. That explains the mechanism
and timescale if a station-originated call receives no response, but the available
failure description does not identify that call. It cannot be concluded that
NotifyReport specifically timed out, or that the CSMS discovery task killed the
transport. Correlated hardware wire logs identifying the unanswered station call
are still required to establish that part of the root cause. No timeout was
increased and no claim of a verified hardware fix is made.

The inspected station implementation returns GetBaseReport Accepted, queues the
matching report in a synchronous after-hook, and sends NotifyReport from its
separate worker with matching request_id, seq_no=0 and tbc=false. This subset is
compatible with the library; no station defect was demonstrated and that
repository was left unchanged.

## Regression coverage

`tests/test_ocpp_bidirectional.py` uses actual 2.0.1 and 2.1 library peers over
local WebSockets, a separate station report worker and no manual receiving in
discovery. It covers normal discovery, the controlled reconnect/boot cancellation
race, reverse NotifyReport while GetBaseReport awaits Accepted, and session stop
with a pending shielded call. Connections and generations remain stable beyond
the shortened test response timeout, report rows are processed, both reports in
the restart case are acknowledged, and session tasks are joined.

Existing JSON-peer tests cover all three protocol versions, unsupported requests,
request timeout, rejected reports, report timeout, wrong request IDs, malformed
sequences, multipart reports and stale boot/reconnect fencing. Explicit connection
and Heartbeat assertions verify that discovery failures do not imply transport loss.

## Follow-up: proven OCPP 2.1 missing-handler timeout

Subsequent inspection identified a separate defect in the installed ocpp 2.1.0
library's `_raise_key_error(action, version)`: it branches for `1.6`, `2.0` and
`2.0.1`, but not `2.1`. When `_handle_call` finds no registered handler, the 2.1
path returns without sending **either CALLRESULT or CALLERROR**. In 2.0.1 the
same missing handler produces a NotImplemented CALLERROR instead.

Wallbox Manager lacked handlers for MeterValues and TransactionEvent. The inspected
reference station sends periodic MeterValues (EVSE 1, timestamped sampled values)
and tokenless TransactionEvent notifications (Started/Updated/Ended, station-owned
transaction ID and sequence, EVSE/connector, optional meter values). Both use
`call(..., suppress=False)`. It also sends NotifyEvent after authority loss, which
is an event notification rather than a vendor DataTransfer command.

A genuine 2.1 library peer sending the reference MeterValues shape reproduced
TimeoutError before this change, using a shortened one-second response timeout.
The corresponding 2.0.1 test failed with NotImplemented. This proves the protocol
failure mechanism missing from the earlier investigation: a silently dropped 2.1
station call reaches the station's response timeout, whose supervised worker then
ends the session. The hardware's 30-second setting matches this mechanism. A new
hardware run is still necessary to confirm that this accounts for every observed
reconnect; we have not captured that specific hardware exchange.

The common 2.x adapter now acknowledges schema-valid MeterValues, TransactionEvent
and NotifyEvent with the **concrete protocol version's** call_result classes.
There is no storage, aggregation, metering/transaction state, authorization lookup,
authority interpretation or new public API. Runtime snapshots remain unchanged.
The existing response-correlation cancellation fix and single reader are unchanged;
1.6 handlers and behavior are unchanged. No wallbox-stationary code was modified.

Both versions' MeterValuesResponse and NotifyEventResponse schemas permit `{}`.
Both TransactionEventResponse schemas also permit `{}`: the reference station
sends no idToken, and neither a transaction ID nor a status is required in its
response. 2.1 adds optional transactionLimit and updatedPersonalMessageExtra fields;
these are not populated. Neither JSON schema encodes the cross-message condition
that an incoming idToken calls for idTokenInfo (also handled explicitly upstream).
For such requests we return only `idTokenInfo.status = Unknown`, never the upstream
unconditional Accepted. This is a fixed acknowledgement of an unverified token,
not an authorization implementation. Costs/priorities/limits are omitted rather
than invented. The normal version-specific request and response validation stays on.

`tests/test_ocpp_reporting_ack.py` covers both versions, MeterValues, all three
transaction event types with embedded samples, the token-bearing response, and
NotifyEvent while discovery is awaiting inventory. It verifies concrete response
types, completion of discovery, stable generations/connection, unchanged generic
state and working Heartbeat. Invalid messages still receive CALLERROR. The tests
use genuine library senders/receivers and validate against each version's schemas.
The missing-handler defect for other unimplemented 2.1 actions remains a library
limitation; this change explicitly handles the reference station's current traffic
rather than adding a broad ACK or bypassing schema validation.
