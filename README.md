# Wallbox Manager

Wallbox Manager is a Home Assistant custom integration for managing EV charging
stations through a common, capability-based interface.

The integration is intended to work as a standalone wallbox manager while also
providing a programmatic interface for a future higher-level Energy Manager.

![Wallbox Manager overview](docs/images/wallbox-manager-overview.png)

## Read-only integration

Version 0.1.0 is the first stable release of the read-only integration. It provides
an OCPP listener for 1.6J / 2.0.1 / 2.1, BootNotification, read-only discovery,
dynamic station devices, diagnostics, reported metering/runtime state and persistent
charging sessions.

Add this repository as a HACS custom repository of type Integration and install
Wallbox Manager once v0.1.0 is published. For manual installation, copy
`custom_components/wallbox_manager` into your HA configuration's
`custom_components` directory. Restart HA, then add Wallbox Manager under Settings
→ Devices & services and configure the bind IP and port. Configure the wallbox URL
as `ws://<HA-host>:<configured-port>/<station-id>`. Version 0.1.0 uses plain
WebSocket on a trusted local network.

Each station appears as a device with learned manufacturer, model and firmware.
Diagnostics include Connected, OCPP protocol version, Connection generation,
Boot generation, Discovery evidence and Discovery revision. Station ID and runtime
incarnation are attributes. Stations may connect after setup; reconnect updates
the same entities. On disconnect Connected becomes false, metadata and last-known
protocol/counters remain, and discovery evidence is invalidated. After integration
reload known devices remain: Connected is false and other diagnostics are
unavailable until a new connection supplies a snapshot.

Reported voltage/current/power per phase, explicit total import power and imported
energy now create scoped HA sensors dynamically. Connector state and Charging
state enums are the canonical operational state entities, preserving connected,
charging and paused/suspended states without redundant boolean projections.
Known meter values, including unchanged zero values, remain available while the
connection is live. Missing/invalid data and boot/disconnect make operational
entities unavailable while retaining IDs. The 120-second sample deadline remains
for conservative session attribution and endpoint accounting, not ordinary HA
meter availability.
See [metering and runtime state](docs/metering-runtime-state.md) for scope, units,
capability-driven entity creation, freshness and deliberately unsupported cases.

Transaction lifecycle events now create exactly nine current-or-last session
entities (without a separate Session Charging State entity) per
observed EVSE/connector. Completed values stay visible until the next session;
energy uses matching meter-register deltas. A sole connector session may use fresh
parent-EVSE total power/energy; ambiguous multi-session attribution stays unknown.
TransactionEvent samples feed sessions without creating duplicate ordinary meter
entities. Existing beta.5 duplicates and beta.6 removed boolean/session-state entities may
remain in HA's registry for manual cleanup; no automatic deletion is performed.
Active sessions and all completed
history survive reload/restart. Disconnect and boot do not end a session. See
[session tracking and persistence](docs/session-tracking.md) for lifecycle,
accounting, restoration, query access and limitations.

OCPP 2.1 transaction-scoped charging OperatingPoint dispatch is implemented for
explicitly bound EVSEs with verified device capabilities. One immediate TxProfile
sets current and phase count together. Current bounds and resolution come from the
EVSE's verified ChargingEnvelope; representable fractional currents are supported.
Physical mappings and any required phase transition need separate verified
operation evidence: numberPhases alone does not select conductors or prove
switching support. Fixed-phase devices can use their verified fixed mode.

wallbox-stationary is the first reference/test device (EVSE 1, 1 A grid, verified
1-/3-phase switching), not a default for other devices. Current discovery does not
verify electrical envelopes or physical phase operations; control requires explicit
capability/evidence providers, including the opt-in reference source below. This does not establish universal OCPP 2.1 charger
compatibility. Physical OFF/stop, OCPP 1.6J/2.0.1 charging control, charging
strategies, Energy Manager and EV learning remain unimplemented.

## Manual charging control (v0.2.x development)

Known OCPP 2.1 EVSEs receive four stable Home Assistant entities:

| Entity | Meaning |
| --- | --- |
| Charging enabled (requested) | User permission (`PowerRequest.allowed`); changing it retains desired power. |
| Desired charging power | Requested watts, 0–100,000 W, 100 W UI step. This range is not a device rating. |
| Power approximation | DOWN / NEAREST / UP: do not exceed / nearest power / do not fall below. Labels are localized. |
| Phase retention deviation | 0–25%, 1% UI step, default 5%; prefer the known current physical mode within this deviation. |

Each explicit edit passes through PowerRequest → verified capabilities, configured
CurrentLimits and fresh measured L-N voltages → solver → OperatingPoint → command
boundary → the EVSE-bound OCPP 2.1 adapter. Phase retention is considered **after**
all hard limits and direction constraints. It never allows DOWN above target or
UP below target. No raw current or physical phase controls are exposed.

Entities remain editable while offline or without capabilities; attributes
`execution_ready`, `execution_blocked_reason`, `control_status`, `solver_reason`,
`command_status` and `command_reason` explain execution. Their primary states are
**desired intent**, not measured output or proof of a stop. Physical stop remains
unimplemented: turning permission off retains the requested watts and reports
`stop_unverified` (or an unsupported adapter result if a solver resolves OFF).
It never sends a zero-ampere substitute or reports a successful physical disable.
A 0 W target alone is not permission off: with stop unsupported, UP/NEAREST may
resolve the smallest positive offer under the existing solver rules.

Only explicit edits dispatch. Telemetry, reconnects and restored HA state never
send commands. Initial intent is disabled with 0 W; HA restores only desired
values. After restart or a blocked/rejected operation, make an explicit edit when
ready. Queued superseded requests are fenced; dispatched requests cannot be
rolled back. APPLIED confirms acceptance, not measured charging power.

### Explicit wallbox-stationary reference setup

The integration's **Configure** options provide an isolated, operator-attested
reference source. Leave it disabled for unknown devices. Before enabling, verify:

- Exact OCPP station ID and reported firmware; reported vendor and model must both
  be `wallbox-stationary`. This is identity matching on the existing trusted local
  network, not authentication or automatic hardware verification.
- EVSE 1, physical L1 and L1/L2/L3 modes, safe atomic phase/current switching,
  immediate TxProfile transaction behavior and 1 A resolution on that firmware.
- The actual verified minimum and each mode's maximum current, entered explicitly.
  Enter installation/user current limits separately; they do not change capability
  evidence. Zero configured maximum can inhibit a mode.

The acknowledgement records manual verification; it must not be used to guess
capabilities. Changing options reloads the integration and never sends a command.
Clearing the acknowledgement removes the source, **without stopping the charger**.
Only the configured station with matching identity/firmware receives this source.
All other known EVSEs retain their entities but cannot execute without a verified
source. Production discovery cannot currently establish these electrical facts.

For the first hardware test, configure the listener and reference source, connect
the verified firmware over OCPP 2.1, establish an active unambiguous EVSE 1
transaction and device-side permission for remote control, and obtain fresh
EVSE-scoped per-phase L-N MeterValues. Set desired watts/direction, then enable
requested charging and inspect both command status and measured charging behavior.
Use the device's own controls to stop: this integration cannot yet stop it.

The reference source now consumes fresh physical feedback from the station's
standard OCPP 2.1 `NotifyEvent` reporting of `Connector.PhaseRotation`: `Rxx`
means L1, `RST` means L1/L2/L3, and an empty value means unknown. This interpretation
is restricted to the verified wallbox-stationary wiring, EVSE 1 / connector 1.
It does not enable generic chargers or verify their capabilities.

Deploy the station feedback implementation and set its `[ocpp] firmware_version`
to the exact manually verified build configured in the manager. Synchronize both
hosts' UTC clocks. Reports expire after five seconds and are cleared on boot or
connection changes; absent/unknown feedback blocks reference phase operations.
Inspect the existing control entities' `physical_phase_mode` attribute. Requested
profiles and measured current never establish this attribute.

The software is ready for a supervised retention/transition hardware test: confirm
physical feedback, request a target within the configured deviation in the current
mode, check that the relay stays put, then request a target for which another
verified mode is appropriate and confirm the new reported position. First verify
that GPIO 5 really follows the installed phase switch (0 = L1, 1 = L1/L2/L3).
This feedback cannot independently detect welded power contacts or a broken
pulled-down signal wire. Hardware behavior has not been tested by the automated
suite. See [physical feedback details](docs/architecture.md#physical-phase-feedback).

## Architecture

See the [proposed architecture](docs/architecture.md) and the
[pinned upstream OCPP adoption analysis](docs/upstream-ocpp-analysis.md) for module
boundaries, ownership transitions, power solving and reuse decisions. These are
design documents; the implemented subset now includes pure solving, a
protocol-independent control command boundary and read-only OCPP
transport/discovery, metering/runtime state and session tracking/persistence.
The command boundary forwards resolved operating points to the OCPP 2.1 adapter
and normalizes its outcomes; acceptance does not confirm measured power.

Wallbox Manager separates charging strategy from wallbox-specific communication.

~~~text
Optional Energy Manager
        |
        | target charging power
        v
Wallbox Manager
        |
        | profiles, control logic and capability model
        v
Protocol adapters
        |
        +-- OCPP 1.6J
        +-- OCPP 2.0.1
        +-- OCPP 2.1
        +-- future protocol adapters
        |
        v
Wallbox
~~~

Standard OCPP functionality is preferred whenever possible. Vendor-specific
functionality may be implemented through isolated OCPP DataTransfer extensions.

## Planned charging profiles and control ownership

The planned user-selectable Wallbox Manager profiles are:

- OFF
- PV_SURPLUS
- PV_OPTIMUM
- PV_MAXIMUM
- GRID

The PV profiles work standalone using configured, vendor-neutral HA sensors:
separate non-negative grid import/export and battery charge/discharge power in W,
battery SOC and observed reserve in %, plus remaining-current-day PV forecast in
kWh for PV_OPTIMUM. Signed vendor readings can be split with HA template/helper
sensors; Wallbox Manager does not write inverter registers.

- PV_SURPLUS preserves a configurable high battery SOC while using current surplus.
- PV_OPTIMUM has separate daytime minimum and evening battery SOC targets, with
  a configured average household consumption in W, battery capacity in kWh and
  forecast/safety reserve in kWh. The forecast means total PV generation remaining
  today, before household consumption. Predicted household energy shortfall until
  sunset is converted to additional SOC above the evening target, clamped between
  minimum SOC and 100%. HA supplies today’s sunset; after sunset the remaining
  duration and forecast contribution are zero, without planning against tomorrow.
- PV_MAXIMUM maximizes PV plus permitted battery contribution using its own minimum
  SOC, independent of PV_OPTIMUM.

Known battery reserves take precedence over lower profile minima. If expected
battery discharge becomes unavailable while grid import persists, flow-based
fallback reduces charging toward PV-only surplus. Small grid-import tolerance
covers control resolution and latency; it is not an intentional charging budget.
Missing/stale required inputs inhibit the dependent profile.

Two additional states represent control ownership and cannot be selected as
normal Wallbox Manager profiles:

- LOCAL: control was taken locally at the wallbox.
- REMOTE: control was explicitly granted to an external Energy Manager.

Selecting a normal Wallbox Manager profile is an explicit user action and may
therefore acquire remote/OCPP authority from the wallbox. A fresh explicit “Take
control” action in Energy Manager can also directly leave LOCAL and acquire REMOTE
through a trusted HA/Wallbox Manager user-action mechanism; selecting a normal
profile first is not required. Keep LOCAL latched until device authority is verified.
Failure leaves LOCAL with no usable lease or background retry. On success, create
a fresh lease and require a fresh target before REMOTE becomes ACTIVE.

If the wallbox is switched to local control, Wallbox Manager must not
automatically reacquire remote authority.

REMOTE control uses an owner-specific runtime lease and heartbeat. Technical
interruptions preserve the desired profile and existing owner authorization. After
reconciliation, normal profiles resume automatically; REMOTE requires an
authenticated recovery handshake, a fresh lease and a fresh target, without another
user click. A deliberate LOCAL takeover blocks automatic recovery and requires
a new explicit user action to leave LOCAL. Ordinary API calls, heartbeats and
recovery handshakes cannot assert that authorization or bypass the LOCAL latch.

## Planned Energy Manager interface

A future Energy Manager communicates with Wallbox Manager through a programmatic
API rather than by manipulating Home Assistant entities.

The Energy Manager requests charging intent, for example target power and a
rounding direction. Wallbox Manager translates that request into a valid
wallbox operating point according to the wallbox capabilities.

The implemented pure solver supports these target-power directions:

- DOWN
- NEAREST
- UP

Standalone profiles handle simple current-day PV logic and energy-flow feedback.
Advanced forecasts, prices, departure/vehicle targets, learned behavior and site-wide
optimization belong to the future Energy Manager, which supplies current power
intent through REMOTE. Wallbox Manager retains technical operating-point solving.

Home Assistant entities remain available for user interaction, display and
automations.

## Development status

Version 0.1.0 establishes the stable read-only scope described above. Development
now includes the first v0.2.x manual HA control path through the OCPP 2.1 adapter.
Charging profiles and the planned Energy Manager interface remain future work.

Immutable station/EVSE/connector identities, capability evidence and independent
phase envelopes, voltage observations, power requests and solver results are
implemented. The pure solver respects current steps and supplied electrical limits,
uses actual per-phase voltages, and returns an offered operating point, logical OFF
or an explicit unreachable reason. A deferred-result contract is reserved for the
future phase-transition planner. It does not command a charger or claim measured
EV consumption. Metering and runtime state are reported separately by adapters.
Ownership and charging profiles remain future work.

Run development checks with `.venv/bin/ruff check .`,
`.venv/bin/ruff format --check .` and `.venv/bin/pytest`. Core tests require no running
Home Assistant instance; Python 3.14 CI runs these same checks.

## Read-only OCPP endpoint

Configure Wallbox Manager with a local bind IP and port (defaults `0.0.0.0:9000`).
Point the wallbox at `ws://<HA-host>:<port>/<station-id>` and explicitly select
OCPP 1.6J, 2.0.1 or 2.1. Multiple stations can connect to one endpoint. Missing or
unsupported subprotocols are rejected. No station ID, vendor or current limit is
hard-coded. The endpoint currently uses plain WebSocket without station
authentication or TLS, for a trusted local network only.

The integration loads without a connected wallbox. It accepts BootNotification,
answers Heartbeat, learns identity/connector inventory, and reruns read-only
discovery after reconnect/boot. Disconnect invalidates old evidence without
requiring an integration reload. StatusNotification supplies scoped operational
state and identity. OCPP 1.6 uses GetConfiguration; 2.x uses correlated, complete
GetBaseReport/NotifyReport inventory. All three versions use their own schemas
from `ocpp==2.1.0`; this is a tested foundation, not a full protocol implementation.

Smart-charging advertisements remain distinct from verified behavior. Physical
current envelopes, physical phase switching and stop support stay unknown; no
nominal current/phase limits are invented. Generic immutable runtime snapshots are
consumed by push-based HA diagnostics and observed meter/state entities. Manual
intent controls are exposed for known OCPP 2.1 EVSEs. Hardware validation with wallbox-stationary has
confirmed capability-driven metering entities, connection-generation/liveness
handling, session tracking and persistence, and session meter attribution. An
actual network black-hole/DROP test confirmed OCPP reconnect, preservation of an
active charging session, and recovery of connector state and meter values after
reconnect. This validation complements the automated fake/local-peer tests; it
does not establish full protocol conformance or compatibility with every wallbox.
Older empty scaffold entries migrate to the default endpoint configuration.

## Upstream code and attribution

The project may reuse or adapt MIT-licensed code from other open-source
projects, including OCPP implementations.

Any code that is copied or substantially adapted will retain the required
copyright and license notices and will be documented appropriately.

Selected transport, lifecycle, boot and discovery code/scenarios are now adapted
from the pinned upstream. See the [adoption record](docs/upstream-ocpp-analysis.md)
and [MIT notices](custom_components/wallbox_manager/THIRD_PARTY_NOTICES.md).

## License

Wallbox Manager is licensed under the MIT License. See `LICENSE`.
