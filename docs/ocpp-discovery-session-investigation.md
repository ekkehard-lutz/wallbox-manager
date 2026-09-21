# Discovery session investigation

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
