# Grid (`NETZ`) profile and installation ownership

This is the implemented beta.2 refinement of the v0.3.x Grid contract. It
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
(0–100 kW) and `min_soc` (0–100%). Switching active wallboxes does not copy settings.
The select currently offers only NETZ. Selecting/reselecting the profile requests
permission OFF and invalidates work; it never acquires authority.

After takeover, choose Grid, adjust power and enable charging. While active and
enabled, power edits immediately resolve and apply a point. Settings may be saved
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

The card displays profile, power and permission, plus reserve/actual charging only
with battery configuration. Authority takeover, failed transitions, phase lockouts,
observations and battery errors remain visible. The card merely invokes backend
operations; all exclusivity and stale-command rules hold without it. Use standard
HA cards for additional metering.

## Validation boundary

Tests exercise real OCPP simulated peers, HA config/entity behavior, reserve
recovery and isolated JavaScript discovery/rendering. They do not replace a real
HA browser or physical station test. Writable profile control retains the existing
OCPP 2.1 support; other OCPP versions' discovery support does not imply writable
profile support. No release version or tag is changed by this iteration.
