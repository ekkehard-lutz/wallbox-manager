# Wallbox Manager architecture

Status: design with initial pure-core implementation, 2026-09-23. Immutable
identity/capability/request contracts, the operating-point solver and the
protocol-independent control command boundary are implemented.
The read-only OCPP transport/discovery and scoped metering/runtime-state foundations
and persistent session tracking are implemented. OCPP 2.1 transaction-scoped
charging OperatingPoint dispatch and the first v0.2.x manual HA intent/runtime
path are implemented; remaining runtime behavior below is planned.
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

Paths below are beneath `custom_components/wallbox_manager/`. The core models,
capabilities, control requests/commands, solver, generic discovery/runtime
snapshots, manual intent runtime and OCPP adapters now exist; other modules below
are proposed unless marked as implemented.

```text
__init__.py              HA setup/unload and config-entry runtime wiring
config_flow.py           integration configuration, validation and migration
const.py                 product identifiers, not protocol enums
manifest.json            Wallbox Manager identity and explicit dependencies
strings.json             canonical translatable UI messages
translations/en.json     English strings
translations/de.json     German strings
sensor.py                metering, ownership, solver and capability diagnostics
select.py                implemented power approximation direction
number.py                implemented desired watts and phase-retention tolerance
switch.py                implemented requested charging permission
control_entity.py        implemented stable EVSE identities and intent restoration
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
  profiles.py            OFF/PV_SURPLUS/PV_OPTIMUM/PV_MAXIMUM/GRID policies
  energy_inputs.py       normalized energy-input snapshots and validity rules
  requests.py            PowerRequest and rounding direction
  commands.py            async control adapter contract, outcomes and validity fence
  runtime.py             implemented manual intent -> solve -> dispatch
  reference_wallbox_stationary.py  explicit operator-attested reference source
  ownership.py           state transitions and command fencing
  leases.py              authenticated owner leases and monotonic deadlines
  controller.py          intent -> solve -> dispatch -> reconcile
solver/
  operating_point.py     feasible physical point and result reasons
  power.py               pure constrained candidate selection
  transitions.py         phase-switch hysteresis and dwell planning
protocols/
  base.py                future shared protocol adapter lifecycle
  ocpp/
    common/
      transport.py       WebSocket lifecycle and explicit subprotocol selection
      metering.py        normalized samples, units, scope and timestamps
      sessions.py        transaction identity and connection generations
      profiles.py        owned OCPP profile IDs, purposes and expiration
    v16/adapter.py       OCPP 1.6J mapping and configuration discovery
    v201/adapter.py      OCPP 2.0.1 inventory and transaction mapping
    v21/adapter.py       OCPP 2.1 schemas and independently tested behavior
    v21/control_runtime.py  live EVSE binding and measured voltage normalization
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

## Implemented control command boundary

`control.commands.apply_operating_point` forwards an already actionable
`solver.operating_point.OperatingPoint` unchanged to a scope-bound asynchronous
`ControlAdapter`. It does not solve again or apply profile/HA/energy policy.
Charging permission, current setpoint and physical phase mode remain distinct:
OFF carries neither phases nor current and is never converted to a generic 0 A
command. A whole point is one coordinated adapter operation, allowing a future
adapter to handle transitions such as 1p/16 A to 3p/7 A without exposing independent
phase/current commands to a coordinator.

The immutable `CommandResult` contains a `CommandStatus`, affected `ControlArea`,
optional `CommandReason` and protocol-neutral diagnostic text. APPLIED means the
whole operation was confirmed, not that the EV consumes the offered power.
TEMPORARILY_REJECTED (for example busy or phase-switch lockout) is normal runtime
behavior. UNSUPPORTED signals missing/inconsistent capability knowledge at runtime.
FAILED represents a technical/protocol/hardware failure. These are returned values;
invalid arguments/results and unexpected internal errors may raise exceptions.

The caller supplies a synchronous `is_current` predicate capturing its target
generation. It must stay false once superseded. The boundary checks it before
calling the adapter; adapters must recheck after queue/lock waits and immediately
before each device side effect. A stale target returns TEMPORARILY_REJECTED with
STALE. Device-specific serialization and atomicity remain the adapter's job.
This cooperative fence cannot retract an already dispatched command or guarantee
rollback of a partial operation. The future controller must also fence late
results before publishing active state and reconcile partial operations.

The live v21 station adapter exposes `bind_control(target, capabilities,
phase_operation_evidence=...)`, returning an EVSE-bound `ControlAdapter`. The
validated target is an `EvseId` for this station with a canonical positive numeric
OCPP EVSE ID. Multiple bindings share the live station's OCPP queue; there is no
implicit EVSE 1 binding. Each binding is valid only for its captured connection
and boot generation; reconnect/reboot requires a new binding and fresh evidence.

`capabilities` is a synchronous provider of the existing `CapabilitySnapshot` (or
None), scoped exactly to the EVSE and matching generation and firmware. It supplies
the existing normalized model; no parallel capability registry is added. Only a
VERIFIED `ChargingEnvelope` for the exact physical `PhaseMode` authorizes its
current range/grid: `min_current_a + n * current_step_a`, bounded by maximum.
The adapter validates an already solved point; it does not solve or apply policy.
Fractional currents are allowed when on that grid. The pinned OCPP schema accepts
JSON numbers; the adapter checks the serialized decimal against the exact Fraction.
Values such as 6.5 and 6.1 are representable; a value such as 19/3 is UNSUPPORTED,
never rounded or truncated to a different setpoint.

The existing snapshot has no protocol phase-operation/transition evidence field.
The minimal additional dependency is a synchronous, side-effect-free
`phase_operation_evidence(snapshot, requested_mode)` provider returning existing
`CapabilityEvidence` (or None). VERIFIED must establish that sending numberPhases
for this physical mode preserves the correct conductors and safely performs any
required transition from the current device state. This proof is distinct from
an envelope: two verified modes alone do not prove switchability between them.
A fixed-phase device may verify its fixed mode without supporting switching. A
single-phase L2 installation may verify count 1 for L2; count 1 never implies L1
or arbitrary conductor selection. Missing/unverified operation evidence refuses
the command. No phase_to_use, inferred topology or automatic device defaults exist.
The evidence supplier owns verification and current-state applicability; this
injection does not itself discover or verify a device's physical behavior.

A supported point produces one immediate Absolute TxProfile (stack 0, amperes,
one schedule and period) containing current and numberPhases together. Profile
IDs use the target EVSE ID so bindings do not replace each other's profiles.
The peer must support this immediate transaction-scoped subset; other standard
OCPP operations are not implied. APPLIED means accepted control, not measured
power confirmation. The adapter never splits a phase/current transition.

Exactly one active record from the canonical runtime session ledger must belong
to the target EVSE, including its connector scopes; other EVSEs/stations are
excluded. Missing or ambiguous transactions return
TEMPORARILY_REJECTED/TRANSACTION_UNAVAILABLE. The ledger retains active transaction
identity across disconnect/restart until an end/superseding event; this assumes
that the retained identity still describes the transaction, with the peer
performing the final transaction match. No second transaction tracker is added.
After the library's outbound lock and immediately before sending, the adapter
rechecks command validity, live generation, transaction identity, the envelope
and phase-operation evidence. Revoked capabilities or changed transactions send
nothing. Already dispatched commands are not cancelled or rolled back.

wallbox-stationary is the first reference/test device: its EVSE 1, 1 A grid,
physical L1 and L1/L2/L3 mappings, atomic phase switching and transaction behavior
are explicit reference fixture data, not general OCPP rules. Wire tests also cover
EVSE 2 and fixed L2 fractional-current devices with real 2.1 message schemas.
Current inventory discovery does not verify min/max/step, physical phase mapping,
phase-switch operation safety, or immediate TxProfile behavior. Advertisements,
registration and protocol version cannot supply these proofs; production bindings
must obtain them explicitly. No universal OCPP 2.1 compatibility is claimed.

OFF/enable-disable remains unsupported and never becomes a 0 A profile. OCPP
1.6J/2.0.1 control, charging strategies, Energy Manager,
ownership/leases, retries and failure counters remain unimplemented.

## Implemented manual intent runtime and HA entities

`control/runtime.py` owns one `ManualIntent` per EVSE: existing PowerRequest,
phase-retention percentage (default 5), generation, SolverResult and CommandResult.
`ControlInputs` only groups existing CapabilitySnapshot, VoltageObservation,
CurrentLimit, eligible modes and optional current physical mode; it is not a
capability registry. Providers are read on demand. The generic runtime imports
neither HA nor OCPP and uses the existing solve/apply_operating_point boundaries.

`switch.py`, `number.py` and `select.py` expose charging permission, desired watts
(0–100,000 W / 100 W UI step), DOWN/NEAREST/UP approximation and retention tolerance
(0–25% / 1%, default 5). Direction values remain stable while en/de labels are
localized. Control identity includes config entry, station, EVSE and entity key.
Known numeric OCPP 2.1 EVSEs get entities independently of verified capabilities.
Registry identity restores offline entities; temporary disconnect, missing evidence
or voltage expiry never removes them. Desired values remain editable and separate
from execution/solver/command status attributes. No raw current or phase selector
is added. The number's UI maximum is not a capability.

The entry creates a generic ControlRuntime via `v21/control_runtime.py`. This
protocol-layer binder selects only a live v21 station adapter and explicitly binds
target EVSE, current capability provider and verified phase-operation evidence.
Eligible modes require VERIFIED envelopes and VERIFIED operation proof. Voltage
normalization uses exact EVSE-scoped, positive L-N phase readings with timestamps
and deadlines, never nominal values, station-wide guesses or nonzero-current
phase inference. Applicable CurrentLimits remain separate from capability data.
The canonical session ledger must contain exactly one active transaction under
the target EVSE; the wire adapter rechecks identity after its outbound lock.

Every explicit edit increments an intent generation before any await. Resolving
uses live capabilities, limits, voltages and known physical mode. The validity
closure rechecks generation, connection/boot, fresh inputs and the resolved point,
including after the OCPP queue wait. New requests may enqueue concurrently on the
existing library serialization lock so older queued targets can be fenced. Late
success cannot overwrite newer intent or claim current confirmation after inputs
changed; no rollback is invented. There is no retry, auto-resume or command on
telemetry. A telemetry/freshness timer only refreshes readiness attributes. An
APPLIED result means protocol/device acceptance, never measured power.

RestoreEntity restores desired fields only; loading enabled=True does not execute.
Live user edits win over later restoration of the same field. Initial intent is
allowed=False / 0 W. Permission off retains requested watts. If stop evidence is
unverified the solver exposes STOP_UNVERIFIED; if it resolves OFF, v21 still
returns UNSUPPORTED with no outbound request. Desired switch state never claims
that the physical wallbox stopped. A new explicit edit is required after blocked
execution or reconnection. Unload invalidates pending work before transport teardown.

### Explicit reference capability source

`control/reference_wallbox_stationary.py` is an isolated operator-attested source,
selected only through an explicit options-flow acknowledgement and complete
station ID, exact firmware, verified minimum/per-mode maximum currents and separate
configured per-mode limits. The live identity must report vendor and model
`wallbox-stationary` and the exact configured firmware over OCPP 2.1. These checks
are not authentication or automated verification. They scope previously established
manual device evidence to the live connection/boot. This source explicitly covers
EVSE 1, physical L1 and L1/L2/L3, 1 A grid and verified atomic phase switching;
none are defaults for generic adapters or unknown chargers. Stop evidence remains
UNSUPPORTED. Options changes reload without sending control.

Current discovery still cannot verify electrical envelopes, physical mappings,
atomic switching safety or immediate transaction-profile behavior. Without the
explicit source (or another verified provider), execution fails closed while
entities stay present. Physical feedback supplies current mode, not capability
verification. Requested mode and vehicle current never substitute for feedback.

### Physical phase feedback

The reference station samples its existing GPIO 5 phase-switch feedback through
`WallboxDomain.read_physical_phase_mode()`: 0 means L1 and 1 means L1/L2/L3.
It reads the hardware rather than the cached/requested `wb_phase_mode`. Startup
before readiness, an active transition/pulse, an unavailable domain lock, invalid
signal or read failure yields unknown. After a failed transition a readable,
settled physical position may still be reported; it is never replaced by the
failed request. No additional persistent phase state is introduced.

Standard OCPP 2.1 is sufficient: the OCA Device Model defines `PhaseRotation`
as actual wiring relative to the upstream phases, with `x` for a disconnected
phase and an empty value for unknown. See the
[OCA OCPP 2.1 appendices](https://openchargealliance.org/my-oca/ocpp/).
The station sends `NotifyEvent` with `Connector` EVSE 1 / connector 1,
`PhaseRotation`, `HardWiredNotification`, `Periodic`, and Actual values `Rxx`,
`RST` or empty. `SupplyPhases` or a profile's `numberPhases` alone would not prove
physical conductor selection. No vendor variable or DataTransfer is required;
the GPIO-to-conductor interpretation remains reference-device-specific.

The station samples every second after registration (outbound call waits can
lengthen this interval), using fresh UTC timestamps, including after reconnect.
The manager normalizes accepted reference reports into `PhysicalPhaseObservation`
in the existing station snapshot. Evidence expires five seconds after observation.
It requires the current connection token, a timestamp at or after the current
boot/connection epoch, and a non-future timestamp. Older reports cannot overwrite
newer ones; conflicting equal-time reports invalidate the mode. Boot/disconnect
clears observations. Synchronize the hosts' clocks; stale/future data fails closed.
Unknown/expired feedback inhibits reference phase operations and retention.
Generic station capability discovery is unchanged. The explicitly enabled source
still requires matching station identity and exact firmware; the station's optional
`[ocpp] firmware_version` must identify the manually verified installed build.

Existing control entities expose `physical_phase_mode` as phase names or null.
Expiry refreshes diagnostics without issuing commands. All pre-dispatch fences,
including after OCPP queue waits, remain active. Post-dispatch result handling
allows the expected physical-mode/eligibility change during a successful transition
while preserving intent, generation, electrical evidence, limits and voltage fences.
Acceptance itself does not establish physical state. The solver is unchanged.

The automated suites exercise both real OCPP message paths without hardware.
The supervised physical retention/transition test is software-ready, conditional
on verifying the installation's GPIO feedback and reference electrical limits.
GPIO feedback is not an independent measurement of power-contact continuity:
welded contacts and a broken pulled-down signal wire cannot be excluded by this
single input. No hardware validation or universal OCPP compatibility is claimed.

### Phase-retention solver preference

After verification, eligible modes, voltage freshness, device current grid,
CurrentLimits and Direction filtering, select the closest valid current-mode
candidate. For target > 0, retain it when
`abs(offered_power - target_power) * 100 <= target_power * tolerance`.
Otherwise use the existing closest directional candidate/tie-breaking. No division
is needed at target zero; existing OFF/stop behavior remains authoritative. At 0%
only an exact current-mode candidate receives this preference. The standalone
solver's optional argument defaults to 0 for existing callers; manual control
explicitly supplies its 5% default. The solver remains exact and deterministic;
there is no time-based lockout. For example, at 230 V and 1 A grids, current 3P can
retain 4,140 W for a 4,000 W NEAREST target at 5%, even though 1P/3,910 W is closer.
For DOWN the same 4,140 W candidate is forbidden regardless of tolerance.

## Implemented transport/discovery foundation

An entry-owned async CSMS listener accepts `ws://host:port/station-id`, with an
explicit negotiated subprotocol: `ocpp2.1`, `ocpp2.0.1`, then `ocpp1.6` in server
preference order. Missing/unsupported subprotocols are rejected; each version uses
its own library messages and schemas. The pinned `ocpp==2.1.0` library provides a
real v21 module; discovery and verified-device charging operations are supported, not full
feature parity or conformance. No server starts at import time. Configuration
contains only bind IP and port; runtime objects live in `entry.runtime_data`.
The current listener is plain WebSocket for trusted local networks; TLS and station
authentication are not implemented. It does not bind to a particular vendor.

`runtime.Runtime` exposes immutable `core.events.StationSnapshot` updates and
subscriptions; HA diagnostic entities consume snapshots without parsing OCPP objects. Snapshots retain
known identities across disconnect and distinguish disconnected state from live
capability evidence. These are known identities, not a claim that all previously
seen connectors are still present. No physical operating envelopes are fabricated.
Ordinary MeterValues updates immutable scoped measurement observations; 2.x
TransactionEvent measurements feed session accounting without advertising ordinary
HA meter channels;
StatusNotification and 2.x chargingState supply distinct connector/charging enums.
HA operational entities are created only for observed supported channels. Live
meters retain valid known values for the connected runtime generation, without
per-channel expiry. WebSocket ping/pong owns connection liveness; boot/disconnect
clears observations. Time-bounded sample validity still governs session accounting.
Connector-state and charging-state enums are canonical; redundant operational
binary projections and the Session Charging State HA entity are not created.
Sessions expose nine entities while retaining charging state internally. See the
[implemented metering/runtime contract](metering-runtime-state.md) for timestamps,
normalization, freshness, scope, invalidation and limitations. Session ledgers are
implemented separately as described below; authority/authorization logic remains
unimplemented. NotifyEvent stays ACK-only except for the isolated OCPP 2.1
reference physical-phase observation above;
tokenless transaction events receive an empty result, and optional idToken inputs
receive Unknown token status without authorization processing.

The URL station ID maps to `StationId`. OCPP 1.6 connector zero stays station-scoped;
a positive connector N maps explicitly to `EvseId(station, "connector-N")` and
`ConnectorId(evse, "N")`, an adapter representation of the 1.6 controllable outlet,
not evidence of a native EVSE hierarchy. A reported NumberOfConnectors N inventories
1..N; absent counts create no default connector. OCPP 2.x uses explicit reported
EVSE/connector IDs independently, including multiple connectors per EVSE. EVSE-only
inventory remains EVSE-only. Identities from different protocol mappings are not
silently equated when a station changes protocol.

A fresh adapter and captured task/socket owner are created per admitted connection.
A runtime incarnation UUID fences counters across integration restarts. Within that
incarnation, each station's connection generation increases on admission; a valid
BootNotification advances a separate boot-notification epoch and invalidates old
capability/discovery work. Reconnect alone does not advance the boot epoch. OCPP
boot notifications can be retried or triggered, so this conservative invalidation
epoch is not a claim to count physical power cycles. Accepted responses carry UTC
time and a heartbeat interval. Repeated boot messages on a live socket rerun
discovery. New sessions fence old publications and finalizers; unload/shutdown
closes the listener and joins captured tasks with bounded session retirement.

Initial discovery starts after the accepted boot response. A known reconnect also
performs fresh read-only discovery even if no new boot is sent. OCPP 1.6 reads
SupportedFeatureProfiles and NumberOfConnectors via GetConfiguration. OCPP 2.x
requests FullInventory via GetBaseReport, correlates NotifyReport by request ID,
and commits only a complete ordered report (bounded to 10,000 rows). Incomplete,
malformed or timed-out reports do not publish partial support. Runtime snapshots
carry monotonically increasing revisions, source/time and generation metadata.

`charging_schedule` records ADVERTISED support from SmartCharging or an explicit
SmartChargingCtrlr/Available value; it does not verify dynamic current control.
An explicit negative advertisement is UNSUPPORTED for that advertised feature;
missing evidence is UNKNOWN. Timeout/malformed discovery is DEGRADED; an unsupported
discovery operation does not deny the physical charging feature. VERIFIED discovery
means the read-only exchange completed, not that charging commands have been
verified. Physical current min/max/step, phase mapping/switching, stop/pause and
actual current-control behavior remain unknown: `CapabilitySnapshot.envelopes` is
empty and stop evidence is UNKNOWN. Populating a `ChargingEnvelope` requires later
adequate device evidence; neither phase-count schedule fields nor static settings
supply that proof. Discovery sends no availability/configuration/control commands.

Offline stations do not block setup. Disconnect invalidates connection-scoped
evidence and retains known identity metadata in memory; reconnect needs no reload.
Persisted HA listener configuration is untouched. Integration restart creates a
fresh runtime and rediscovery. HA Device Registry retains station identities and
learned metadata; runtime capabilities and control state are not persisted. This
phase exposes diagnostic entities and manual HA intent entities; EV-acceptance
learning is not implemented.

## Implemented session tracking and persistence

`core.sessions` contains immutable protocol-independent lifecycle events and
charging-session records. `Runtime` fences live events and owns `SessionLedger`;
`SessionStorage` serializes the full ledger through HA Store version 1 before
thin scoped session entities present the current or last completed session.
See [session tracking](session-tracking.md) for the complete contract and limits.

OCPP 2.0.1/2.1 TransactionEvent Started/Updated/Ended and OCPP 1.6
StartTransaction/StopTransaction establish boundaries. Neither zero power,
StatusNotification, temporary disconnect, BootNotification nor HA restart ends a
transaction. Persisted internal UUIDs and external transaction IDs are independent
of runtime generations; the current connection may resume the same transaction
while obsolete live events remain fenced. Completed records are retained in full,
and their entity values remain visible until the next session at that scope.

Energy is a validated register delta, not integrated power. Missing endpoints,
resets and conflicting readings produce unknown energy. Fresh exact-scope normal
meters are preferred. A connector session can consume its parent EVSE's total
power/energy only when it is the sole active session there. Fresh normal registers
can seed start/end snapshots without timestamp equality. TransactionEvent samples
stay internal to sessions. Ambiguous shared metering becomes unknown and never
creates connector meter entities. Completed power is zero. Saved active power does not
become live after restart. Store loads before listener admission, coalesces writes
and flushes on unload/shutdown. `runtime.sessions.history(scope=None)` exposes
immutable completed records for a future UI; no per-history HA entities exist.
Manual HA charging intent is implemented separately below. Authorization services
and EV learning remain unimplemented.

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

`PhaseMode` represents any nonempty subset of L1/L2/L3: L1, L2, L3,
L1+L2, L1+L3, L2+L3 or L1+L2+L3. Each mapping can have its own verified
`ChargingEnvelope`, minimum, maximum and current step. The models, voltage lookup,
limit intersection, candidate generation and tie-breaking use the actual phase
mapping/count; the 1P/3P examples do not restrict support to those counts.

`ChargingEnvelope` describes what the **wallbox can safely offer**, not what a
connected EV is guaranteed to consume. A wallbox may support 32 A in both 1P and
3P while a particular EV accepts 32 A in 1P but only 16 A in 3P. That EV behavior
must not reduce the discovered wallbox capability. Similarly, a supported 2P mode
does not guarantee that the EV uses both phases or accepts the maximum current.

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

### Standalone profiles and controller responsibilities

Wallbox Manager deliberately provides useful standalone, current-day PV charging.
It does not require the future Energy Manager. All PV profiles aim for approximately
zero grid import attributable to vehicle charging; they do not have an intentional
grid charging allowance. Numerical tuning remains implementation work.

| Profile | Intent and independent parameters |
| --- | --- |
| OFF | Disallow charging; use a verified stop/pause operation, not assumed zero-amp support. |
| PV_SURPLUS | Charge primarily from current PV surplus while preserving the house battery near a configurable high `battery_target_soc`; around 97% is a conceptual/default candidate, not a fixed requirement. Use SOC hysteresis around the target. |
| PV_OPTIMUM | Use current PV and permitted battery energy with separate `minimum_battery_soc` and `evening_battery_soc`, a remaining-current-day PV forecast and a configurable forecast/safety reserve. Apply a simple linear current-day strategy, not an intentional grid contribution. |
| PV_MAXIMUM | Maximize useful vehicle charging from current PV plus permitted battery discharge, respecting its own independent `minimum_battery_soc`; hold that lower limit when reached. No intentional grid import. |
| GRID | Follow an explicitly configured grid charging budget, with DOWN to respect it. Configure that budget before activation. |

`PV_OPTIMUM.minimum_battery_soc` and `PV_MAXIMUM.minimum_battery_soc` are independent:
30% and 10%, respectively, are an example of a valid installation preference.
Neither is an alias for the external battery reserve. PV_OPTIMUM's evening target
is a separate objective, not a second name for its daytime minimum.

The profile controller decides how much charging power is currently allowed from
energy-system inputs and profile policy. It produces `PowerRequest`; it does not
choose amps, phase count or vendor commands. PV requests normally use DOWN so a
minimum charging step cannot justify deliberate grid charging. Pause if available
power cannot sustain the minimum feasible point. The solver handles technical
feasibility, rounding, phase transitions and electrical limits; it does not interpret
battery forecasts or allocate household energy. REMOTE targets pass through the same
technical solver but do not run a standalone PV profile in parallel.

### Configured energy-system inputs

Consume vendor-neutral Home Assistant entity values through the HA integration
boundary and pass validated snapshots to the controller. No Fronius entity IDs,
register addresses or vendor-specific sign conventions belong in the core.

| Configurable HA input | Unit and semantics | Required use |
| --- | --- | --- |
| `grid_import_power` | W, finite and non-negative, power entering the installation from the grid. | All standalone PV profiles, for grid feedback and tolerance checks. |
| `grid_export_power` | W, finite and non-negative, power leaving the installation toward the grid. | All standalone PV profiles, for currently exported surplus. |
| `battery_charge_power` | W, finite and non-negative, power flowing into the house battery. | All standalone PV profiles, to distinguish battery charging from freely available export. |
| `battery_discharge_power` | W, finite and non-negative, power flowing out of the house battery. | All standalone PV profiles, for battery-flow accounting and discharge-unavailable fallback. |
| `battery_soc` | %, finite in [0, 100], observed house-battery state of charge. | All three battery-aware PV profiles. |
| `battery_reserve_soc` | %, finite in [0, 100], known externally imposed reserve; observed/configured input, not owned by Wallbox Manager. | All battery-aware PV profiles; combines with the profile floor/target. |
| `pv_forecast_remaining` | kWh, finite and non-negative, the total PV energy expected to be generated from now until the end of today's PV production period. It is not surplus after household consumption, vehicle-available energy or a full-day forecast including energy already generated. | PV_OPTIMUM only. |

The battery-aware profiles described here require those battery inputs; absence does
not imply a zero reserve or unrestricted battery energy. A battery-free variant is
not specified by this design. OFF and GRID do not depend on PV-only inputs, and
REMOTE does not require the standalone forecast; all still require their own
technical control prerequisites.

An installation whose source provides signed bidirectional power may use HA
template/helper sensors to split it into separate positive import/export or
charge/discharge entities. Wallbox Manager does not infer the source's sign
convention. Unit validation/conversion must be explicit; do not silently read kW
as W or a W forecast as kWh. Entity selection must identify the whole-installation
grid measurement boundary and the house battery, not an unrelated meter.

Each snapshot carries source identity, observation/update time and validity.
Power/SOC inputs need freshness limits appropriate to their update cadence and a
bounded observation skew for flow comparisons. A constant value is not inherently
stale if the source continues reporting it. The forecast has separate update-age
and current-day validity rules; yesterday's forecast must not survive midnight as
today's remaining energy. A configured reserve may update infrequently, so its
validity policy must distinguish a trustworthy stable setting from an unavailable
source. Exact age limits and skew windows remain configurable/design tuning,
not fixed numbers in this architecture.

Missing, unknown, unavailable, nonfinite, negative or out-of-range required inputs
make the dependent PV profile INHIBITED with a specific reason. Request a verified
safe pause when still authorized; never continue an old target, substitute zero,
choose GRID/PV_MAXIMUM or bypass LOCAL. Fresh valid inputs can automatically resume
the persisted desired profile after reconciliation. Contradictory or asynchronous
flow readings must not trigger an increase; refresh/cohere the snapshot first.
Missing forecast inhibits PV_OPTIMUM without disabling otherwise valid PV_SURPLUS
or PV_MAXIMUM. Input/configuration validity is separate from wallbox capabilities;
both must be revalidated before dispatch, including after restart/reconnect.

### Battery targets and simple PV_OPTIMUM forecast use

PV_SURPLUS gives priority to keeping the battery approximately at its configured
high SOC target. Below the target's hysteresis band, preserve energy for battery
replenishment and reduce/pause vehicle charging as needed. Near/above the target,
use current surplus while avoiding sustained battery depletion. Hysteresis prevents
rapid on/off changes around the target; it does not permit ignoring a higher known
reserve. The controller adjusts only vehicle demand, not battery charging settings.

PV_OPTIMUM may use battery energy during the day down toward its configured minimum,
subject to actual battery/inverter restrictions and the known reserve. Separately,
it attempts to leave the battery at `evening_battery_soc` when PV production ends.
Validate the configured evening objective against the daytime minimum; a forecast
shortfall can make the evening objective unreachable and must be reported rather
than met through unrequested grid charging.

PV_OPTIMUM uses the following additional configuration and normalized time input:

| Input/parameter | Unit and validation | Source |
| --- | --- | --- |
| `average_consumption_power` | W, finite and non-negative; assumed average household/site consumption for the remaining time until sunset. | User-configured constant, not a learned load profile. Household demand is accounted for separately from total PV generation. |
| `battery_capacity` | kWh, finite and strictly positive; explicitly configured usable/nominal battery energy capacity consistent with the SOC conversion basis. | Validated configuration; never inferred from current SOC or power measurements. Exact config-flow presentation remains implementation work. |
| `forecast_reserve` | kWh, finite and non-negative; conservative deduction from expected PV generation. | Existing configurable forecast/safety reserve. |
| `hours_until_sunset` | Hours, finite and non-negative, remaining duration until today's sunset; zero after that sunset. | Normalized by the HA integration layer using HA's existing sun/location facilities, installation location and timezone. |

The HA layer obtains today's local-date sunset; Wallbox Manager requires no separate
latitude, longitude or sunset-sensor configuration. It supplies only the normalized
duration to the pure controller, which imports no HA sun APIs. Do not blindly use
a rolling “next sunset” value after today's sunset: it may refer to tomorrow and
incorrectly create approximately 24 hours of remaining time. If today's solar timing
cannot be established, inhibit the dependent calculation with a reason rather than
substitute tomorrow or fabricate a duration.

The finalized simple calculation is:

```text
remaining_consumption_kwh =
    average_consumption_power_w / 1000 * hours_until_sunset

usable_forecast_kwh =
    max(0, pv_forecast_remaining_kwh - forecast_reserve_kwh)

battery_energy_needed_kwh =
    max(0, remaining_consumption_kwh - usable_forecast_kwh)

additional_soc_needed_pct =
    battery_energy_needed_kwh / battery_capacity_kwh * 100

target_battery_soc = evening_battery_soc + additional_soc_needed_pct

target_battery_soc = clamp(target_battery_soc, minimum_battery_soc, 100)
```

Here `clamp(value, lower, upper) = min(upper, max(lower, value))`; SOC quantities
are percentage points on the 0–100 scale. Validate both configured SOC thresholds
in that range and their minimum/evening relationship before calculating. Invalid
capacity, consumption, reserve or timing inhibits PV_OPTIMUM under the same input
validity rules as its required sensors.

Expected household consumption is approximated linearly from average power and
remaining time. Usable total PV generation first covers that consumption. Any
predicted shortfall adds the corresponding battery SOC to preserve above the desired
end-of-PV evening SOC. Surplus forecast cannot lower the target below the evening
objective through a negative shortfall. Clamp the result to the configured minimum
and 100%; if the unclamped requirement exceeds 100%, expose the planning shortfall
rather than imply the evening goal is guaranteed. This equation adds no learned
behavior, price optimization or sophisticated forecast model.

For example, 500 W over four hours gives 2 kWh expected consumption. A 1.5 kWh
remaining forecast less a 0.5 kWh reserve leaves 1 kWh usable forecast. The 1 kWh
shortfall requires 10 SOC percentage points with a 10 kWh battery. An evening target
of 60% and minimum of 30% therefore produce a planning target of 70%.

`target_battery_soc` is a dynamic planning target, separate from the hard currently
known `effective_discharge_floor` defined below. Compare current battery SOC to the
planning target to preserve the planned battery energy when allocating vehicle
power, while always respecting the effective floor. A higher known external reserve
can constrain allocation even when the calculated planning target is lower. Neither
a favorable forecast nor the planning target authorizes grid charging or overrides
actual inverter limits; instantaneous grid/battery feedback still constrains power.

Recalculate as current time/`hours_until_sunset`, remaining forecast, battery SOC,
known reserve or configuration changes. SOC and reserve affect allocation and
constraints even though they do not appear in the forecast arithmetic itself.
Persist selected profile and configuration, never a calculated planning target as
authoritative restart state. Recalculate from fresh inputs after recovery.

After today's sunset, set `hours_until_sunset = 0` and treat the current-day forecast
contribution as exhausted (zero for this calculation), even if a sensor retains a
residual value. This intentional end-of-day normalization is not a generic fallback
for missing/invalid daytime forecasts. Do not substitute tomorrow's sunset or consume
a tomorrow forecast during this evening calculation. Expected remaining consumption
and additional SOC needed then become zero, leaving the clamped evening target.
Normal current-day input selection resumes for the new local date; no next-day
optimization is introduced. Charging after sunset still respects that planning
target, the profile minimum, known reserve, approximately zero grid import and
observed energy flows; the equation is not permission for unrestricted discharge.

PV_MAXIMUM does not need that forecast or an evening target. It seeks the greatest
useful vehicle power available from PV and permitted battery discharge, constrained
by observed flows and physical limits. At its effective minimum, remove the intended
battery contribution and hold the floor while following available PV. House loads
or inverter behavior may still move SOC; Wallbox Manager can reduce vehicle demand
but cannot guarantee the SOC of a battery it does not control.

For PV_OPTIMUM and PV_MAXIMUM, the effective discharge floor is
`max(profile.minimum_battery_soc, battery_reserve_soc)`. If the known reserve is
higher, report that the requested profile minimum is currently unreachable and
respect the higher limit. PV_SURPLUS likewise respects the external reserve as
well as its high-SOC preservation target. Observe reserve changes and re-evaluate;
never write the reserve entity or assume ownership of it.

### Grid feedback and battery-discharge-unavailable fallback

Account for existing measured vehicle consumption in feedback: installation export
is incremental surplus after current loads, not the total allowable vehicle power.
Do not add battery discharge or vehicle demand twice when using grid readings.
Battery charging power is not automatically free surplus under every profile.
Use time-aligned wallbox consumption and grid/battery observations; exact controller
gains and ramp scheduling remain undecided.

Grid-import tolerance accommodates discrete current steps, measurement/controller
latency and normal fluctuations around the approximately zero-import objective.
For the current reference wallbox, approximately 250 W (roughly a 1 A step on one
phase) is an acceptable example. It is not an intentional grid charging budget:
never add tolerance to the PV target or deliberately consume that extra power.
Make it configurable/derivable from current step, active phase mode, voltage and
installation behavior; do not hard-code 250 W for every device. Whole-site import
may come from house loads, so the controller reduces the controllable vehicle load
without claiming it can eliminate all household import.

The normal battery-limit algorithm uses SOC and the known reserve above. Separately,
apply a generic **battery-discharge-unavailable fallback** when all these conditions
persist across coherent observations or a short debounce window:

- a PV profile currently permits/expects battery discharge;
- the vehicle is actually charging;
- grid import exceeds the allowed control tolerance;
- battery discharge is below a configurable small power deadband.

Do not compare discharge to exactly 0 W. Deadband, persistence window and clearing
hysteresis are tuning parameters; a single transient sample is insufficient. Invalid
or stale readings invoke input inhibition, not a battery-limit diagnosis. When the
condition holds, regard battery contribution as currently unavailable and reduce
vehicle demand toward currently available PV-only surplus, pausing below the
minimum feasible charging point. Clear/reassess conservatively using fresh evidence;
do not repeatedly ramp up into the same grid-import condition.

Grid import above tolerance while the battery is still materially discharging is
different: reduce vehicle demand through ordinary grid feedback as needed, but do
not label this as unavailable discharge or infer a reserve. The fallback never
estimates a hidden SOC percentage; inverter power limits, temperature or other
restrictions can produce similar observed behavior.

The motivating Fronius installation example supplied for this design has an internal
web-interface reserve that can exceed the externally visible Modbus reserve, with
the higher restriction taking effect. This explains the need for flow-based fallback;
it is not a Fronius reserve detector or a vendor-specific control path. Other systems
may expose all relevant restrictions and never require this fallback.

### Boundary to battery integrations and the future Energy Manager

Wallbox Manager reads configured HA entities and controls the wallbox. It does not
write Fronius Modbus registers or embed Fronius PV Manager logic. If active battery
control is later needed, use a defined programmatic interface of the responsible
battery/inverter integration. That API is neither designed nor implemented here.

Standalone Wallbox Manager owns simple current-day PV policy, configured sensor
inputs, simple linear forecast use, current-flow feedback and technical wallbox
operating-point solving. The future Energy Manager owns learned behavior, vehicle
target SOC/departure requirements, expected vehicle/next-day use, electricity-price
optimization, advanced weather/forecast modeling, long-term and cross-device/site
optimization, and more sophisticated battery strategies. None of those prediction
features belongs in standalone PV_OPTIMUM. REMOTE remains the programmatic boundary
for supplying current charging intent/target power; wallbox capabilities, ownership
and technical constraints remain enforced locally.

### Operating-point solving

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
above target; NEAREST minimizes absolute error. A configured phase-retention
preference may retain a less-close direction-valid current-mode point as described
above. Otherwise ties favor the current mode, then lower power. Hard safety limits
always win. If the directional set is
empty, return `unreachable` with bounds and a suggested feasible point, never
silently claim that opposite rounding succeeded. A controller may request a new
explicitly relaxed target; a hard budget cannot be relaxed. Below minimum, DOWN
may select OFF, UP may select minimum if allowed, and NEAREST compares both.

Example at 230 V, 1P 6–32 A, 3P 6–16 A, 1 A steps, both modes currently eligible:
without an applicable retention preference, a 4,000 W request yields
1P/17 A/3,910 W with DOWN or NEAREST and
1P/18 A/4,140 W with UP (tie against 3P/6 A favors current 1P mode).
At 500 W, DOWN and NEAREST choose OFF and UP chooses 1P/6 A/1,380 W.
Above 11,040 W, UP is unreachable in this envelope.

Implementation details for the initial pure solver: current grids are anchored at
`min_current_a`; the maximum is a ceiling and need not lie on the grid. Additional
installation/shared-station current intervals intersect that grid without moving
its origin. The caller supplies fresh remaining shared budgets. Exact standard-library
rational values and integer indices derive endpoints and target-neighbor candidates,
equivalent to enumeration without allocating every current step. Unit suffixes are
part of the contracts. Numeric inputs use their decimal spelling; booleans and
nonfinite values are rejected.

The initial voltage contract accepts measured RMS phase-to-neutral samples only,
with explicit phase mapping, source, observation time and validity deadline. The
caller supplies the comparison time; missing, expired or future samples inhibit
selection for an eligible verified mode. Observation scope and connection generation
must match the capability snapshot; adapters normalize scope only after validating
applicability. Nominal fallback and line-to-line conversion are not implemented.
Offered points retain the complete voltage basis and assume balanced current and
unity power factor; they are estimates, not measured consumption.

Only VERIFIED envelope/stop evidence enables the corresponding solver candidates.
Configuration override records retain intent and reason separately and do not grant
physical support. OFF has no amp setpoint. Prohibited charging yields OFF when stop
is verified, otherwise an explicit unreachable/stop-unverified result. No charging
point is suggested for prohibited charging. Zero targets also select supported OFF
without needing voltage. After current-mode and lower-power tie preferences, fewer
phases, canonical phase mapping and then current give a deterministic final order.
The result contract includes diagnostic bounds/suggestions for directional failures
and a deferred retry-deadline boundary; this solver does not plan phase transitions.

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

### Future session-local EV acceptance

Wallbox Manager will support simple controller-side, session-local learning of
actual AC current acceptance, conceptually named `EVAcceptanceEstimate`. This is
neither a physical wallbox capability nor a permanent vehicle capability. The
learning controller is future work; no learning runtime is implemented here.

Requested power, offered power/current, acknowledged command and measured EV
consumption remain separate. The pure solver determines the offered operating
point; actual consumption is observed later through metering. An acknowledged
command does not establish actual consumption. Offering 20 A while observing
16 A, or offering 3 x 25 A while observing 3 x 16 A, is not by itself a wallbox
capability failure, a solver failure or evidence that the offered wallbox current
is unsupported. Never lower the persistent `ChargingEnvelope` on that basis.

At the start of each new charging session, every supported phase mode has unknown
EV acceptance and no additional EV constraint beyond its currently verified
wallbox/installation envelope. The controller may initially offer up to that
permitted envelope; this is an initial assumption, not evidence of EV acceptance
or permission to bypass the requested power, ownership or other safety limits.
Conceptually, for each mode:

```text
effective_max_current(mode) = min(
    wallbox_or_installation_max(mode),
    observed_session_ev_acceptance(mode) if known
)
```

An unknown acceptance estimate contributes no extra bound. For example, permitted
installation envelopes might be 1P 6–20 A, 2P 6–20 A and 3P 6–27 A. These are
examples, not defaults. The 20 A ceiling may already reflect an installation or
regulatory unbalance limit even when the wallbox hardware permits more. Learning
and upward probes must always respect those limits and the remaining shared
station budget.

Learn independently for each explicit `PhaseMode`, not merely its phase count.
For example, L1 may have an observed 16 A estimate, L1+L2 may remain unknown, and
L1+L2+L3 may independently have an observed 16 A estimate. Never infer a 2P limit
from 1P or 3P evidence, or transfer a limit between L1, L2 and L3 single-phase
mappings without explicit evidence for the destination mode.

The future controller compares reliably offered current/power with measured
current/power. If a higher current has been offered in a stable command state for
a sufficient period while consumption remains materially lower, it may lower the
estimate for that mode. For example, 1P/20 A offered with approximately 16 A
persistently measured may yield a temporary estimate around 16 A for that mapping.
A single low meter sample must never establish an EV limit.

An estimate is not permanent even within a session: battery temperature,
conditioning, BMS balancing, vehicle-side charging strategy or SOC-dependent
tapering may cause temporary underconsumption. The future controller may
periodically and conservatively probe a slightly higher offered current, within
all current authorization, power-request and electrical limits. If the EV accepts
the higher current, the estimate may rise. Both downward and upward adaptation
are supported by the design; this does not authorize repeated uncontrolled
increases merely because consumption is low.

Learning and probing require persistence/debounce before lowering an estimate,
measurement tolerance, stable wallbox command state, coherent/fresh metering,
bounded upward probe steps, and hysteresis/cooldown after probes. Avoid rapid
phase/current oscillation and respect phase-transition restrictions. Exact timing,
deadbands, probe intervals and other tuning parameters remain to be implemented
and tested with the future controller.

The controller may feed an estimate into the solver as an explicit temporary
constraint. If a requested power cannot be reached efficiently with 1P because
session acceptance appears limited to 16 A, the solver can compare alternative
2P or 3P offers, provided those modes are supported, eligible and safe. Unknown
acceptance in an alternative mode still does not guarantee actual EV consumption.

Discard all estimates when the charging/vehicle session ends or the vehicle
disconnects; a new session starts unconstrained by earlier EV observations.
Invalidate stale evidence or treat its estimate as unknown. A phase-mode change
never transfers an estimate to the new mode: use only fresh evidence specific to
that mapping, otherwise treat its acceptance as unknown. Never persist learned
EV acceptance as wallbox capability. Cross-session vehicle learning, fingerprinting
and identification are outside this implementation.

The existing `CurrentLimit(mode, min_current_a, max_current_a, source)` contract
is sufficient without structural changes. It carries an explicit additional
interval for one physical phase mapping and intersects the wallbox grid without
changing capability evidence. Use a distinguishable source such as
`temporary_session_acceptance`, separate from installation/site and shared-station
sources; retain the evidence and reason for that constraint in the controller's
session estimate. The controller owns provenance, freshness, session lifetime and
removal. The solver need not interpret the source or know why the limit exists.

The solver remains deterministic and stateless: `PowerRequest`, wallbox physical
capabilities, explicit current/site/session constraints, voltage observations and
eligible phase modes produce an offered `OperatingPoint` or explicit non-success.
It does not inspect historical metering, learn EV behavior, probe the EV, maintain
timers or decide when to retry higher current. Existing tests already establish
that an explicit per-mode session limit leaves other modes and the underlying
`ChargingEnvelope` unchanged; timing/probing tests belong to the future controller.

## Ownership state machine

A technical interruption must not erase an existing user control decision.
Explicit local user takeover always takes priority over automatic recovery.
Separate the following state dimensions rather than using one owner field:

| Dimension | Meaning and lifetime |
| --- | --- |
| Persistent desired control | `desired_owner = LOCAL | WALLBOX_MANAGER | REMOTE`, selected normal profile (`OFF`, `PV_SURPLUS`, `PV_OPTIMUM`, `PV_MAXIMUM`, `GRID`), and the registered authorized Energy Manager owner when REMOTE is desired. Survives technical interruptions. |
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
still requires a fresh explicit user action: either selecting a normal Wallbox
Manager profile or choosing “Take control” in Energy Manager through the trusted
user-action boundary below. The latter directly authorizes LOCAL -> REMOTE; no
intermediate normal-profile selection is required. Energy Manager startup/reconnect,
heartbeats, target updates, recovery handshakes, HA restart, OCPP reconnect, wallbox
restart and background retries must never clear that block automatically. If
authority cannot be determined reliably, remain inhibited; do not fabricate a standard OCPP authority signal.

Every mutating request carries an ownership epoch and runtime/session generation.
Under one serialization boundary, validate desired authorization revision, active
ownership, lease, capabilities and connection generation before dispatch and again
before committing a result. Authority-acquisition requests leaving LOCAL use the
separately validated fresh explicit user-action authorization because active software
ownership has not yet been established; this exception permits only the acquisition attempt,
not charging targets or lease creation before confirmation. Local takeover advances
the epoch, invalidates leases and recovery attempts, cancels queued work and wins over late acknowledgments.
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
| LOCAL -> REMOTE | A fresh explicit Energy Manager “Take control” user action, validated by the trusted HA/Wallbox Manager boundary, authorizes an authority-acquisition attempt directly. Keep desired LOCAL and its durable latch while ACQUIRING. Only after remote/OCPP authority is verified, and the attempt is still current, clear the latch and persist desired REMOTE/registered owner authorization, then issue a fresh lease. Require a fresh target before REMOTE/ACTIVE. Rejection, timeout or unverifiable authority leaves LOCAL latched, no usable lease and a visible failure; no delayed/background retry, and another attempt requires another fresh explicit user action. |
| Initial REMOTE acquisition | A trusted fresh explicit Energy Manager takeover is allowed from LOCAL via the row above or from verified manager control. Reject competing owners. Establish/verify device authority before persisting desired REMOTE and allocating a fresh lease; remain ACQUIRING/inhibited until a fresh valid target is solved and applied. No initial takeover can be asserted by an ordinary background API call. |
| REMOTE recovery | Recover only the persisted, still-authorized registered owner through the handshake below, after device reconciliation. Issue a new token/deadline and require a fresh target before REMOTE becomes ACTIVE. No new user click; no heartbeat-only acquisition. |
| REMOTE heartbeat | Refresh only an unexpired matching runtime lease. A heartbeat never acquires authority, performs recovery or revives an expired lease. |
| Active lease timeout | If no technical-recovery episode has been entered, revoke the lease and remote recovery authorization, persist WALLBOX_MANAGER/OFF intent and inhibit. Attempt verified stop only while authority is confirmed; report uncertainty on failure. Never resume an earlier profile. A new takeover needs explicit authorization. This is distinct from invalidating a lease because a technical interruption started recovery. |
| Explicit remote release | Validate lease, revoke remote recovery authorization and lease, persist WALLBOX_MANAGER/OFF intent and inhibit. Do not automatically resume an earlier charging profile. |
| HA restart/reload | Restore desired control and the LOCAL latch, never active ownership or old leases. Reconcile identity, boot/session generation, capabilities, fresh telemetry and authority. LOCAL remains blocked. Otherwise automatically re-establish safe control for the desired normal profile, or await the authorized REMOTE recovery handshake. |
| OCPP disconnect/reconnect | Fence in-flight commands/results, invalidate their connection generation and any runtime lease, clear active ownership and enter RECONCILING/unavailable. Preserve desired profile/ownership and remote recovery authorization; do not infer LOCAL. On reconnect perform fresh reconciliation, then automatically recover the normal profile or use the REMOTE handshake. Verified LOCAL overrides both. |
| Wallbox restart | Invalidate connection-specific control state, leases, commands and capability evidence (retain the charging-session ledger until a transaction end), while preserving desired control authorization. Rediscover and verify actual authority; LOCAL wins. Otherwise automatically resume the desired normal profile or recover REMOTE by handshake. Inspect owned OCPP profiles/settings without assuming they survived or disappeared. |
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
Initial acquisition must be authorized by a trusted Wallbox Manager/Home Assistant
mechanism that distinguishes a genuine fresh explicit user action from ordinary
programmatic Energy Manager calls. Registered-caller authentication alone does not
prove a user clicked “Take control”. A caller-controlled boolean such as
`user_authorized=True`, a supplied context label or an old authorization record is
not sufficient. Ordinary background API calls cannot claim that a user authorized
LOCAL -> REMOTE. The exact HA implementation and how trusted authorization reaches
`async_acquire_remote_control` remain implementation decisions; the conceptual
signature above does not grant that authority merely by accepting `owner_id`.

Bind the trusted user action to this owner, controlled device and acquisition
attempt. It cannot be replayed for a failed, superseded or later attempt. While
leaving LOCAL, retain the durable latch and desired LOCAL until device authority
acquisition is actually confirmed. Request initiation alone must not clear it or
create a usable lease. On rejection, timeout or unverifiable authority, report the
failure, remain LOCAL and schedule no background retry. Late acknowledgments cannot
complete the failed attempt; another attempt requires a fresh user action. A
concurrent new local takeover wins, advances the ownership epoch and fences the
attempt even if acquisition subsequently returns success. After verified success,
persist the latch clear and desired REMOTE authorization together before issuing
the fresh lease; activate REMOTE only after a fresh target is solved and applied.

Recovery instead validates that desired ownership is still REMOTE for exactly this registered owner,
station/EVSE and authorization revision. It cannot create authorization for a new
owner. HA and Energy Manager restarts do not require a new user click when that
persisted authorization is still valid. A deliberate LOCAL takeover after that
authorization revokes it: neither a stale authorization nor the recovery handshake
can leave LOCAL. A new explicit Energy Manager “Take control” action can establish
new authorization through the initial-acquisition path, without first selecting a
normal Wallbox Manager profile. Technical recovery behavior is otherwise unchanged.

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

The initial implemented subset is specified in [Metering & Runtime State](metering-runtime-state.md).
The following richer storage, aggregation and control presentation are future design;
the initial subset does not store raw/historical payloads or compute derived totals.

Keep configured energy-system HA inputs separate from charger protocol metering;
their non-negative directional semantics and freshness rules are defined above.
Do not apply those input conventions to raw OCPP samples, whose signed/export
meaning must still be preserved. Normalize periodic and transactional metering into
samples containing station,
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
Configuration selects the energy-input entities and exposes separate PV_SURPLUS
battery target, PV_OPTIMUM minimum/evening SOC, forecast reserve, configured average
consumption power and battery capacity, and PV_MAXIMUM minimum SOC. Solar timing
comes from HA rather than additional user-configured location or sunset entities. Observe external battery reserve read-only. Present effective limits,
input freshness/errors, forecast shortfall and active fallback reasons separately
from configured desires. Tolerance/deadband/hysteresis settings must not appear as
grid charging budgets. No profile controls or translations are implemented yet;
add their localized strings with the eventual UI.
Dynamic entity bounds come from snapshots and are revalidated on write. Preserve
stable IDs across reconnects and discovered capability changes. UI text and errors
use `strings.json` with matching `translations/en.json` and `translations/de.json`;
keep machine-readable codes untranslated. Never expose lease credentials as entities.

## Delivery and verification plan

Implement pure capability, ownership and solver contracts first, with fake adapters;
then versioned protocol adapters, HA presentation and device-specific extensions.
Test each adapter against the same normalized contract suite. OCPP 2.1 needs its
own schema/library compatibility proof; advertising its subprotocol is insufficient.
Transport/discovery adoption is recorded in the upstream analysis and distributed
MIT notice. No changelog is created before v1.0.0.

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

Future ownership tests must explicitly cover successful LOCAL -> REMOTE takeover
through the trusted user-action path; rejected, timed-out or unverifiable acquisition
leaving LOCAL latched with no usable lease; no delayed/background retry or activation
from a late response after failure; heartbeat and ordinary target requests unable to
leave LOCAL; recovery and stale prior REMOTE authorization unable to leave LOCAL
after a later local takeover; a fresh explicit takeover after LOCAL succeeding;
and concurrent local takeover during acquisition winning and fencing the REMOTE
attempt. Include background startup/reconnect calls and forged caller-controlled
user-authorization claims, and verify that successful technical REMOTE recovery
still needs no new user click when prior persisted authorization remains valid.

Future standalone PV tests must cover independent profile minima and the separate
PV_OPTIMUM evening target; PV_SURPLUS target hysteresis; higher/changing known
reserve; the finalized forecast equation and its W/kWh/SOC conversions, positive
capacity validation, safety-reserve subtraction, zero shortfall and both clamp
bounds; the worked numerical example; today-versus-next sunset, timezone/local-day
rollover, missing solar timing and after-sunset forecast exhaustion; fresh target
recalculation after time/configuration changes and restart; unavailable/negative/nonfinite/wrong-unit/stale/skewed
inputs; no intentional grid budget in PV profiles; no double-counting of current
vehicle/battery flows; step/mode-dependent tolerance; deadband/debounce and fallback
recovery; excess import with versus without material battery discharge; floor
holding and pause below minimum; and fresh re-evaluation after technical recovery.
Test that no battery reserve writes or inferred hidden reserve percentage occur.
These are future controller acceptance cases, not tests implemented in this task.

Current development gates are `ruff check`, `ruff format --check` and `pytest`
(using `.venv/bin/` locally). Pure-core and solver tests now run alongside the
unchanged translation key and placeholder checks, with Python 3.14 CI running
the same quality gates. Future
runtime changes must include their own tests. These checks do not establish protocol
conformance or hardware safety; device behavior requires simulator and hardware
verification before enabling control.

## Read-only HA diagnostics (beta.1)

The config entry forwards `binary_sensor` and `sensor` platforms even with zero
stations. Both subscribe to the generic Runtime, replay current snapshots and
recreate known station entities from HA Device Registry. New stations are added
through push events without reload or polling. Entity subscriptions are removed
by `async_on_remove`; platform discovery subscriptions use entry unload callbacks.

A station device identifier is `(wallbox_manager, <entry-id>:<station-id>)`.
Entity unique IDs append a stable diagnostic key to that string. The entry
namespace separates identical station IDs on distinct listeners; neither identity
contains generations, runtime incarnation, vendor nor model. A station is the HA
device boundary; EVSE/connector distinctions remain in the generic runtime.

Connected is a diagnostic connectivity binary sensor. Diagnostic sensors expose
negotiated protocol version, connection generation, boot generation, capability
revision and actual discovery evidence state. Discovery attributes include source,
reason and timestamp; all entities carry station ID and runtime incarnation.
There is no invented running/completed lifecycle or electrical measurement class.

Disconnect sets Connected false while keeping metadata and last-known protocol
and generations visible. Discovery reflects runtime invalidation to UNKNOWN with
the disconnected reason, rather than presenting old evidence as current. On
reload/restart the durable registry recreates the entity set, Connected is false,
and other sensors are unavailable until a fresh runtime snapshot exists. No old
connection counters or capabilities are restored. Missing BootNotification fields
never overwrite learned registry metadata with fabricated defaults.
