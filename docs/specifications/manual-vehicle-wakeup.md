# Manual vehicle wake-up through OCPP

User-authorized implementation scope: 22 October 2026. This narrow successor
extends WBM-CTRL-05/12 and the active foundation's two-operation scope for one
explicit operation; all existing takeover/permission behavior remains binding.
WBM-ARCH-01–12 retain precedence. Implementation, independent review, hardware
acceptance, merge, release and deployment remain separate.

| ID | Requirement |
| --- | --- |
| WBM-WAKE-01 | Add an enabled-by-default HA button, **Wake up vehicle / Fahrzeug aufwecken**, to the existing bound station device and integration. Use existing station identity construction: `<entry_id>:<station_id>:wake_up_vehicle`. Preserve Take Control/ChargingEnabled identities, attributes and services. |
| WBM-WAKE-02 | The custom Lovelace card has no wake-up control or service action. Preserve its Local/Remote takeover visibility and existing ChargingEnabled control. |
| WBM-WAKE-03 | Require the bound EVSE 1, live registered OCPP connection and current-generation observed Remote authority at request and wire dispatch. Station rejection is normal. WBM does not duplicate vehicle/pause/current/CP/meter safety checks. |
| WBM-WAKE-04 | Allow only the exact action/vendor/message/data below under the existing task-local dispatch authorization, including exact integer EVSE type. Arbitrary DataTransfer and every other physical operation remain blocked. |
| WBM-WAKE-05 | Reuse per-entry intents, keyed diagnostics, serialized calls and adapter-owned tasks. Coalesce concurrent identical presses; an explicitly repeated completed press is a new operation. Caller cancellation leaves the transport consumer owned. Authority revision, connection/boot and intent identity fence queued dispatch and late results. Never replay/retry automatically. |
| WBM-WAKE-06 | Expose pending, confirmed, rejected, unsupported, timeout, communication_failure and superseded using existing diagnostics. Confirmation means successful CP restoration only. Preserve independent observations/session data. Distinguish restoration_unknown from a failed operation with confirmed restoration. Unexpected response shapes are communication failures. |
| WBM-WAKE-07 | Use a per-request 360-second response budget: configured station hold up to 300 seconds, two bounded two-second CP feedback checks and acquisition/transport margin. Preserve the library's remaining deadline across unrelated CALL IDs. Leave the general OCPP response timeout unchanged. Timeout is an unknown outcome, not proof of CP restoration; never automatically retry. |
| WBM-WAKE-08 | The station alone owns physical checks, command locking, CP feedback and restoration. Disconnect/shutdown cancels its hold and awaits cleanup. No WBM polling, extra timer/subscription infrastructure, automatic wake-up, configuration knob, regulator, current/phase command or profile execution is authorized. |

Capability negotiation is intentionally absent: Remote EVSE 1 enables the button;
unsupported peers reject the explicit command through OCPP. No invented Device
Model value establishes support. The 360-second wire wait retains the existing
outbound serialization; other WBM CALLs can wait behind it, while incoming
telemetry and protocol responses continue. Hardware acquisition still has its
backend timeouts; an exceptionally slow backend/transport may exceed the margin.

## Optional future WBM policy

**WBM-WAKE-FUTURE-01 — Optional automatic vehicle wake-up after a 0 A pause**

**Status: PLANNED / OPTIONAL — NOT APPROVED FOR IMPLEMENTATION.**

When the Wallbox Manager later implements positive-current charging transitions,
it may optionally trigger the existing vehicle wake-up operation if the vehicle
does not begin drawing charging power within a configurable delay after
transitioning from a 0 A pause to a positive charging operating point.

The exact delay, required observations, conditions, abort criteria and repetition
policy remain subject to separate approval. This is a WBM policy responsibility.
Stationary continues to own CP actuation and safety. This requirement authorizes
no executable logic, settings, timers, retries, polling or automatic triggers;
manual wake-up remains the only implemented initiation path.

## OCPP 2.1 wire contract

One CSMS-to-station `DataTransfer` CALL, through the existing OCPP connection:

```json
{"vendorId":"wallbox-stationary","messageId":"WakeUpVehicle","data":{"evseId":1}}
```

`data` is a JSON object, not a JSON-encoded string. Only EVSE integer **1** is
supported; booleans, floats, strings, missing/extra data fields and custom request
extensions are rejected. No duration or hardware target is supplied by WBM.
OCPP's existing CALL ID/schema validation matches the response. Library Python
payloads normalize `evseId`/`cpRestored` to `evse_id`/`cp_restored`.

Successful CALLRESULT payload, only after confirmed CP restoration:

```json
{"status":"Accepted","data":{"result":"completed","cpRestored":true}}
```

The vehicle may remain asleep or draw no power. WBM confirms only the manual CP
operation; it does not change authority/permission/charging observations, physical
session identity, transaction ID/sequence or session energy from this response.

| OCPP status | `data.result` | Meaning / additional data |
| --- | --- | --- |
| `UnknownVendorId` | `unsupported_vendor` | Unrecognized vendor, no actuation. |
| `UnknownMessageId` | `unsupported_message` | Missing/unrecognized message, no actuation. |
| `Rejected` | `invalid_request`, `unsupported_evse` | Malformed extension or unsupported scope, no actuation. |
| `Rejected` | `not_registered`, `no_authority`, `unsupported` | Not registered/Remote, or Domain/worker unavailable; no actuation. |
| `Rejected` | `busy` | Existing command/worker, no queued wake-up. |
| `Rejected` | `unsafe_pause`, `unsafe_evidence` | Domain readiness/pause/CP or hardware evidence rejected before actuation. |
| `Rejected` | `hardware_failure` | Unexpected sanitized backend/dispatch failure. No exception text is transmitted. |
| `Rejected` | `wakeup_failed` | CP interruption failed/cancelled after an attempt; `cpRestored:true`. |
| `Rejected` | `restoration_unknown` | Restoration unconfirmed; `cpRestored:false`. Requires operator attention. |

Before a CP attempt, response data contains only `result`; no restoration is
claimed. Apart from the two attempted-failure results and success, `cpRestored`
is absent. Result strings are fixed and bounded. Unsupported/malformed requests
never invoke the Domain. No Reset, UnlockConnector, TriggerMessage, charging
profile or generic hardware command is used as wake-up.
