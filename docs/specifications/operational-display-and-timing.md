# WBM: session-energy presentation, active messages and live NETZ timing

**APPROVED REQUIREMENTS — specifications only, 24 October 2026.**
Baseline WBM `develop` `b46832404a478c1f2588822b70acaf7af46a080e` / v0.4.0-beta.14.
Requirement IDs: **WBM-DISP-01–03, WBM-MSG-01–07, WBM-TIME-01–24**.
Implementation, commit of production code, merge, release, deployment and hardware operations require separately authorized stages.

## Energy display

- **WBM-DISP-01:** In the Lovelace card show **Sitzungsenergie** in kWh with **exactly one locale-aware decimal** (e.g. `12,3 kWh`), including `0,0 kWh` for valid zero. Preserve unknown/unavailable as `—`.
- **WBM-DISP-02:** Presentation only; do not change measured energy, source sensor precision, ledger, Recorder, statistics, rounding of deltas or meter data.
- **WBM-DISP-03:** Actual live power remains at exactly two decimals in kW; requested NETZ power remains at one decimal.

## Bottom “Meldungen” section

- **WBM-MSG-01:** Display **one and only one message**: the most recently raised warning or error **which is still applicable** to this wallbox/connector. Normal informational status, action success, charge-state narration and the ordinary parameter line are not warnings.
- **WBM-MSG-02:** Backend owns source, identity, error/warning severity, chronology and the condition for resolution; card projects it without concatenating every status string. Local card service-call failures are user-visible and must have a clear lifecycle.
- **WBM-MSG-03:** When the cause disappears (reconnected, fault cleared, newly confirmed successful command, expired/canceled lockout or superseded failed operation), remove the warning immediately; do not require page reload or manual dismissal.
- **WBM-MSG-04:** If another older warning is still active after the newest resolves, show that next-newest active warning; if none, show `—` or a blank normal state.
- **WBM-MSG-05:** Pending or temporarily unconfirmed operation is not by itself an error; a genuine blocking lockout can be a warning while it persists, but no stale historical error may survive successful retry. A timeout alone does not prove physical fault clearance.
- **WBM-MSG-06:** Preserve full HA diagnostics and log history independently of the compact card. No periodic log-reading loop; use the existing event notifications and canonical state.
- **WBM-MSG-07:** Fence warning state by connector/entry and runtime generation so old delayed callbacks/reconnects cannot resurrect resolved warnings. Multiple cards show consistent state.

## NETZ schedule and countdown: normative semantics

An operator-confirmed Start creates a **business schedule** with absolute wall-clock deadlines independent of vehicle connection, OCPP transport, Local/Remote authority, measured power or whether a charging session is active. These business deadlines **do not** grant authority to send electrical commands. Physical requests always obey station hardware enable, Remote ownership, transaction, phase/current caps and lockouts.

- **WBM-TIME-01:** A valid explicit **Ladung starten** starts its configured delay countdown **immediately**, whether a vehicle is plugged in or not. No automatic takeover or hardware enable.
- **WBM-TIME-02:** Define schedule epoch as accepted Start (or an explicit Apply that re-arms delay); planned start = epoch + approved delay (unset/0: now). Planned end = planned start + approved duration, if set. Duration begins at **planned start**, not at physical first energy transfer.
- **WBM-TIME-03:** Show the remaining start delay in the existing Startverzögerung editor/display as `HH:MM`. In the final 1–59 seconds display `00:00` (floor complete minutes); at zero display normal `--:--`. Without an armed delay show `--:--`, not a fictitious countdown.
- **WBM-TIME-04:** Before planned start show the approved full Ladedauer if supplied. At planned start begin counting down Ladedauer independent of car/actual charging/lockouts. Display `00:00` in the last 1–59 s and `--:--` after expiry. Unset duration shows `--:--`.
- **WBM-TIME-05:** Positive offered current is permitted only at/after planned start and with current valid Remote/transaction/hardware eligibility. If connection comes late it receives only the **remaining** time; the time window is never prolonged because it was physically unable to charge.
- **WBM-TIME-06:** On duration expiry retire schedule, set manager release OFF, cancel all stale positive intents and retry timers, and command 0 A only if legitimately authorized. Do not change hardware ChargingEnabled, invent unplug/session Ended, or revive charging after reconnect.
- **WBM-TIME-07:** Backend enforces absolute deadlines even without a card, without vehicle, or during OCPP disconnection. Store/reconcile absolute deadlines appropriately on reload; do not silently restore previous charge release or replay stale positive requests.
- **WBM-TIME-08:** Duration `00:00` is explicit zero (immediate end at planned start), unlike unset (no duration limit). Unset delay or zero delay means immediate planned start. Do not turn absence of a car into a reason to refuse creation of a valid schedule.

### Effect of approved changes on an active schedule

- **WBM-TIME-09:** An explicit **Ladeparameter übernehmen** changing **only duration** recomputes current planned end = **unchanged prior planned start + newly approved duration**. It does not restart delay/charging or alter planned start. A newly calculated end in the past expires immediately. Removing duration removes the deadline.
- **WBM-TIME-10:** An explicit Apply changing **start delay** re-arms start at **Apply acceptance timestamp + new delay**, even if the car is charging or started while editing. Promptly request 0 A under normal authority (no transaction termination/hardware disable); keep positive offer blocked until the new planned start, plus any Stationary re-enable/phase lockouts.
- **WBM-TIME-11:** After re-arming delay, duration (new or retained) starts at **new planned start** even if ReenableLockout or PhaseSwitchLockout still prohibits physical charging. Expiry may occur before physical restart.
- **WBM-TIME-12:** If delay and duration are both changed, one atomic Apply uses the new schedule epoch, delay and duration. Power/phase changed alongside these remain subject to ordinary approval and existing OCPP intent fencing.
- **WBM-TIME-13:** Editing either time field without Apply changes *neither* the approved values/deadlines nor physical dispatch; the old business countdown continues in the background.

### Editing and 15-second Apply window

- **WBM-TIME-14:** While actively interacting with **either** time editor (focus, typing, clear, keyboard), show the user's draft rather than the live remaining countdown. Both time editors form one logical interaction: no countdown is shown in either while editing. The actual backend countdown continues.
- **WBM-TIME-15:** Once interaction with **both** time editors ends, start/reset a **15-second** window for meaningful pending time edits *while release is ON*. During the window the single button says **Ladeparameter übernehmen**. A fresh edit or refocus postpones expiry to 15 seconds after the latest completed edit.
- **WBM-TIME-16:** If no Apply occurs before the 15 seconds elapse **and the original schedule is still alive**, safely cancel **only pending time drafts** and restore both live countdown displays and the normal **Ladung beenden** label. Do not change approved values/revision, accepted deadlines, manager release, power/phase drafts or reserve.
- **WBM-TIME-17:** If a separate meaningful power/phase draft is pending, its Apply state continues after time-draft rollback; never conceal it with a forced Stop label.
- **WBM-TIME-18:** If old duration expires while editing or within the 15-second grace period, old manager release goes OFF and the button changes to **Ladung starten**; cancel the grace timer, **retain unapproved edited time values** for the next explicit Start and do not auto-approve them or apply to a finished run.
- **WBM-TIME-19:** On successful Apply return to Stop while release ON and the new schedule is active. Explicit Stop cancels any delay/duration and never auto-restarts later. Continue the canonical single-button Start/Apply/Stop interface.
- **WBM-TIME-20:** Countdown display projects canonical backend absolute timestamps and may locally update without creating 1-Hz HA sensor/Recorder churn or a second business scheduler. Handle timezone/DST via absolute instants, multi-card synchronization, browser refresh and stale callbacks.
- **WBM-TIME-21:** Direct HA text-service edits and edits from another card must remain supported. The 15-second rollback must be revision/ownership-safe; a stale card timeout **must never overwrite newer writes**. A purely local display rollback that leaves an independently pending backend draft is not sufficient.
- **WBM-TIME-22:** Validate and durably persist approved schedule changes before signaling success. Failed writes leave old schedule/approval intact and an actionable current warning.
- **WBM-TIME-23:** Any real release OFF (Stop, duration expiry, profile change, authority loss) cancels active schedule/positive intents. Preserve valid future settings but never permit an orphan deadline to start the wallbox. TIME-18 specifically governs expiry during a pending edit.
- **WBM-TIME-24:** Pending time edits do not turn into an implicit new Start, even if the car was connected after editing. The planned start and planned end are **manager wall-clock policies**, independent of the station's transaction lifecycle. Do not invent new OCPP scheduling variables or violate hardware lockouts.

### Operational clarification: abandoned editors (authorized review follow-up)

WBM-TIME-15/16/20/21 require bounded, renewable ownership while focused. A focused
editor may renew indefinitely without publishing or persisting unchanged values.
Stopped renewal retires ownership and starts the ordinary 15-second window from
the lease deadline; no meaningful pending timing draft means no rollback is needed.
Renew/Update/End from expired ownership cannot revive it. Only a fresh deliberate
Begin with the current revision/runtime may claim new ownership. Duration expiry
still wins and retains drafts under TIME-18. This lease applies solely to browser
editing intent, never to observation validity, OCPP authority or electrical safety.

## Acceptance examples

| Scenario | Expected result |
| --- | --- |
| Start at 12:00, delay 00:10, duration 00:20, no vehicle | Delay runs 12:00–12:10, duration 12:10–12:30; release OFF at 12:30 even if no EV ever connected |
| At 12:09:30 | Delay display `00:00`; at 12:10 becomes `--:--` |
| Charging since 12:00, duration 00:20→00:10, Apply 12:12 | End already 12:10, so Stop immediately |
| Charging, edit delay to 00:05, Apply 12:03 | 0 A interim, new planned start 12:08, duration then runs even if re-enable lockout persists |
| Background duration expires while user edits | Button Start, preserve edited values for next new run, no 15-second rollback |
| Editing finishes, then 15 s without Apply | Revert time drafts only; show live countdown and Stop if still ON |
| Unapproved time changes and independent power draft | Time expiration does not discard unrelated power draft |

## Test matrix and boundaries

Use deterministic fake wall-clock and monotonic editor clocks; test unplugged/start-without-vehicle, cable attach during delay, lockouts and missed power window, offline/reconnect, HA reload, delay/duration zero/unset, extend/shorten/remove duration, apply while charging, expiry during editing, multiple cards/direct text edits, lost/replaced revisions, persistent write failures, warning prioritization/clearance, one-decimal session energy and no increased measurement Recorder rate. Preserve OCPP authority, Stationary safety, manager release OFF, phase switching, battery reserve and PV semantics.

**Documentation phase only. No source implementation, merge, release, deployment or service restart here.**
