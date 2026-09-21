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

There are no charging controls, profiles, phase switching, vendor extensions,
Energy Manager functionality or EV learning yet.

## Architecture

See the [proposed architecture](docs/architecture.md) and the
[pinned upstream OCPP adoption analysis](docs/upstream-ocpp-analysis.md) for module
boundaries, ownership transitions, power solving and reuse decisions. These are
design documents; the implemented subset now includes pure solving and read-only
OCPP transport/discovery, metering/runtime state and session tracking/persistence.

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

Version 0.1.0 establishes the stable read-only scope described above. Charging
controls and the planned Energy Manager interface remain future work.

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
consumed by push-based HA diagnostics and observed meter/state entities. No
charging controls are exposed. Hardware validation with wallbox-stationary has
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
