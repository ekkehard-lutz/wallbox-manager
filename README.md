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
- MAXIMUM
- GRID

Two additional states represent control ownership and cannot be selected as
normal Wallbox Manager profiles:

- LOCAL: control was taken locally at the wallbox.
- REMOTE: control was explicitly granted to an external Energy Manager.

Selecting a normal Wallbox Manager profile is an explicit user action and may
therefore acquire remote/OCPP authority from the wallbox.

If the wallbox is switched to local control, Wallbox Manager must not
automatically reacquire remote authority.

REMOTE control uses an owner-specific lease and heartbeat. A new explicit user
action is required to acquire REMOTE control after ownership is lost.

## Energy Manager interface

A future Energy Manager communicates with Wallbox Manager through a programmatic
API rather than by manipulating Home Assistant entities.

The Energy Manager requests charging intent, for example target power and a
rounding direction. Wallbox Manager translates that request into a valid
wallbox operating point according to the wallbox capabilities.

The planned target-power directions are:

- DOWN
- NEAREST
- UP

Home Assistant entities remain available for user interaction, display and
automations.

## Development status

This project is under active development. No stable release is available yet.

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
