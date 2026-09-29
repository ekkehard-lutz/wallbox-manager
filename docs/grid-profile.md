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

The coordinator extends the existing HA Store `wallbox_manager.active_wallbox`
with a versioned `ownership` record written only after successful explicit takeover
and OFF confirmation. It records entry/station/EVSE/connector identity, observed
station vendor/model/serial/firmware, explicit permission intent, and PV battery
continuation state scoped to the external transaction. Per-wallbox profile and
parameters remain in the existing profile Store, without a second settings source.

- `ready`: explicit takeover completed, or historical ownership was reconciled
  with fresh compatible runtime evidence;
- `transition`: an explicit activation is running and profile commands are inhibited;
- `status`: readiness/recovery or a specific rejection, exposed by the active-wallbox select.

Legacy records containing only `active_wallbox`, absent/corrupt ownership records,
and unknown ownership versions remain inhibited and require explicit takeover.
Migration does not grant ownership. Unknown authority, transport disconnect and
changed runtime generations suspend live readiness and fence pending work, while
preserving legitimate ownership history and permission/profile intent. Fresh Remote
and ChargingEnabled evidence reconcile that same record automatically on reconnect.
An observed Local transition or mismatched station identity durably revokes history;
a later Remote transition cannot revive it. HA shutdown/reload preserves history
through its existing deliberate suspension path.

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
The select offers NETZ and, when both power references are configured, PV Surplus. Selecting/reselecting the profile requests
permission OFF and invalidates work; it never acquires authority.

After takeover, choose Grid, configure optional timing and enable charging.
Fixed power remains configurable through its existing entity. While active and
enabled, power edits use a **one-second trailing-edge backend debounce**. For edits
at 0.0, 0.2, 0.5 and 0.8 seconds, only the last value is resolved/applied at about
1.8 seconds. `GridProfiles.set_value` updates intent and publishes it immediately,
saves normally, cancels the preceding task and creates a connector-scoped task
with `asyncio.sleep(1)`. No intermediate requested operating point is solved.
Entity services, automations and the card all enter this same path. Pure technical
bounds are independent of the request and share the solver's exact current grid.
Task epochs and intent generations fence late work, alongside the primitive
connection, authority and permission checks. OFF, profile changes, owner changes,
authority loss, disconnect and unload invalidate pending work. NETZ timing gates the requested power through the same control path.
Explicit enable applies the currently scheduled value; explicit permission OFF and a zero-power stop also bypass the timer.

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
contract without toggling permission. PV regulation uses the same control boundary.

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

The central integration options use native General parameters and Regulation
parameters sections, including battery/power references, diagnostics and PV tuning.
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

Only the active, ready, actually charging connector using Grid (`NETZ`) can
request an override. PV Surplus never requests an override, including with legacy
stored `min_soc` settings. Its SoC threshold is only an eligibility threshold. Actual charging requires an active transaction, charging
state and positive fresh flow. The existing session ledger selects connector,
unambiguous EVSE and TransactionEvent power; embedded transaction metering does
not need a duplicate ordinary MeterValues channel.

The per-wallbox `min_soc` is the **Grid charging reserve**, independent of the
PV storage target `soll_soc_speicher` and its `soc_hysterese`. Before the first
override, capture the installation's actual reserve and persist it atomically.
The temporary value is `max(original, downsize(min(profile reserve, actual SoC)))`.
Downsize uses the number entity's step grid anchored at its minimum, with a
one-percentage-point default when step metadata is absent; its maximum also
bounds the request. Thus original 20, requested 40 and SoC 65 yields 40; SoC 37.8
with step 1 yields 37; SoC at/below 20 never lowers the original. SoC must be
finite, 0–100 and reported within 90 seconds. Unavailable, restored or explicitly
expired entity evidence cannot authorize a write. A constant reserve number does
not expire merely because its value has not changed.

Owned overrides follow changed profile reserve/SoC only when the down-sized value
changes; repeated identical evaluations issue no writes. Failed writes do not
create a regulation-cycle retry loop. The journal retains the previous and pending
temporary values during adjustment so a failed write cannot lose the original.
Reserve writes and restoration subscribe to HA state changes before dispatch and
allow up to ten seconds for service completion and matching live numeric readback.
The old value during this window is `confirmation_pending`, not a failure. No
polling loop or repeated write is used. A timeout is `write_unconfirmed`; a service
exception is `error`. The normal battery state-event reconciliation clears either
obsolete error once the current journal target is confirmed, even after timeout.
Pending adjustment targets survive reload; superseded targets cannot confirm a
newer adjustment. External changes still release the override. As before, the HA
number interface has no operation ID: a matching live numeric value is the
available confirmation boundary, not proof of which writer produced it.

Permission OFF, profile selection, owner switch, vehicle/session end, observed
suspension, authority loss and ordinary control termination restore the original,
but only while the entity matches the value written by Wallbox Manager. External
changes win, including an external return to the original; no reassertion occurs
during that charging episode. An external write of exactly the same numeric value
cannot be distinguished. HA has no atomic compare-and-set number service, so an
external write racing actual service dispatch remains a limitation.

A normal owned HA shutdown/reload preserves the existing journal and override.
Startup waits for the existing ownership reconciliation and fresh charging evidence,
then compares/adopts the override without `40 → 20 → 40` oscillation. Rejected
ownership or persisted OFF releases it through the same compare-before-restore
path. Missing startup evidence leaves restoration/reconciliation pending; it grants
no new authority. Without legitimate retained ownership, load/unload restores as
before. Changed entity references restore the journaled old entity before any new
override. Failed restoration retains the journal and permits another attempt when
the entity returns; owner switching waits for safe reserve release.

Entity attributes include `battery_status` and `battery_reserve` diagnostics:
original/profile/observed/desired reserve, SoC, write count/last value, pending
restoration and error reason. Unchanged cycles do not emit repeated error logs.

### Electrical display after recovery

Continuing hardware charging alone does not establish a confirmed current limit.
The five-second `Connector.PhaseRotation` NotifyEvent cache may be absent after
restart. Recovery now reads that same scoped Actual variable with GetVariables,
then validates the fresh A-unit Composite Schedule against physical phase,
voltage, capabilities, limits, permission, authority and generation. Unsupported,
ambiguous, expired or mismatched evidence keeps recovery waiting. There is no
fallback from measured EV current or historical watts.

A validated point is adopted into the existing confirmed-point store and published
immediately through `applied_phase_count` and `applied_current_a`. A matching
3p/8A schedule therefore appears without changing desired power or issuing another
operating-point write. `electrical_recovery_status` distinguishes missing evidence,
rejected/stale readback, phase/voltage problems, rejected electrical resolution and
successful adoption. GetVariables and GetCompositeSchedule support still require
hardware validation; neither read changes CP or charging permission.

## Zero-configuration card and automatic resource loading

After HACS installation/update, restart Home Assistant and refresh the browser.
The integration serves its bundled JS directly at
`/wallbox_manager/wallbox-manager-card.js?v=<content hash>` using the supported
[asynchronous static-path API](https://developers.home-assistant.io/blog/2024/06/18/async_register_static_paths/).
It registers that URL with HA's `frontend.add_extra_js_url`, the public API for
custom integrations to load extra modules. `after_dependencies: frontend` orders
setup when the frontend is configured. Headless HA needs no frontend resources.
This does not depend on HACS also installing this integration repository as a
frontend repository. Old entries for the integration-owned local route are
removed through the Lovelace collection API; the extra module is the sole
automatic loader. YAML lists and unrelated resources are untouched. Updates use
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
manual resource maintenance. See [registration lifecycle and migration](frontend-registration.md).

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
Takeover remains explicit. The NETZ card exposes only discharge reserve, optional
start delay and optional charging duration. The reserve uses integer steps from
0 to 100 and appears when its battery references are configured. Duration inputs
use `hh:mm`. PV Surplus shows only battery target SoC. Existing power and
approximation entities remain available for advanced use; technical regulation
parameters live in integration settings.

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
survives desired-power edits and the separately confirmed ON command. No applied
snapshot is restored from disk. Recovery may establish a new confirmation using
fresh, fenced effective-schedule readback; measured EV current alone is insufficient.

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
Normal internal status messages are hidden. The confirmed operating point appears
under **Wallboxparameter / Wallbox parameters**. The permanent **Meldungen / Messages**
section follows it, showing `-` when empty. Existing command, takeover, battery and
input/service errors appear there once, retaining their existing red styling.
All backend guards apply independently of the card.

## Validation boundary

Tests exercise real OCPP simulated peers, HA config/entity behavior, reserve
recovery and isolated JavaScript discovery/rendering. A local browser preview
checks narrow light/dark layouts with mock HA components. These do not replace a real
HA browser or physical station test. Writable profile control retains the existing
OCPP 2.1 support; other OCPP versions' discovery support does not imply writable
profile support. No release version or tag is changed by this iteration.

## Restart and reload reconciliation

The same recovery path handles a new HA runtime, integration reload and a station
reconnect within a running HA instance. A temporary network outage, wallbox service
restart, Pi reboot or power cycle does not itself revoke prior explicit ownership.
On disconnect, execution is inhibited and old recovery tasks are cancelled; on
return, fresh evidence starts a new recovery using the existing ownership record. Fresh Remote/OCPP
authority, matching entry/station/EVSE/connector and station identity, and fresh
ChargingEnabled observation must agree with a valid historical ownership record.
Remote alone never establishes ownership. Recovery sends no takeover and no
unconditional OFF. Stored permission OFF does not enable the station; if fresh
hardware permission conflicts with stored OFF, ordinary fenced OFF reconciles it.
Stored ON with hardware OFF follows the existing explicit-enable/startup path only
after required fresh inputs are available.

For an already enabled station, recovery reads standard OCPP GetCompositeSchedule
(A, 60 seconds) to obtain its effective electrical limit. It accepts only a current,
EVSE-matching, single constant balanced period, with fresh verified physical phase
feedback and an unambiguous live transaction. Fresh capabilities, voltage and
current limits must validate that point; authority, permission, transaction and
intent generations are fenced through the read. This read-only operation does not
change existing command payloads or phase/current transition semantics.

If readback already satisfies the restored PV intent, normal regulation reuses the
confirmation and does not replay a command. PV continuation and battery latch are
restored only for the same external transaction. A different/new session follows
the normal strict start policy. No synthetic OFF/ON cycle is introduced.

If readback is unsupported, ambiguous or unsafe, ownership/intent can still be
restored, but electrical regulation waits in `recovery_waiting_electrical`; reads
retry at 60 seconds without changing the running charge. Missing PV/runtime inputs
wait at the configured regulation interval. The integration does not infer a
current limit from measured draw. This is a hardware-validation requirement for
non-disruptive adoption. Existing phase/current writes are unchanged.

The current protocol exposes no persistent authority-transition counter. A
Remote -> Local -> Remote transition entirely while HA or the station/transport is
offline is indistinguishable from uninterrupted Remote if the station returns with
the same identity and fresh
authority evidence. Recovery cannot detect that history. Online Local transitions
are observed, revoke persisted ownership, and continue to require explicit takeover.

Fresh connector and charging observations are independent of the electrical retry.
They update immediately without a power/profile command or a 60-second delay. If a
vehicle departed during the outage, fresh Available/Idle evidence replaces retained
occupancy/charging, and the ended transaction prevents adopting a stale running
session. Recovery waits for the normal runtime prerequisites. If the effective
point changed, fresh phase/schedule/voltage evidence validates the actual point
before the active profile reconciles its target. Matching points require no duplicate
charging command; a changed target can require an ordinary fenced profile command.
There is no blind replay of the old electrical point or synthetic OFF/ON cycle.

## Relative NETZ timing

Start delay and charging duration are optional `hh:mm` durations, **not clock
times**. Empty delay starts immediately; empty duration means unlimited. Delay
alone waits then charges indefinitely; duration alone charges immediately for the
specified duration; both wait first then count duration from the scheduled start.
`01:30` plus `02:00` means wait 90 minutes then charge for two hours. Hours may
exceed 23; minutes must be 00–59. Explicit `00:00` duration expires immediately.

The profile Store persists seconds and an absolute activation timestamp. Selecting
NETZ or materially changing its timing starts a new activation. Selection retains
the existing permission-OFF behavior: the user must enable permission separately.
The scheduled start is activation plus delay; the end is start plus duration,
independent of command latency and temporary unavailability. Restart/reload keeps
these deadlines and counts downtime; existing ownership recovery must still prove
control eligibility. No persisted timing record ever grants authority or permission.

Each schedule task carries the profile epoch. Selection, timing edits, OFF, loss
of authority/connection/ownership and unload invalidate/cancel the task. Switching
away and back creates a fresh activation. Expiry edits target power to zero through
`apply_stored`; it does not call hardware directly or change the OCPP protocol.
The normal capability, permission and command-generation fences remain in force.
A pre-dispatch gate also rejects positive NETZ points while waiting or expired.
Transient command failures retain the existing 60-second retry interval.

Profile attributes show activation UTC time, configured delay/duration seconds
and waiting/active/expired state. An expired activation stays expired until a new
selection, timing edit or explicit OFF followed by ON. Configuration edits with
no control eligibility are stored without sending commands.
