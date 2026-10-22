# Automatic vehicle wake-up — approved requirements

Approved for implementation: 22 October 2026, final revised user requirements.
AUTO-WAKE-01–10 supersede manual-only initiation and all earlier automatic
wake-up deferrals, including WBM-WAKE-FUTURE-01. Optional means selectable by the
operator, not optional implementation. No merge, release or deployment authorized.

| ID | Approved requirement |
| --- | --- |
| AUTO-WAKE-01 | WBM exposes only a station-bound **Automatic vehicle wake-up / Automatisches Aufwecken** switch on the existing HA device/integration page. Remove the manual HA button; neither control belongs in Lovelace. Stationary owns detection, delay, EVSE validation and CP actuation. WBM does not monitor charging or time wake-up. |
| AUTO-WAKE-02 | Startup `auto_wakeup_delay` is a strict integer in 1–3600 seconds, default 30. Independent `wakeup_duration` remains 1–300 seconds, default 5. Enable state defaults false only when no saved value exists at first commissioning. Persist every confirmed ON/OFF change durably in Stationary's existing persistence architecture; restore it across service/Pi/HA restarts and OCPP reconnects. No episode/attempt replay. |
| AUTO-WAKE-03 | Use the existing EVSE service cadence. Fresh successful EVSE evidence must establish actual register 1000 changing from 0 to at least configured minimum, in the same confirmed physical vehicle session. Require enabled, registered Remote operation and 1002 = 2 (B). Never arm retroactively on enabling, startup, reconnect or registration into a positive-current episode. |
| AUTO-WAKE-04 | Start a monotonic delay on that transition while 1002 remains 2. Cancel on 1002 = 3/4, zero/below-minimum current, disconnect, authority loss, disable, shutdown, phase switching or invalid/stale/failed EVSE evidence. A rejected, cancelled or attempted episode cannot rearm without a later genuine zero-to-positive transition. Exactly one attempt per episode, including fully charged/nonresponsive vehicles. |
| AUTO-WAKE-05 | Immediately before CP interruption directly reread EVSE and revalidate the same session/episode, enable, connection, Remote authority, configured current >= minimum, 1002 = 2, no emergency/fault/phase/conflicting command and unambiguous active/unblocked GPIO feedback. Reject overlapping/stale evidence. 1006 is an EVSE output state; recognized value 3 is allowed and is not an independent vehicle request signal. Never interrupt CP on 1002 = 3/4. |
| AUTO-WAKE-06 | Refactor the existing Domain pulse for automatic positive-current/B and legacy manual 0 A. No mandatory SDM630 current/power/age/sample, meter read or extra polling. Manual 0 A also permits normal 1006 = 3. Preserve exclusive command lock, CP block/release feedback, configured hold, bounded restoration, cancellation/shutdown cleanup, observation fencing and session/transaction/energy continuity. No preparatory current reduction, contactor/phase or permission command. |
| AUTO-WAKE-07 | Exact station-scoped Actual persistent boolean ReadWrite `WallboxController.AutoWakeUpEnabled` supports GetVariables, SetVariables, inventory and initial/change-only HardWiredNotification. Changes require Remote authority; confirmed reads work in either authority. WBM uses a new stable switch ID, supported unique endpoint, existing intent/dispatch/authority and boot/connection fencing; only observations set state, never ACKs. No extra WBM timer, polling, retry or monitoring. |
| AUTO-WAKE-08 | Reuse existing worker/lifecycle ownership. Disable, authority/connection/shutdown cancellation abort the hold and await restoration. Missing samples cannot preserve pending evidence. Diagnostics change only on arming/cancellation/attempt/completion/failure, without per-poll timestamps or Recorder churn. No retry after reconnect/restart. |
| AUTO-WAKE-09 | Keep legacy Stationary `wallbox-stationary` / `WakeUpVehicle` DataTransfer compatibility, removing the corresponding WBM button/command/translation. WBM transport still forbids arbitrary DataTransfer and SetVariables. Preserve Take Control, ChargingEnabled, all existing observations and sessions. |

EVSE DIN semantics: register 1000 is configured current; 1002 = 2 is connected
without charging request (CP B), 3 is a charging request (C), 4 ventilation.
1006 may equal 3 during a 0 A pause. The DIN owns contactor activation according
to CP; B must not energize charging under normal operation. Missing/stale EVSE
evidence is neither B nor zero. SDM630 is not a wake-up gate.

Fake EVSE/GPIO and monotonic-clock tests are the sleeping-vehicle acceptance
basis because no suitable sleeping EV is available. Physical restoration and
hardware integration boundaries remain separate; no hardware operation authorized.

## Confirmed persistence amendment (binding)

AUTO-WAKE-10: Stationary must fsync the new setting through its existing atomic
persistence path before confirming SetVariables. Failed/unavailable persistence
rejects the write and retains the last confirmed in-memory setting. Missing file
or key means first-commissioning false; corrupt/unreadable or wrongly typed saved
values are not permission to confirm a false fallback. Expose no invented setting
before startup restoration. Local/Remote transitions preserve the stored setting;
execution still requires registered Remote authority.

WBM reads the actual persistent setting at every bootstrap/reconnect and never
writes a default. OFF cancels detection; ON and startup restoration of ON cannot
arm an existing positive-current episode. Reconnect/restart cancels episode state
without resetting the setting. `auto_wakeup_delay` remains independent startup
configuration. Mark the Device Model Actual attribute persistent.

Narrow Stationary hardware evidence limits: a direct EVSE acquisition must finish
within two monotonic seconds; a gap greater than five seconds between the ordinary
service observations cancels the pending episode. These are local physical
execution prerequisites, not WBM observation TTLs or additional polling.
