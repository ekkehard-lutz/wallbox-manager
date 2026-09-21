# Wallbox Manager

Wallbox Manager is a Home Assistant custom integration for managing EV charging
stations through a common, capability-based interface.

The integration is intended to work as a standalone wallbox manager while also
providing a programmatic interface for a future higher-level Energy Manager.

## Architecture

See the [proposed architecture](docs/architecture.md) and the
[pinned upstream OCPP adoption analysis](docs/upstream-ocpp-analysis.md) for module
boundaries, ownership transitions, power solving and reuse decisions. These are
design documents; OCPP runtime functionality has not been implemented yet.

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

## Charging profiles and control ownership

Normal user-selectable Wallbox Manager profiles are:

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

## Energy Manager interface

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

This project is under active development. No stable release is available yet.

Immutable station/EVSE/connector identities, capability evidence and independent
phase envelopes, voltage observations, power requests and solver results are
implemented. The pure solver respects current steps and supplied electrical limits,
uses actual per-phase voltages, and returns an offered operating point, logical OFF
or an explicit unreachable reason. A deferred-result contract is reserved for the
future phase-transition planner. It does not command a charger or claim measured
EV consumption. OCPP transport/discovery, ownership, profiles and HA entities remain
future work.

Run development checks with `.venv/bin/ruff check .`,
`.venv/bin/ruff format --check .` and `.venv/bin/pytest`. Core tests require no running
Home Assistant instance; Python 3.14 CI runs these same checks.

The first public release is planned as v1.0.0. A changelog will be introduced
with that release.

## Upstream code and attribution

The project may reuse or adapt MIT-licensed code from other open-source
projects, including OCPP implementations.

Any code that is copied or substantially adapted will retain the required
copyright and license notices and will be documented appropriately.

No upstream code has been incorporated at this stage.

## License

Wallbox Manager is licensed under the MIT License. See `LICENSE`.
