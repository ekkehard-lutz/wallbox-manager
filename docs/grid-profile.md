# Grid (`NETZ`) profile and installation ownership

This is the implemented beta.4 refinement of the v0.3.x Grid contract. It
supersedes the older conceptual Grid, takeover and battery-read-only proposals
in `architecture.md`. PV_SURPLUS, PV_DAILY_OPTIMUM, PV_MAXIMUM and the Energy
Manager interface remain deferred. This change does not prepare a release.

## Active wallbox is not charging permission

`ownership.py` owns one installation-wide `active_wallbox`. Its durable identity
is the JSON tuple `[config_entry_id, station_id, evse_id, connector_id]`. The
entry ID prevents identical OCPP station names on different listeners from
colliding. One coordinator shared by all loaded Wallbox Manager entries enforces
at most one eligible profile-controlled connector. Multiple physical wallboxes
remain supported; a multi-connector station exposes its distinct control scopes.

The coordinator persists the active identity in HA Store
`wallbox_manager.active_wallbox`. It does **not** persist permission ON, a successful
takeover authorization or pending work. Separate transient fields are:

- `ready`: the explicit takeover and OFF confirmation completed in this runtime;
- `transition`: a guarded activation is running and profile commands are inhibited;
- `status`: readiness or a specific failure, exposed by the active-wallbox select.

Startup/reload restores the identity and per-wallbox settings but starts inhibited.
The user explicitly takes control again; no startup, telemetry, retry, profile
selection or battery event acquires authority. Local/unknown authority and loss
of the active connector invalidate readiness and pending profile work.

Only the active, ready connector under confirmed Remote authority may receive
profile permission ON or operating points. This check is in `ControlRuntime`,
including dispatch-time fences, so entity services, automations, primitive
`change(allowed=True)`, profile calls and the custom card share the same guard.
Explicit OFF remains available subject to existing authority rules. Non-active
wallboxes may independently charge in Local with their physical settings. They
are external site loads for future PV control, not additional regulated targets.

## Explicit activation and takeover

With one discovered control scope, the card hides the active-wallbox dropdown.
**Take control / Steuerung übernehmen** explicitly activates that scope. With
multiple scopes, choosing one in **Active wallbox / Aktive Wallbox** is itself
the explicit authority-acquisition request. The existing station Take control
button uses the same coordinator; ambiguous multi-connector stations require
selecting the connector explicitly in the dropdown.

Activation is serialized and follows this sequence:

1. Inhibit profile commands globally; invalidate pending observations, retries
   and generations of queued commands.
2. If the previous active wallbox is Remote, explicitly send OFF and confirm it.
   If it is already Local, leave it alone: no reacquisition and no stop command.
   Unknown/unavailable previous authority or an unconfirmed required OFF blocks
   the transition.
3. Restore/release the old temporary reserve. A journal still requiring restoration
   blocks new activation; an external reserve change releases ownership normally.
4. Acquire the selected station's authority using the existing OCPP confirmation.
5. Explicitly send OFF to its discovered connectors and confirm OFF, even if they
   were already OFF. There is no target-power dispatch during takeover.
6. Persist the selected connector only after successful confirmation, revalidate
   connection/authority/OFF after the storage await, then mark it ready.
7. Leave charging permission OFF. Starting requires a separate subsequent user
   action on the charging-permission switch.

Every successful `ControlRuntime.take_control` follows the explicit OFF contract,
including the station button. The lower primitive takeover is also hardened;
there is no legacy takeover-and-apply-saved-power path. A failed attempt is not
reported as ready and cannot authorize delayed ON. No background retry acquires
control. A → B does not force A back to Local; the user may do that physically.

A disconnected active wallbox remains selected but inhibited. There is no automatic
replacement, even if only one other wallbox remains. If the old entry/device has
been removed or is permanently unreachable and required OFF cannot be confirmed,
a new activation fails closed. Restore communication and establish OFF or Local
before switching/decommissioning it. Software cannot prove that an unreachable
charger stopped; there is intentionally no unsafe "forget and enable another"
shortcut. Removing inventory never makes the card target an unrelated entity.

## Backend Grid settings and bounded control

`profiles.py` retains connector-scoped Store values: profile NETZ, `soll_power`
(kW, bounded by known effective technical limits; storage range 0–100) and
`min_soc` (0–100%, whole percent for new edits). Existing beta.2 stores remain readable. Switching active wallboxes does not copy settings.
The select currently offers only NETZ. Selecting/reselecting the profile requests
permission OFF and invalidates work; it never acquires authority.

After takeover, choose Grid, adjust power and enable charging. While active and
enabled, power edits use a **one-second trailing-edge backend debounce**. For edits
at 0.0, 0.2, 0.5 and 0.8 seconds, only the last value is resolved/applied at about
1.8 seconds. `GridProfiles.set_value` updates intent and publishes it immediately,
saves normally, cancels the preceding task and creates a connector-scoped task
with `asyncio.sleep(1)`. No intermediate requested operating point is solved.
Entity services, automations and the card all enter this same path. Pure technical
bounds are independent of the request and share the solver's exact current grid.
Task epochs and intent generations fence late work, alongside the primitive
connection, authority and permission checks. OFF, profile changes, owner changes,
authority loss, disconnect and unload invalidate pending work. There is no delayed
permission ON. Explicit enable cancels the timer and applies the latest value
immediately; explicit permission OFF and a zero-power stop also bypass the timer.

Settings may be saved
for an inactive wallbox, but cannot dispatch power. Permission remains confirmed
hardware state. The primitive W target, approximation policy and installation
current limits remain available for advanced use; the next Grid start uses its
stored kW setting. Avoid competing intent writers. A manual primitive edit clears
vehicle assumptions and fences an existing profile observation.

The existing solver handles approximation, current steps, separate per-phase
maxima, fresh voltages, installation limits and phase retention. The primitive
runtime still owns protocol queues, sequencing, confirmation and connection,
authority and generation fences. Zero power uses the verified zero-current
contract without toggling permission. No PV or continuous regulation was added.

Grid reads the connector's existing `phase_switch_deviation_pct` setting (default
5%). With enabled, positively charging state, the solver retains the current
physical mode if its reachable power is within tolerance **and** satisfies
DOWN/NEAREST/UP. For example, 3p at 4.14 kW may be retained for a 4 kW NEAREST
request at 5%, but not at 3%, and not for DOWN. Disabled charging and an applied
OFF/0-A point do not retain a phase mode, even if old positive telemetry remains.
All current ceilings, supported modes, phase lockouts and cooldowns still apply.

Only a phase-switch lockout causes minute retries, using a valid substitute and
fresh inputs. Observation begins one minute after the desired point is applied,
not after a substitute. Current readings must be fresh, not future-dated, and
sampled after application. Missing readings are not zero.

For 3p, L2 and L3 both below 3 A trigger 1p recalculation; for 2p, L2 is checked.
The single-phase current envelope remains authoritative. For 1p, deviation over
2 A permits a better multiphase approximation, comparing offered power with
observed single-phase power. Each sequence permits at most one promotion and one
demotion: 1→3→1 and 3→1 terminate at 1p. Each transition re-enters the normal
application/lockout sequence. After completion there are no more commands until
a new start or power change. Switching owners cancels the old sequence too.

## Station capability configuration and migration

The central integration options now contain only the two battery references.
There is no central station picker for capability editing.

OCPP discovery creates a real HA **configuration subentry** for each station/EVSE/
connector, titled with that identity. In Settings → Devices & services → Wallbox
Manager, use that wallbox subentry's **Configure capabilities** / **Fähigkeiten
konfigurieren** reconfigure action. The form is already scoped to the wallbox.
Only capabilities missing from OCPP are shown. Explicit unsupported/malformed
OCPP evidence cannot be overridden. Complete inventory, including the expected
Wallbox01 inventory, yields an empty confirmation form. No vehicle fields exist.

This uses HA's supported [config subentries and reconfigure
flows](https://developers.home-assistant.io/docs/core/integration/config_flow/).
HA does not give this integration an independent arbitrary per-device options
handler. Subentries provide meaningful scoped configuration without fake devices,
duplicate listener entries or changes to existing device/entity identities.
Existing entities remain owned by their original central entry; the subentry
owns the capability configuration. Subentries are created by discovery, not a
manual "choose station" flow. Updates are read live by the capability resolver;
no extra listener restart or authority acquisition is needed.

Beta.1 `station_references[station][evse:connector]` move into matching subentries
on setup. Older explicitly associated values first pass through the existing
version-3 migration, then the same subentry migration. Existing subentries retain
their identity. Ambiguous/invalid old values remain quarantined under
`unassigned_references` and never select an arbitrary wallbox. Integration-wide
battery options and per-wallbox profile Stores are unchanged. No active wallbox
is guessed during migration; initial explicit takeover establishes it.

## Installation battery and reserve lifecycle

`min_soc_speicher` and `soc_speicher_aktuell` remain options of the central listener
config entry, shared by its wallboxes. They are not properties of EVSE 1/connector
1 and have no artificial hosting device. Both must be configured. The writable
reserve supports `number`/`input_number` with `set_value`; the SOC reference must
supply a finite numeric percentage from 0 to 100. Before writes, the backend
checks availability, service support and entity bounds. One missing reference
disables new battery overrides without affecting ordinary Grid charging.

Only the active, ready, actually charging connector can request an override.
Actual charging requires an active transaction, charging state and positive fresh
flow. The per-wallbox `min_soc` stays with that profile. On an episode's start,
read current reserve and SOC. If requested reserve is higher than current reserve,
journal the original before writing `min(requested reserve, current SOC)`, even
when SOC is below the original. An already sufficient reserve remains unchanged.
Changing `min_soc` during an episode is stored for the next episode.

Permission OFF, profile selection, active-wallbox switch, disconnect, finishing/
suspension, authority loss or unload restores the original, but only while the
entity still matches the temporary value. A different external value wins and is
not overwritten or reasserted during that episode. An external write of the exact
same numeric value cannot be distinguished. A resumed episode reads a new baseline.
Non-active Local charging never activates a reserve override.

The existing atomic journal survives restart, reload and reference changes.
Recovery restores before new activation. Failed restoration retains the journal;
there is one failed write attempt per runtime, with another attempt after a
referenced entity becomes available or reload. Failed activation is not retried
through every telemetry event. Unconfirmed writes remain journaled and diagnosed.
HA has no atomic compare-and-set number service, so an external write racing the
actual service dispatch remains a limitation. Switching owners waits for safe
reserve release rather than replacing a pending journal.

## Zero-configuration card and automatic resource loading

After HACS installation/update, restart Home Assistant and refresh the browser.
The integration serves its bundled JS directly at
`/wallbox_manager/wallbox-manager-card.js?v=<content hash>` using the supported
[asynchronous static-path API](https://developers.home-assistant.io/blog/2024/06/18/async_register_static_paths/).
It registers that URL with HA's `frontend.add_extra_js_url`, the public API for
custom integrations to load extra modules. `after_dependencies: frontend` orders
setup when the frontend is configured. Headless HA needs no frontend resources.
This works without editing Lovelace resource storage and does not depend on HACS
also installing this integration repository as a frontend repository. Updates use
a content hash to avoid stale browser assets. No dashboard card is inserted.

Add a card with only:

```yaml
type: custom:wallbox-manager-card
```

Optional presentation:

```yaml
type: custom:wallbox-manager-card
name: Wallbox
```

No `profile`, `power`, `permission` or `reserve` entity IDs are needed. Those beta.1
configuration keys are no longer part of the public interface. For an upgrade from
beta.1, **remove the old manually registered `/local/wallbox-manager-card.js`
resource once**, then refresh the browser; otherwise the old module can register
its custom element first. Future updates require no copy to `/config/www` and no
manual resource maintenance. No user resources are automatically deleted.

The backend active-wallbox select exposes the discovered topology and readiness.
Control entities expose integration-owned `wallbox_manager_role`, entry identity
and a stable `wallbox_manager_target` tuple. The card resolves their current entity
IDs from this metadata in HA states. It never guesses ID prefixes or matches
friendly names. Renames, additions and inventory changes are discovered on state
updates. Disconnected targets show inhibited controls. With multiple central
listener entries, their selects are views of the same global ownership coordinator.

The theme-aware header uses a 48 × 48 px `mdi:ev-station` (twice the original
24 px dimensions), the optional presentation title, and
the real device display name (`name_by_user`, then device/station name) beneath it.
The multi-wallbox selector lives in the header; single-wallbox cards omit it.
Takeover remains explicit. Both numeric fields use the former narrow reserve
width (4 em), including mobile layouts. Keyboard-accessible power buttons step by
0.1 kW below 10, and 1 kW above: 9.8 → 9.9 → 10 → 11 and the reverse. Direct input
also accepts fractions above 10. The card formats the HA language's decimal
separator. `technical_max_kw` comes from verified envelopes, fresh voltages and
effective current limits; the buttons clamp to it and direct overflow is rejected.
Unknown limits are not replaced with a fictitious 99/100 kW rating; backend
validation errors remain visible. Power edits stay interactive during service
responses. Reserve is labelled **Entladereserve / Discharge reserve**, with integer
steps from 0 to 100, and appears only with both central battery references.

Pressing either numeric control's +/- button applies one step immediately.
Holding repeats after 450 ms, then every 150 ms without acceleration. Pointer
capture handles mouse/touch release; cancellation, leaving the button, focus
loss, disabling the control, changing the selected wallbox, reconfiguration and
card removal stop repetition and clear timers. Pointer-generated clicks do not
apply a duplicate step. Native keyboard/assistive clicks remain supported. These
are input-repeat timers only: every power edit still uses the single existing
one-second backend debounce, applying only the final value after release.

A separated two-column, three-row section shows connection/charging state,
current session energy/measured power, and session duration/applied operating point.
Permission ON is never used as evidence of actual charging. Energy and power
continue using session accounting and actual meters with their freshness deadlines.

**Duration:** discovery uses `session_duration` and the existing scope metadata,
including renamed entities; no entity ID is hardcoded. Beta.3 treated the numeric
HA state as seconds unconditionally. HA can expose this seconds-native duration
sensor in minutes, hours or other duration units, so the card could advance at
only 1/60 or 1/3600 of the intended speed. The real HA entity regression confirms
its existing one-second backend tick works, including a registry conversion to
hours. The card now converts the state's advertised unit. It formats total hours
and minutes without seconds or a 24-hour wrap: `0:22`, `1:23`, `25:12`, `49:05`.

The duration entity also exposes a read-only sample timestamp and a 90-second
validity window for active sessions. The card's existing one-second display tick
can interpolate from that valid reading while active, even if HA publishes
periodically. It never extrapolates an unavailable, disconnected, unknown-start,
future-dated or expired source. Session duration continues during a charging
pause. On completion the final duration stays fixed, including while disconnected;
a new session uses the new entity state and session identity without cached offsets.
This is session elapsed time, not accumulated charging-active time.

**Applied operating point:** the bottom-right cell shows the confirmed phase count
and charging-current limit, for example `1-phasig · 16 A` / `1-phase · 16 A`. It is
not measured vehicle current. The existing stable control attributes
`applied_phase_count` and `applied_current_a` now project a read-only snapshot of
the primitive command boundary's successfully fenced `APPLIED` result. The snapshot
survives desired-power edits and the separately confirmed ON command. No control
policy, solver decision, command sequencing or retry depends on it, and it is not
persisted or restored.

During a phase lockout, a confirmed substitute remains displayed while the retry
waits: a desired 3p/9 A target with an applied 1p/16 A substitute displays 1p/16 A.
Once dispatch begins, the display is conservatively unknown until the complete
operation is confirmed; a rejected, partial, cancelled or stale result never
publishes the target. It changes to 3p/9 A only after confirmation. Explicit OFF
and takeover clear the snapshot. Connection/boot token, authority revision,
permission revision and active ownership guard its visibility; reconnect, reload,
lost authority or expired permission cannot resurrect a previous confirmation.
A confirmed zero-current point while permission remains ON displays `Off · 0 A`;
otherwise missing confirmation is a neutral dash. The cell's tooltip distinguishes
the current limit from a measurement. Measured charging power remains unchanged.

The display timer performs no service calls or power regulation.

Observation and session entities expose stable roles and scoped join metadata.
Connector observations take priority. EVSE/station aggregates are offered to the
card only when runtime topology maps them to exactly one connector; ambiguous
aggregates are omitted. Entity renames cannot redirect readings to another box.
Normal internal status messages are hidden. Actionable command, takeover and
battery errors remain visible. All backend guards apply independently of the card.

## Validation boundary

Tests exercise real OCPP simulated peers, HA config/entity behavior, reserve
recovery and isolated JavaScript discovery/rendering. A local browser preview
checks narrow light/dark layouts with mock HA components. These do not replace a real
HA browser or physical station test. Writable profile control retains the existing
OCPP 2.1 support; other OCPP versions' discovery support does not imply writable
profile support. No release version or tag is changed by this iteration.
