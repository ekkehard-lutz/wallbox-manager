# v0.4.0-beta.8

This GitHub **prerelease** integrates the independently source-reviewed manual
vehicle wake-up implementation `f29dad7edfe2742f7a483f92479d51509cbb2046` and the
Take Control visibility correction `4c974edb294d8b2d47f64b6bbb749dfced46c8cf`.
The complete feature history is retained by non-fast-forward develop merge
`7d6085cb4da262e1a8f809c8b8a20818a16961bc`, from beta.7 baseline
`b8befd28610a76d06f50c3843ead45e92e6e5f62`.

The coordinated Stationary develop merge is
`f423b59802fe411b80372d6c0c7befc57219b312`. It retains the full reviewed Phase 1/2
history, Phase 3 Domain wake-up `a601998cc851339b0f8d5dc67e63baf005695ebb` and
OCPP adapter `0758758aa611919ea6aada193139849fbadee5ee`.
Publishing this WBM prerelease does not install either component.

## Manual wake-up

- Adds **Wake up vehicle / Fahrzeug aufwecken** on the existing Home Assistant
  device/integration page. It is enabled by default and belongs to the same
  wallbox device. It is **not on the custom Lovelace card**.
- Requires the bound EVSE 1, a live registered OCPP connection and current observed
  Remote authority. The existing dispatch boundary permits only this exact OCPP
  2.1 `DataTransfer` request:

  ```json
  {"vendorId":"wallbox-stationary","messageId":"WakeUpVehicle","data":{"evseId":1}}
  ```

- Stationary invokes the existing
  `wake_up_vehicle(require_ocpp_authority=True, cancel_event=...)` through its
  existing hardware worker. Stationary owns all pause/current/meter safety
  checks, command locking, CP feedback and restoration. WBM duplicates none of
  those physical checks. Concurrent presses coalesce; obsolete intents and late
  results are fenced, and reconnect never replays wake-up.
- The successful result is
  `{"status":"Accepted","data":{"result":"completed","cpRestored":true}}`.
  **It confirms the CP cycle and restoration, not an awakened vehicle or charging.**
  Rejection, unsupported requests, timeout, communication failure, supersession
  and unconfirmed CP restoration remain distinguishable. No response changes
  charging observations optimistically.
- A command-specific 360-second response budget covers the station's configurable
  1–300-second hold, CP confirmation and hardware/transport margin. General OCPP
  timeouts are unchanged. Other outbound WBM calls can wait behind this serialized
  operation. Timeout does not prove restoration and causes no automatic retry.
  Disconnect/shutdown on Stationary requests cancellation and awaits CP cleanup.

See the [normative request/response contract](../specifications/manual-vehicle-wakeup.md)
and [implementation assessment](../manual-vehicle-wakeup-assessment.md).

## Take Control and preserved behavior

The card hides **Take Control** while its bound station is connected with an
available observed Remote authority. An external return to Local shows it again.
Missing, unknown, unavailable or disconnected authority keeps it visible and
disabled. Historical command results never decide current authority or visibility.
ChargingEnabled remains visible; its existing observations and independent
permission behavior are preserved.

Take Control and ChargingEnabled entity IDs, unique IDs, device associations,
service functionality, authority policy and existing five-second permission
readback remain unchanged. Ordinary observations retain last-value-held semantics
within their generation. Session/transaction identity, energy and statistics,
entry isolation and reconnect/no-replay behavior are preserved. No implicit
current, phase, permission or charging-profile operation accompanies wake-up.

## Optional future policy: documentation only

**WBM-WAKE-FUTURE-01 — Optional automatic vehicle wake-up after a 0 A pause** is
recorded as **PLANNED / OPTIONAL — NOT APPROVED FOR IMPLEMENTATION**. When positive
charging transitions are implemented later, WBM may optionally request wake-up if
the vehicle does not draw charging power within a configurable delay after a
0 A pause. Delay, observations, conditions, abort criteria and repetition policy
all require separate approval. WBM owns policy; Stationary owns CP and safety.

**No automatic trigger, setting, timer, retry, polling or executable policy is
implemented.** NETZ/PV regulation, charging-profile execution and current/phase
control remain out of scope. The PV V3 draft receives no broader approval.

## Validation and hardware boundary

Release gates include the complete Python/frontend suites, Ruff lint/format,
JavaScript syntax, Python compilation, manifest/version consistency, tracked
source/HACS package-content checks and Git whitespace checks. Successful GitHub
Actions on the exact final develop commit and on the annotated release tag are
required before publication. Feature-branch CI alone does not satisfy these gates.

Local WBM validation: **234 Python tests and 16 frontend tests pass**, along with
Ruff lint/format, JavaScript syntax, tracked Python compilation and manifest
checks. Five existing dependency deprecation warnings remain. Package checks use
the exact final commit's tracked source archive, excluding local investigation
files and obsolete runtime directories.

The reviewed implementation's tests cover strict requests, hardware rejection,
restoration failures, cancellation/disconnect, session continuity, scoped entity
registration, command fencing and the card's authority transitions. Automated
checks and source review do not establish physical CP restoration reliability,
actual vehicle wake-up, installed interoperability or live HA CPU acceptance.
**Manual wake-up hardware testing remains outstanding.** Unconfirmed CP restoration
requires operator attention. Existing bootstrap telemetry limitations remain.

## Publication and operator installation

Manifest version: **0.4.0-beta.8**. Config-entry/storage versions and dependencies
remain unchanged. HACS uses the annotated tag's tracked GitHub source archive;
no separate integration ZIP is required. Existing entity/history/statistics and
card binding are retained.

The operator will separately deploy the approved Stationary commit using Git and
install WBM beta.8 through the established HACS prerelease workflow, with a Home
Assistant backup. Installation and restarts require that separate operator step.
This publication performs no wallbox Git operation, installation, service restart,
Home Assistant restart or live OCPP command. Neither repository's `main` changes.
The GitHub Release is **Prerelease: true**, **Latest: false**, **Draft: false**.
