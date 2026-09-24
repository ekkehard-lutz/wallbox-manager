# Grid (`NETZ`) profile

This is the implemented v0.3.x profile contract. It supersedes the older conceptual
Grid, profile takeover and battery-read-only proposals in `architecture.md`.
PV_SURPLUS, PV_DAILY_OPTIMUM and PV_MAXIMUM algorithms and the Energy Manager API
remain deferred until their specifications are agreed.

## Backend and controls

`profiles.py` owns connector-scoped settings in HA Store: selected profile,
`soll_power` (0–100 kW) and `min_soc` (0–100%). `battery.py` owns a separate atomic
reserve journal. Neither depends on the card. The select currently offers only
NETZ; selecting/reselecting it requests permission OFF and invalidates pending
work. Future implemented profiles must use this same selection boundary.

Select Grid, adjust requested power, then use the existing charging-permission
switch. Changing power while enabled immediately resolves and applies another
point. Permission remains confirmed hardware state and is never restored as
an ON command. Startup restores settings, not commands. A new explicit start
always uses the stored Grid power. Local/unknown authority cannot acquire control
through a profile, retry or observation; use the existing explicit Take control
button when appropriate. Settings may be stored by automations while inhibited.

The primitive W target, approximation policy and installation current limits remain
available for advanced use. The Grid kW setting is the source on the next Grid
start. A primitive edit fences an in-progress profile observation; avoid competing
writers. Zero power uses the existing verified zero-current contract and does not
toggle permission. No PV inputs, regulation loop or vehicle configuration exist.

## Operating points and observation

The existing solver handles approximation, current steps, separate per-phase
maxima, fresh voltages, installation limits and phase retention. The primitive
runtime remains responsible for protocol queues, sequencing, confirmation,
authority and connection/boot/generation fencing. Its new `phase_retry` marker
distinguishes an applied substitute from the desired point.

Only a phase-switch lockout leads to minute retries. Each retry uses fresh inputs
and existing primitive safety checks. Other failures terminate the sequence and
remain visible in primitive diagnostics. A one-minute observation starts only
after the desired point is applied. Observations must be fresh, not future-dated,
and sampled after application; missing measurements never mean zero.

For 3p, both L2 and L3 below 3 A cause a 1p recalculation; for 2p, L2 alone is
checked. The single-phase envelope remains authoritative. For 1p, a difference
greater than 2 A allows a better multiphase approximation, comparing its offered
power with the observed single-phase power. Each sequence permits at most one
promotion and one demotion: 1→3→1 ends at 1p, and 3→1 ends at 1p. Both transitions
re-enter the normal application/lockout sequence. No further commands follow
completion. New starts or power changes clear these inferred limitations.

Permission OFF, selection, newer power intent, disconnect and authority loss
invalidate pending work. Primitive generation checks also fence commands already
queued when an observation task is cancelled.

## Per-station capabilities and migration

Integration options select a discovered station/EVSE/connector for a separate
capability-reference form. It offers only fields missing from current OCPP
inventory. Explicit unsupported or malformed OCPP evidence cannot be overridden.
A complete inventory produces an empty form. No vehicle-capability fields exist.
References are stored under `station_references[station][evse:connector]`.
EVSE-level minimum/step/phase facts retain their natural EVSE scope.

Config-entry version 3 migrates old explicitly associated reference values into
that station/connector. Ambiguous or invalid legacy values are retained under
`unassigned_references`, unused by execution; inspect the options backup and enter
them for the correct discovered station. Nothing is assigned by discovery order.
Existing OCPP discovery and evidence precedence are unchanged.

## Home battery lifecycle and conflicts

Both integration options `min_soc_speicher` and `soc_speicher_aktuell` must be
present to enable new overrides. The writable reference supports HA `number` or
`input_number` with `set_value`; the read-only reference must provide a finite
numeric SOC from 0 to 100. Availability, service presence and entity bounds are
checked before use. Use entities expressed in percent. The dashboard never asks
for these configuration references.

On actual charging (active transaction, charging state and positive fresh flow),
the manager reads the reserve and SOC. If the requested reserve is higher than
the current reserve, it journals the original value before writing
`min(requested reserve, current SOC)`. This follows the requested formula even
when SOC is below the original reserve. A sufficiently high existing reserve is
left alone. Without actual charging, permission alone cannot hold an override.

Permission OFF, selection, disconnect, charging finish/suspension, authority loss
or unload restores the saved value, provided the entity still equals the last
written temporary value. A different external value wins: ownership is released
without overwriting it or reasserting the profile during that charging episode.
An external write of exactly the same numeric value is indistinguishable from
our own value. A resumed episode reads a new baseline. Several simultaneously
charging connectors share one reserve journal; the highest requested minimum
at the beginning of the episode is used, and restoration waits until all stop.
Changing `min_soc` during an episode is stored for the next episode.

Restart/reload attempts restoration before new activation, including after a
configuration change. A failed restoration keeps its durable record. Writes are
not retried on every telemetry update: one failed restoration attempt is allowed
per runtime, with another attempt when an unavailable referenced entity becomes
available, or after reload. Failed activation is not retried during that episode.
A service call whose state is not yet confirmed reports `write_unconfirmed` and
keeps its journal. Diagnostics and logs expose failures; grid charging continues.
HA has no atomic compare-and-set service for number entities: external automation
racing the service call itself cannot be fully excluded. Avoid multiple reserve
writers if stronger ownership guarantees are required.

## Lovelace card

Copy `custom_components/wallbox_manager/www/wallbox-manager-card.js` to
`/config/www/wallbox-manager-card.js`. In Settings → Dashboards → Resources add
`/local/wallbox-manager-card.js?v=1` as **JavaScript module** (advanced mode).
Refresh the browser after installation; change the URL version after updates.

Use actual generated entity IDs from the station device page:

```yaml
type: custom:wallbox-manager-card
profile: select.my_wallbox_charging_profile
power: number.my_wallbox_soll_power
permission: switch.my_wallbox_charging_enabled
reserve: number.my_wallbox_min_soc
```

`reserve` is the manager's requested-reserve number, not the battery entity.
Supply it when battery support is configured. The card always shows profile,
power and permission. Reserve and actual charging state appear only with both
battery references configured. Status includes phase lockouts, observation,
transitions, authority inhibition and battery errors. Inputs are restricted when
remote control is unavailable. English/German labels follow HA language.
Use standard HA cards for additional metering.

## Validation boundary

Automated tests use the real OCPP simulated peer and the existing primitive test
suite. They do not replace hardware verification of the station's supported
extensions and phase switching. Profile execution currently follows the existing
OCPP 2.1 control support; discovery/read-only support for other protocol versions
does not imply writable profile support.
