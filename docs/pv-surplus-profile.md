# PV Surplus primitive profile (0.3.x)

PV Surplus shares the Grid profile's persisted settings, explicit charging
permission, active-wallbox ownership, common operating-point solver and OCPP
execution runtime. With authority, profile selection explicitly disables charging
and waits for confirmation. Without authority, selecting/configuring profiles only
stores settings and sends no OCPP commands. Neither action takes authority.
An explicit takeover preserves configuration, leaves charging disabled and still
requires a separate enable action.

## Central references and measurement quality

Configure Home Assistant entity references in the integration options:

- `leistung_pv`: PV generation power (required).
- `leistung_verbraucher`: total consumer power, **including** the selected
  wallbox (required).
- `soc_speicher_aktuell`: battery SoC (optional). Its configuration alone enables
  battery-aware PV operation; the Grid reserve reference is not required.

Grid is always available. Backend profile options include PV Surplus only when
both power references are configured, independently of battery/reserve mappings.
Temporary sensor outages keep the profile in the selector but block charging.
The card hides the selector if there is only one available profile. Removing a
required mapping fences pending work and requests OFF; automatic fallback to Grid
waits for confirmed disabled permission. Without authority it remains pending until
OFF can be confirmed, rather than taking over a locally controlled wallbox.

Options persist across reloads. Power accepts W, kW and MW and is normalized to
watts. SoC requires percent (`%`), within 0–100. Unknown, unavailable, non-numeric,
non-finite, future or more than 90 seconds unreported readings suspend charging
with a zero-power request. Explicit measurement expiry is also respected.
A configured but invalid SoC never falls back to battery-free operation.

Actual charging power is discovered by stable session-power metadata: integration
entry, station, EVSE, connector and current runtime incarnation. Exactly one
matching sensor is required; other wallboxes are excluded. No requested power,
current limit or historical applied point substitutes for a missing measurement.
Fresh session-power expiry is exposed on the existing sensor. Consequently,
missing initial session measurements also prevent automatic charging.

```
available_power = pv_power - consumer_power + selected_actual_charging_power
```

For 8000 W PV, 5000 W consumers and 3000 W selected charging, the result is 6000 W.
Zero or negative available power prevents a start and starts the stop-delay timer
for an ongoing charge. Adding back actual charging power avoids repeatedly subtracting the controlled wallbox's own consumption.

## Without a battery

Choose the common solver's canonical approximation value:

- `up` / NOT_BELOW / Not below target: use at least the available power, within
  achievable hardware limits; some grid import is possible.
- `down` / NOT_ABOVE / Not above target (default): do not exceed available power;
  a target below the minimum feasible positive point prevents a start and starts
  the configured stop delay for an ongoing charge.

No minimum-current or device/vehicle limits are bypassed.

## Battery state machine

Settings are `soll_soc_speicher` (default 95%) and `soc_hysterese` (default 5
percentage points). The stop threshold is target minus hysteresis. Target settings
are restricted to 0–99%, hysteresis to 0–target; policy calculations additionally
clamp the upper threshold to 99%.

| Situation | Action |
| --- | --- |
| Any start or restart, SoC ≤ target | OFF; waiting for SoC above target |
| SoC > target | Charge with NOT_BELOW |
| Already charging, stop threshold ≤ SoC ≤ target | Continue with NOT_ABOVE |
| SoC < stop threshold | OFF; battery stop |
| Insufficient available power | Minimum feasible power during stop delay, then OFF |
| Restart after any pause | Require SoC > target again |

Equality at target permits continuation only. Equality at the stop threshold does
not stop an ongoing charge. Runtime continuation uses a confirmed APPLIED charging
point and explicit profile state, not a transient OCPP Charging status. A new or
ended transaction clears continuation and requires the full start rule again.
Permission stays enabled during profile-controlled OFF, waiting and pauses.
Diagnostics distinguish active charging, PV pause, battery stop, waiting for SoC,
invalid measurements and waiting for command confirmation.
The PV profile does not modify the battery discharge reserve; Grid reserve
restoration remains handled by the existing shared battery integration.

## Regulation and safety

`regulation_interval` defaults to 5 seconds (configurable 1–300 seconds). Every
cycle reads current measurements and computes a fresh policy and solver result.
Ordinary positive power adjustments during charging use the existing one-second
debounce. Starts with zero start delay and OFF skip it. A battery SoC event that requires OFF immediately fences pending work and
wakes this same regulator, including while its interval or debounce is waiting.
The event value is checked so that a short dip below the threshold cannot be
hidden by a later recovery. Measurements are re-read after debounce and positive
command fences reject changed or stale policy inputs. The shared control runtime
also applies the PV policy immediately before dispatch and after command replies;
every OFF clears continuation, including OFF requested through primitive controls.
An already dispatched frame cannot be recalled, but its delayed reply cannot
restore continuation after a safety stop; a zero-power command follows. Equal
confirmed operating points produce no redundant OCPP operating-point commands. Phase lockouts retain the existing retry
and cooldown implementation, including avoiding resending an already applied
fallback point. Temporary refusals (including stale/busy) retain the last confirmed
point and continuation state and use the existing 60-second retry interval.
Changing desired surplus does not bypass or extend that deadline; safety OFF does.
The control runtime serializes operating-point commands through completion of the
adapter operation, including phase fallback. Pending commands retain their intent
generation across normal PV evaluations; the next cycle reads the newest inputs.
An accepted write rejected by the final fence is not reported as confirmed and
requires reconciliation even if the next target equals the last confirmed point.

Authority loss, deselection, profile transitions, permission disable and unload
invalidate pending work. Reload restores settings only, not charging authorization
or an ongoing battery continuation state. Safe zero-power capability must be
verified before PV charging can start. A missing safe-stop capability is surfaced
as a blocked request rather than inventing protocol support.

The bundled card discovers all entities automatically. It shows approximation
without a battery, or target SoC/hysteresis with a battery. Regulation interval and
both delays are shown in both modes. Hiding controls never erases stored settings.
Profile configuration remains editable without authority; the permission button
then displays the real state but cannot issue a misleading enable action.
English/German labels, live session data, APPLIED display and the explicit
permission button remain available.

## Asymmetric PV delays (beta.2)

Persistent `pv_start_delay` defaults to 0 seconds and `pv_stop_delay` to 60 seconds;
both accept 0–3600 seconds. Start delay counts only continuous eligible surplus,
valid measurements, feasible positive solver output and the complete battery start
rule. A lost condition resets it. With zero delay, enabling with valid measurements
starts immediately; newly eligible measurements also wake a paused regulator.

During active charging, insufficient surplus starts a separate stop deadline.
The controller holds the last confirmed current and phase point, validated by the
same solver against current electrical limits. This intentionally permits temporary
grid import or battery discharge, including while SoC is in the continuation band.
Sufficient surplus cancels this deadline. Expiry requests OFF without disabling
permission; every subsequent restart requires the full start rule and start delay.
If the held point is no longer electrically feasible, OFF is immediate.

User OFF, authority loss, invalid/stale measurements, low battery SoC and existing
safety conditions bypass PV delays. Regulation wakes at the earliest regulation,
PV-delay or measurement-expiry deadline. Device phase/restart lockouts remain
independent and cannot extend the PV stop deadline. Delayed positive commands are
checked again at dispatch/acknowledgement against current policy and deadlines.

## Limitations and hardware validation

This is the accepted first-order power balance: no DC/AC conversion or inverter
loss correction, no PV Daily Optimum, PV Maximum or external Energy Manager.
Reference sensors must report at least every 90 seconds; delayed/asynchronous
meter readings can temporarily distort the balance. No prediction or smoothing is
included. Command execution time and a changed-target debounce can extend a cycle.

Validate on hardware: zero-power suspension while permission remains enabled,
restart from OFF, connector-specific power metadata and freshness, both phase
transitions and lockout recovery, battery threshold crossings, sensor outages,
authority handover and integration reload. Verified capabilities and simulated
OCPP tests cannot replace these device checks.

## Opt-in PV controller diagnostics

Open **Settings → Devices & services → Wallbox Manager → Configure** and enable
**PV controller diagnostic logging** (German: **PV-Regler-Diagnoseprotokoll**).
It defaults to disabled, is saved in the integration options, and needs no OCPP
control authority. A diagnostic-only change takes effect for subsequent evaluations
without integration reload, charging/OCPP commands or changes to profile settings.
Changing entity references at the same time still uses their existing reload path.
Disable this option after troubleshooting: records are detailed and may be frequent.

Each actual evaluation writes exactly one physical **INFO** record prefixed
`PVCTRL`, followed by compact JSON with stable sorted keys. Normal HA logs suffice;
no `logger:` configuration is needed. Nested solver checks, debounce replanning
and dispatch freshness fences belong to the same record. `trigger` distinguishes
periodic regulation, permission evaluation, safety-stop execution, SoC-event
policy evaluation and standalone wake-up plans. Read-only dispatch fences alone
do not create additional cycles; an inactive/unauthorized regulator does not
invent periodic cycles. Records finish when evaluation/command processing ends,
before the interval wait; cancellation and exceptions also produce one record.
Concurrent event/permission records can finish out of start order, so compare
`started_at`, `evaluated_at`, identity and `trigger` as well as log order.

Records contain:

- Station/EVSE/connector/profile, authority/ownership, connection, hardware enable,
  vehicle/charging state; scoped measured power/current/voltage and their validity.
- External entity IDs, raw state, numeric value, unit, availability, report timestamp,
  report age and explicit missing/non-numeric/unknown/unavailable/stale/future/expired
  status; selected connector power sources are identified separately.
- Profile thresholds/hysteresis/interval/delays, electrical envelopes and limits,
  calculated non-wallbox load and surplus, policy target/direction, solver bounds,
  selected point and resulting current/phase/power approximation.
- Applied point before/after, last commanded solver point, new and previous command
  outcomes, pending target, phase retry/backoff and elapsed/remaining delay.
  `command_evaluated` means a new execution result, which can include reuse of an
  already confirmed point; it does **not** claim an OCPP frame was sent.
- Explicit `decision` and `reason` (plus `policy_reason`, `solver_reason`,
  `command_reason` where applicable). Examples: START, HOLD, INCREASE, DECREASE,
  START_PENDING, STOP_PENDING, STOP, OFF, INPUT_UNAVAILABLE, WAIT_PHASE_LOCKOUT,
  COMMAND_FAILED, NO_AUTHORITY, CANCELLED. PLANNED denotes policy-only evaluation,
  not a claim of successful execution. There is currently no separate software
  enable/re-enable lockout timer; `enable_lockout=not_implemented` makes that explicit.

Null means absent/unknown, never a fabricated zero. Power is watts, current amps,
voltage volts, delay/age seconds, SoC percent. A diagnostic collection failure is
isolated from control; it is explicitly marked rather than triggering a stop.
Only selected numeric/state metadata is collected, not credentials, arbitrary
entity attributes, protocol messages or endpoint URLs.

### Freshness remains the existing policy

Power/SoC and selected wallbox-power inputs use HA `last_reported` (fallback
`last_updated` only for objects without that property). `last_reported` advances
when an entity reports even the same value; `last_updated` advances only when its
state/attributes change. This is **HA report age**, not proof of a new physical
sample inside an inverter. The existing acceptance window is 0–90 seconds, with
valid units/numbers/ranges and any `valid_until` deadline also required. Battery
reserve is logged for context; the PV policy does not regulate or reject charging
based on the reserve reference's age. OCPP measurements have their own timestamps
and deadlines. No freshness rules or controller defaults changed.

Multiple 5-second evaluations may use the same upstream sample. Fronius PV Manager
currently has a **30-second coordinator interval**, confirmed by repository analysis;
changing Wallbox Manager's interval cannot speed up those source entities. No
new-sample gate, smoothing, or Fronius fast polling is introduced here. See the
[read-only polling analysis and proposed next step](fronius-polling-analysis.md).

### Next hardware test

1. Install/restart, verify the automatically loaded card as described in
   [frontend verification](frontend-registration.md), and enable diagnostics.
2. Record selected reference entities, target SoC/hysteresis, start/stop delays and
   interval. Reproduce the approximately 39% SoC / 38% target / 3.5 kW PV case.
3. Save consecutive `PVCTRL` lines from before charging starts through the stop,
   including event/safety-stop records. Compare `decision/reason`, source ages,
   site load/surplus, policy target, selected and confirmed points, and retry/delay
   state. A SoC above target alone does not explain the other input/safety branches.
4. Check that diagnostic-only toggles produce no charging command or disconnect;
   compare normal behavior with logging disabled. Check invalid/stale source and
   phase-lockout cases under the existing hardware test procedure.
5. Disable diagnostic logging and retain the captured records for analysis before
   changing the control algorithm or upstream polling.

### First-start reconciliation

An explicit ON first prepares the electrical point while ChargingEnabled is still
false, then confirms permission independently. A temporary preparation/permission
refusal now starts an in-memory startup worker with the existing 60-second retry
interval, even when no prior applied point exists. It re-reads policy and fresh
execution evidence for each attempt. Only successful preparation and permission
confirmation hand over to normal PV regulation. OFF, changed intent, ownership or
authority loss, disconnect/reboot and unload revoke this pending authorization;
restart/reload never restores it. Phase feedback alone cannot prove the current
setpoint, so an uncertain operation is retried rather than inferred as applied.

After an accepted command, PV policy checks use its previously verified phase mode
while continuing to check live power/SoC policy and electrical limits. The runtime
still fences changes to voltage, capabilities, limits, transaction, intent,
authority and permission. New writes always require fresh phase-operation proof.
A temporary phase-feedback gap after confirmation keeps the positive desired
request instead of manufacturing an OFF request. This does not extend the policy
stop delay or bypass safety-invalidating inputs.

PVCTRL adds `startup_pending` and `command_fence_reason`; `retry_remaining_s` now
also includes first-start retries. `policy_reason=actively_charging` describes a
positive policy decision, not permission or hardware confirmation;
`policy_allows_charging` makes that distinction explicit. Use `enabled`, `applied`
and `ongoing_after` to determine actual permission and confirmed continuation.
Connector availability is diagnostic telemetry, not transport connectivity or
authority, and is not a command fence by itself.

### Voltage drift during command confirmation

The command fence validates fresh voltage semantically: re-solve the stored power
request and direction, then compare charging/OFF, phase mode and current. Voltage
samples and voltage-derived offered watts need not be identical. After dispatch,
use the already verified dispatched phase mode; before a new write, use fresh
phase eligibility. The confirmed point records the freshly validated voltage and
power basis. No percentage or absolute voltage tolerance is introduced: even a
small change is material if it crosses a discrete current step under the selected
approximation policy. Capability, current-limit, transaction, authority, intent
and permission fences remain independent and unchanged.

`command_fence_reason=voltage_unavailable` identifies missing, expired or otherwise
invalid required phase voltage. `electrical_setpoint_changed` identifies a fresh
resolution that cannot retain the dispatched phase/current/OFF setting. Harmless
drift is accepted without a stale fence reason. An accepted but materially changed
point continues to use the existing reconciliation and retry path.
