# Legacy manual vehicle wake-up compatibility

[Approved AUTO-WAKE-01–10](automatic-vehicle-wakeup.md) supersede WBM-WAKE-01–08
and WBM-WAKE-FUTURE-01. There is no outstanding automatic-wake-up approval.
Stationary owns automatic detection/delay/CP; WBM exposes only its confirmed enable
switch. The manual HA button and WBM DataTransfer dispatch are removed.

The station retains this legacy manual 0 A wire contract for compatibility.
Confirmed CP restoration never asserts that a vehicle awakened or began charging.

## OCPP 2.1 wire contract

One CSMS-to-station `DataTransfer` CALL, through the existing OCPP connection:

```json
{"vendorId":"wallbox-stationary","messageId":"WakeUpVehicle","data":{"evseId":1}}
```

`data` is a JSON object, not a JSON-encoded string. Only EVSE integer **1** is
supported; booleans, floats, strings, missing/extra data fields and custom request
extensions are rejected. No duration or hardware target is supplied by the legacy caller.
OCPP's existing CALL ID/schema validation matches the response. Library Python
payloads normalize `evseId`/`cpRestored` to `evse_id`/`cp_restored`.

Successful CALLRESULT payload, only after confirmed CP restoration:

```json
{"status":"Accepted","data":{"result":"completed","cpRestored":true}}
```

The vehicle may remain asleep or draw no power. The legacy response confirms only the manual CP
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
