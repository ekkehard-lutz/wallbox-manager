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
fallback point. Unchanged requests retain the Grid profile’s 60-second phase
retry interval; repeated regulation cycles do not reset that deadline.

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
The same solver resolves the minimum valid positive point (`1 W`, NOT_BELOW),
subject to all current/phase/capability limits. This intentionally permits temporary
grid import or battery discharge, including while SoC is in the continuation band.
Sufficient surplus cancels this deadline. Expiry requests OFF without disabling
permission; every subsequent restart requires the full start rule and start delay.
If no safe minimum exists, OFF is immediate.

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
