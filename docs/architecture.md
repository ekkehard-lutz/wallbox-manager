# Wallbox Manager architecture

Status: proposed design, 2026-09-20. This document specifies future behavior;
only the integration scaffold exists. No OCPP runtime is implemented here.
The [upstream adoption analysis](upstream-ocpp-analysis.md) records source evidence
and the exact upstream revision used. Implementation must update these documents
and the README as decisions become operational.

## Product and boundaries

`wallbox_manager` is the Home Assistant integration domain. OCPP is an internal
protocol implementation. A station contains EVSEs and connectors; preserve those
identities rather than flattening them into an assumed single connector. Control
is scoped to a controllable EVSE, with a station coordinator enforcing shared
physical limits and serializing station-wide operations. An adapter must declare
when a command affects the whole station. Such a command cannot override a
sibling EVSE's owner.

The first wallbox-stationary device is a reference fixture, not the model for all
chargers. Its phase switching and local-authority signals need verified device
contracts. Unknown capabilities disable the affected action; model names alone
never authorize it. Non-OCPP adapters implement the same internal contracts.

## Proposed package layout

All paths below are proposed beneath `custom_components/wallbox_manager/`;
this task does not create these runtime modules.

```text
__init__.py              HA setup/unload and config-entry runtime wiring
config_flow.py           integration configuration, validation and migration
const.py                 product identifiers, not protocol enums
manifest.json            Wallbox Manager identity and explicit dependencies
strings.json             canonical translatable UI messages
translations/en.json     English strings
translations/de.json     German strings
sensor.py                metering, ownership, solver and capability diagnostics
select.py                the five normal profile choices
number.py                capability-derived product settings, no direct OCPP calls
button.py                explicit supported maintenance actions
entity.py                shared snapshot subscriptions and stable identities
api.py                   versioned programmatic Energy Manager facade
runtime.py               per-entry lifecycle, adapter registry and task cleanup
core/
  models.py              station/EVSE/connector IDs and immutable snapshots
  capabilities.py        versioned capability evidence and operating envelopes
  events.py              normalized telemetry, boot, authority and result events
  coordinator.py         per-EVSE serialization and shared station constraints
  persistence.py         desired ownership/profile, owner authorization and LOCAL latch
control/
  profiles.py            OFF/PV_SURPLUS/PV_OPTIMUM/MAXIMUM/GRID policies
  requests.py            PowerRequest and rounding direction
  ownership.py           state transitions and command fencing
  leases.py              authenticated owner leases and monotonic deadlines
  controller.py          intent -> solve -> dispatch -> reconcile
solver/
  operating_point.py     feasible physical point and result reasons
  power.py               pure constrained candidate selection
  transitions.py         phase-switch hysteresis and dwell planning
protocols/
  base.py                adapter contracts, normalized results and events
  ocpp/
    common/
      transport.py       WebSocket lifecycle and explicit subprotocol selection
      metering.py        normalized samples, units, scope and timestamps
      sessions.py        transaction identity and connection generations
      profiles.py        owned OCPP profile IDs, purposes and expiration
    v16/adapter.py       OCPP 1.6J mapping and configuration discovery
    v201/adapter.py      OCPP 2.0.1 inventory and transaction mapping
    v21/adapter.py       OCPP 2.1 schemas and independently tested behavior
    extensions/
      registry.py       vendor/message/version routing and validation
      wallbox_stationary.py  verified first-device extension contract only
  extensions.py          extension interface for future non-OCPP protocols
```

HA entities and the programmatic API call the same core command boundary. Neither
may bypass ownership via an adapter reference. Core and solver modules must not
import HA or OCPP types. Adapters translate protocol objects into immutable core
events; entity updates subscribe to snapshots. Setup/unload owns all tasks,
subscriptions, timers and connections. No server starts as a side effect of import.

## Capability model

A capability snapshot is scoped to station/EVSE, firmware and connection/boot
generation, with a revision, timestamp, source and evidence state: unknown,
advertised, verified, unsupported or degraded. Separate configuration overrides
from observed support and retain the reason for each override. A timeout is not
proof of unsupported functionality.

Record supported phase modes, actual phase mapping, per-mode minimum/maximum
current and step, electrical/site limits, supported power/current schedule units,
profile purposes and stack constraints, stop/pause/start support, transaction
scope, and metering channels. For example, 1P 6–32 A and 3P 6–16 A are separate
envelopes, not a single 32 A number entity. Re-evaluate bounds whenever capability
revision or operating mode changes; reject stale writes at the core boundary.

Physical phase switching is a distinct capability with preconditions, safe
sequence, feedback and timeout. `numberPhases`/`phaseToUse` schedule fields do not
prove that a contactor changes the connected phases. Likewise, administrative
availability, charge permission, CP signaling/relay and transaction lifecycle are
separate capabilities. `ChangeAvailability` is not a universal CP relay switch.

Prefer standard protocol operations. Use a vendor extension only for functionality
that lacks an adequate standard mapping on that device. The registry keys handlers
by protocol version, vendor, message and device/firmware applicability. Validate
payloads, bound sizes and timeouts, reject unknown messages appropriately and
normalize results. Extensions cannot bypass ownership or hard electrical limits.
No arbitrary DataTransfer entity/service is an alternative control path.

## Control and power solving

```text
Profile Controller (or validated Energy Manager request)
  -> PowerRequest(target_w, direction, allowed)
  -> OperatingPoint Solver
  -> OperatingPoint(phases, current, offered_power)
  -> Protocol Adapter
  -> Wallbox
```

`target_w` is a finite, nonnegative charging target; bidirectional power is outside
this initial contract. `direction` means rounding (`DOWN`, `NEAREST`, `UP`), not
energy flow. `allowed=False` prohibits charging regardless of target. Attach
request identity, ownership epoch, capability revision and observation generation
to the command envelope. Requested, offered, acknowledged and measured power are
different fields. A successful protocol response does not prove physical execution.

Proposed profile semantics (numerical tuning remains future product work):

| Profile | Intent |
| --- | --- |
| OFF | Disallow charging; use a verified stop/pause operation, not assumed zero-amp support. |
| PV_SURPLUS | Follow fresh available PV surplus with DOWN; pause below viable minimum. |
| PV_OPTIMUM | Follow surplus with a configured minimum charging floor and bounded grid contribution; report that contribution. |
| MAXIMUM | Request the highest feasible operating point under all device/site limits. |
| GRID | Follow an explicitly configured grid charging budget, with DOWN to respect it. |

PV inputs need a defined sign convention, freshness and feedback accounting so
charger consumption is not counted twice. Missing/stale required inputs inhibit
the dependent profile and expose a reason; never silently fall back to MAXIMUM.
PV_OPTIMUM grid allowance and GRID budget must be configured before activation.

The pure solver enumerates current steps for each physically feasible phase mode.
Use integer step indices or decimal arithmetic, not float modulo. Intersect device,
installation and shared station envelopes. For balanced AC current, estimate
`offered_power = current * sum(active phase-to-neutral voltages)`; label this as an
estimate with its voltage/assumed power-factor basis. Prefer fresh measured phase
voltages. Nominal-voltage fallback is explicit and has a quality flag; line-to-line
conversion requires a verified topology. Do not infer switchable modes from the
number of nonzero meter readings.

Include OFF as a separate zero-power candidate when stopping is supported.
DOWN chooses the largest feasible offer at or below target; UP the smallest at or
above target; NEAREST minimizes absolute error, breaking ties toward the current
mode, then lower power. Hard safety limits always win. If the directional set is
empty, return `unreachable` with bounds and a suggested feasible point, never
silently claim that opposite rounding succeeded. A controller may request a new
explicitly relaxed target; a hard budget cannot be relaxed. Below minimum, DOWN
may select OFF, UP may select minimum if allowed, and NEAREST compares both.

Example at 230 V, 1P 6–32 A, 3P 6–16 A, 1 A steps, both modes currently eligible:
a 4,000 W request yields 1P/17 A/3,910 W with DOWN or NEAREST and
1P/18 A/4,140 W with UP (tie against 3P/6 A favors current 1P mode).
At 500 W, DOWN and NEAREST choose OFF and UP chooses 1P/6 A/1,380 W.
Above 11,040 W, UP is unreachable in this envelope.

Current operating state, switching hysteresis and minimum dwell time restrict
eligible transitions. Return `deferred` and a retry deadline when an otherwise
feasible mode is temporarily unavailable. Hard-limit reductions and emergency
inhibition take precedence over dwell. Phase switching requires a verified
stop/reduce, zero-current confirmation, switch, feedback and controlled resume
sequence; a timeout inhibits further charging commands and reports uncertainty.

EV consumption below offered power is normal. Expose requested/offered/measured
values and sustained underconsumption; do not repeatedly increase the limit or
switch phases just because the EV is full, tapering or internally constrained.
Re-solve on relevant inputs with debouncing, never replay a stale solved point.

## Ownership state machine

A technical interruption must not erase an existing user control decision.
Explicit local user takeover always takes priority over automatic recovery.
Separate the following state dimensions rather than using one owner field:

| Dimension | Meaning and lifetime |
| --- | --- |
| Persistent desired control | `desired_owner = LOCAL | WALLBOX_MANAGER | REMOTE`, selected normal profile (`OFF`, `PV_SURPLUS`, `PV_OPTIMUM`, `MAXIMUM`, `GRID`), and the registered authorized Energy Manager owner when REMOTE is desired. Survives technical interruptions. |
| Runtime active ownership | `active_owner = NONE | WALLBOX_MANAGER | REMOTE`; permission actually established for this runtime/device session. Never inferred solely from persisted intent. LOCAL is represented by the latch/device authority, with no active software owner. |
| Runtime REMOTE lease | Owner-bound opaque token, ownership epoch and monotonic deadline. Process/session scoped; never persist or restore token/deadline. |
| Device/local authority | Fresh device-reported authority evidence plus the durable deliberate-LOCAL latch. Connectivity, desired state and actual device authority are separate facts. |
| Reconciliation/control status | `ACTIVE | ACQUIRING | INHIBITED | RECONCILING`, including pending recovery, unavailable telemetry and failure reasons. A remembered desired profile is not a claim that it is executing. |

LOCAL and REMOTE are never profile-select options. Display desired ownership/profile,
actual authority and control status separately, so REMOTE/RECONCILING cannot be
mistaken for an active lease. A remembered normal profile while REMOTE or LOCAL is
desired is historical preference, not an automatic fallback.

### Persistence and the LOCAL latch

Persist desired control, station/EVSE identity, authorized registered owner identity
and authorization revision, profile settings, and the deliberate-local latch in a
schema-versioned record. Commit explicit selections/takeovers and revocations at
the serialized control boundary before treating the new authorization as usable.
Persist no runtime lease credentials/deadlines, in-flight commands or solved
operating points as executable recovery state. Lost/corrupt persistence or an
identity mismatch leaves control inhibited, not implicitly authorized.

A verified deliberate local takeover sets the durable latch, changes desired
ownership to LOCAL, invalidates remote recovery authorization and removes active
software ownership. Restore LOCAL after HA restart and retain it across OCPP
reconnect and wallbox reboot. Disconnect, idle state, rejection and suspension do
not themselves prove LOCAL. A fresh device report of LOCAL always wins, including
when takeover occurred while HA was offline.

After a wallbox reboot, verified device evidence may establish that physical local
authority no longer exists. Update the observed authority accordingly, but retain
the deliberate-LOCAL recovery block and desired LOCAL decision: that evidence alone
is not a new user authorization to acquire control. Leaving the latched LOCAL state
still requires a new explicit user action, such as selecting a normal profile.
Neither automatic reconciliation, a recovery handshake nor a heartbeat may clear
that block. If authority cannot be determined reliably, remain inhibited; do not
fabricate a standard OCPP authority signal.

Every mutating request carries an ownership epoch and runtime/session generation.
Under one serialization boundary, validate desired authorization revision, active
ownership, lease, capabilities and connection generation before dispatch and again
before committing a result. Local takeover advances the epoch, invalidates leases
and recovery attempts, cancels queued work and wins over late acknowledgments.
An already-transmitted command cannot be recalled; discard its late result and
reconcile without automatically leaving LOCAL. The device must prioritize its local
control signal for physical enforcement; document devices without that guarantee.

### Transitions and recovery

| Event/transition | Required behavior and failure handling |
| --- | --- |
| LOCAL -> normal profile | Only a new explicit user selection can attempt authority acquisition. Keep the local latch until acquisition is confirmed; refusal/timeout leaves LOCAL and a visible error. No background retry that could later leave LOCAL. On success persist the selected profile and WALLBOX_MANAGER desired ownership and clear the latch. |
| REMOTE -> normal profile | Persist the new desired profile/WALLBOX_MANAGER ownership, revoke remote recovery authorization, invalidate the lease and fence pending targets/handshakes immediately. Activate only after validated execution; failure remains inhibited with the new desired profile and no revived lease. |
| Normal profile -> LOCAL | Verified local takeover persists LOCAL and its latch, removes manager authority and cancels work. No automatic reacquire or availability command. |
| REMOTE -> LOCAL | Atomically persist LOCAL, revoke remote recovery authorization and lease, advance epoch and cancel work. Old owner targets, heartbeats and recovery requests fail even before the old lease deadline. |
| Initial REMOTE acquisition | Explicit authorized Energy Manager takeover while manager control is verified and active. Persist desired REMOTE and registered owner authorization; allocate a fresh lease. Remain ACQUIRING/inhibited until a fresh valid target is solved and applied. Reject competing owners and acquisition from LOCAL; a user must first select a normal profile. |
| REMOTE recovery | Recover only the persisted, still-authorized registered owner through the handshake below, after device reconciliation. Issue a new token/deadline and require a fresh target before REMOTE becomes ACTIVE. No new user click; no heartbeat-only acquisition. |
| REMOTE heartbeat | Refresh only an unexpired matching runtime lease. A heartbeat never acquires authority, performs recovery or revives an expired lease. |
| Active lease timeout | If no technical-recovery episode has been entered, revoke the lease and remote recovery authorization, persist WALLBOX_MANAGER/OFF intent and inhibit. Attempt verified stop only while authority is confirmed; report uncertainty on failure. Never resume an earlier profile. A new takeover needs explicit authorization. This is distinct from invalidating a lease because a technical interruption started recovery. |
| Explicit remote release | Validate lease, revoke remote recovery authorization and lease, persist WALLBOX_MANAGER/OFF intent and inhibit. Do not automatically resume an earlier charging profile. |
| HA restart/reload | Restore desired control and the LOCAL latch, never active ownership or old leases. Reconcile identity, boot/session generation, capabilities, fresh telemetry and authority. LOCAL remains blocked. Otherwise automatically re-establish safe control for the desired normal profile, or await the authorized REMOTE recovery handshake. |
| OCPP disconnect/reconnect | Fence in-flight commands/results, invalidate their connection generation and any runtime lease, clear active ownership and enter RECONCILING/unavailable. Preserve desired profile/ownership and remote recovery authorization; do not infer LOCAL. On reconnect perform fresh reconciliation, then automatically recover the normal profile or use the REMOTE handshake. Verified LOCAL overrides both. |
| Wallbox restart | Invalidate transaction/session-specific state, leases, commands and capability evidence, while preserving desired control authorization. Rediscover and verify actual authority; LOCAL wins. Otherwise automatically resume the desired normal profile or recover REMOTE by handshake. Inspect owned OCPP profiles/settings without assuming they survived or disappeared. |
| Recovery timeout | End the bounded recovery attempt and fence provisional leases/targets. Stay INHIBITED with OFF safety intent and a visible failure; retain desired state for diagnosis/retry, not execution. Do not apply an old target or silently fall back to a historical normal profile. |
| Command timeout/rejection | Report pending/unknown or refused result, not successful state. Inhibit the affected action and reconcile. Retry only with current authorization and freshly validated intent; failed attempts to leave LOCAL always need a new explicit user action. |

Technical recovery begins with read-only identity and authority verification. A
valid persisted normal-profile decision authorizes subsequent safe re-establishment
of control without a new user selection, provided LOCAL is not active/latched and
fresh device evidence permits it. Reconcile owned protocol profiles and settings,
then rerun the profile controller and target-power solver against current telemetry,
capabilities and installation limits. OFF also survives and resumes as OFF. Never
replay a stored target/physical operating point or blindly enable availability.
Unknown authority, incomplete discovery or stale inputs keep RECONCILING/INHIBITED;
a normal profile may resume automatically when those prerequisites are satisfied.

REMOTE uses a bounded recovery episode with a configurable timeout (value to be
chosen during implementation), starting when technical recovery begins. Handshake
and first fresh target must complete within that window; repeated heartbeats or
connection flaps within an episode do not extend it. At expiry, report recovery
failure and stay safely inhibited/OFF. Desired REMOTE authorization can remain
recorded, but a late target/heartbeat cannot reactivate control. A new explicit
recovery attempt by the same authenticated authorized owner can open a new bounded
window without a user click; it must repeat all checks. LOCAL, user profile selection,
release or active-lease expiry revokes that eligibility. Timer and disconnect events
serialize: an already-expired active lease cannot be relabeled technical recovery
to revive revoked authorization.

OFF safety intent is not a guarantee that an unreachable charger stops. During
active control use device-enforced expiring limits/watchdogs where verified, with
an explicit end-of-validity behavior. Expiration alone might remove a limit and
allow more charging. Devices lacking a verified fail-safe must expose that
limitation; never promise software lease expiry or recovery timeout physically
stops an offline wallbox. While actual authority is unknown or LOCAL, do not send
an unauthorized stop command in the name of recovery.

## Programmatic Energy Manager API

An API handle is scoped to a runtime and EVSE; callers do not write HA entity states.
Separate first-time takeover from recovery of an existing persisted authorization:

```text
async_acquire_remote_control(owner_id=...) -> Lease(token, epoch, expires_in)
async_recover_remote_control(owner_id=..., recovery_id=...) -> Lease(token, epoch, expires_in)
async_set_remote_target_power(owner_id=..., lease_token=..., watts=..., direction=...)
    -> TargetResult(requested, offered, measured, status, reasons)
async_remote_heartbeat(owner_id=..., lease_token=...) -> LeaseStatus
async_release_remote_control(owner_id=..., lease_token=...) -> ReleaseResult
```

Bind `owner_id` to a registered authenticated caller; a string is not authentication.
All calls serialize with user selections, local events, recovery and lease timers.
Initial acquisition records explicit takeover authorization. Recovery instead
validates that desired ownership is still REMOTE for exactly this registered owner,
station/EVSE and authorization revision. It cannot create authorization for a new
owner. HA and Energy Manager restarts do not require a new user click when that
persisted authorization is still valid.

The recovery handshake obtains a current runtime recovery ID/challenge, authenticates
the registered owner, verifies the persisted authorization and completes device
reconciliation, including absence of a LOCAL block. Bind the response to the current
runtime, recovery attempt and connection/boot generation; reject obsolete handshake
responses and serialize concurrent attempts. Issue a fresh opaque lease token and
fresh monotonic deadline. Do not reuse the old token, epoch/deadline or target.
The owner must then submit a current target using that new token. Solve against fresh
capabilities and telemetry and confirm the new command outcome before reporting
REMOTE/ACTIVE. Until then charging control remains inhibited; heartbeat alone cannot
complete this transition. A boot/disconnect/local event during any step fences the
attempt and its results. Revoked/changed owner registration denies recovery.

Proposed active lease TTL is 30 seconds with heartbeats at most 10 seconds apart,
measured on a monotonic clock. Reject at `now >= deadline`; target changes do not
extend it. Recovery timeout is separate and also bounds the provisional period
between lease issuance and first valid target. No heartbeat may extend that recovery
window. Tokens differ even for the same owner across attempts and are not persisted.
API errors distinguish unauthorized owner, stale lease, stale recovery attempt,
recovery timeout, local control, unavailable device, invalid input, unsupported
capability and unreachable target. Tokens and credentials are redacted in diagnostics.

## Metering and Home Assistant presentation

Normalize both periodic and transactional metering into samples containing station,
EVSE, optional connector, transaction ID, source timestamp, received timestamp,
sequence where available, measurand, phase, location, context, unit, multiplier,
value and quality. Retain raw meaning alongside normalized units. EVSE-only or
station-only readings must not be assigned arbitrarily to connector 1.

Preserve per-phase samples as first-class channels and expose them as HA sensors
alongside useful aggregates. Explicit totals and derived totals remain distinguishable;
do not add a reported total to its phase readings. Sum compatible active-power or
energy samples only across matching time/scope/context; voltage and current need
explicitly named average/max diagnostics rather than misleading sums. Missing is
not zero. Do not overwrite newer live state with delayed transaction samples;
retain their historical meaning separately with bounded storage. Counter resets,
signed/export values, duplicate events and out-of-order samples need explicit rules.

Profile select contains only the five normal profiles; read-only sensors expose
desired ownership/profile, active ownership, device authority, lease/recovery status,
pending actions, capability limitations and solver reasons.
Dynamic entity bounds come from snapshots and are revalidated on write. Preserve
stable IDs across reconnects and discovered capability changes. UI text and errors
use `strings.json` with matching `translations/en.json` and `translations/de.json`;
keep machine-readable codes untranslated. Never expose lease credentials as entities.

## Delivery and verification plan

Implement pure capability, ownership and solver contracts first, with fake adapters;
then versioned protocol adapters, HA presentation and device-specific extensions.
Test each adapter against the same normalized contract suite. OCPP 2.1 needs its
own schema/library compatibility proof; advertising its subprotocol is insufficient.
No production code is adopted by this analysis and no changelog is created before
v1.0.0.

Required future tests include every transition above; stale same-owner tokens;
local takeover during acquisition/dispatch; restart persistence and corrupt state;
automatic resumption of each normal profile after HA restart, reconnect and reboot;
REMOTE recovery with a fresh lease and fresh target; no activation from heartbeat
alone; wrong/revoked registered owners; stale recovery challenges; local takeover
during recovery or while offline; recovery timeout and late messages; lease-expiry
versus disconnect ordering; heartbeat exactly at expiry; simultaneous owners;
shared station constraints; solver rounding, min/max/step and different 1P/3P envelopes; stale voltages;
phase-switch dwell/failure; EV underconsumption; and per-phase periodic and
transactional readings, including duplicates, multipliers and scope ambiguity.

Current development gates are `ruff check`, `ruff format --check` and `pytest`
(using `.venv/bin/` locally). A small scaffold test checks translation key and
placeholder consistency now. Runtime tests and CI automation must arrive with the
first runtime changes, not after release. These checks do not establish protocol
conformance or hardware safety; device behavior requires simulator and hardware
verification before enabling control.
