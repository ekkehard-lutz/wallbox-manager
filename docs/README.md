# Wallbox Manager documentation

| Topic | Guide |
| --- | --- |
| Installation, HACS and rollback | [Installation and configuration](../README.md#installation-and-configuration) · [HACS installation and rollback](v0.4/release-notes-v0.4.0.md#hacs-installation-and-rollback) |
| Architecture and OCPP 2.1 | [Stationary and OCPP](../README.md#stationary-and-ocpp) · [Responsibilities and private LAN](specifications/architecture-responsibility.md) · [OCPP and session lifecycle](specifications/v0.4-current-requirements.md#ocpp-and-session-lifecycle) |
| Authority and charging actions | [Authority, hardware permission and charge release](specifications/v0.4-current-requirements.md#authority-hardware-permission-and-charge-release) · [Action requirements](specifications/charge-release.md) |
| NETZ, scheduling and Fronius reserve | [NETZ profile](specifications/grid-profile.md) · [Timing and recovery](specifications/operational-display-and-timing.md) · [Persistence and restart](specifications/v0.4-current-requirements.md#persistence-and-restart) |
| PV profiles and regulation | [Implemented PV profiles](specifications/v0.4-current-requirements.md#pv-profiles) · [PV economics](specifications/v0.4-current-requirements.md#pv-economics) · [Operating points](specifications/pv-operating-point-response.md) |
| Home Assistant entities and Lovelace | [Entities and configuration](specifications/v0.4-current-requirements.md#home-assistant-entities-and-configuration) · [Service schemas](../custom_components/wallbox_manager/services.yaml) · [Card registration](frontend-registration.md) · [Diagnostics](diagnostic-logging.md) |
| Development and stable release | [Development, tests and release procedure](development.md) · [Stable validation](v0.4/release-validation-v0.4.0.md) · [Field acceptance](v0.4/field-acceptance.md) |

The [current requirements baseline](specifications/v0.4-current-requirements.md)
summarizes the published implementation. Use the
[specifications index](specifications/README.md) for detailed requirements,
approval status and precedence.
