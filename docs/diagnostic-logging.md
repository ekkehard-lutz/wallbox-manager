# Wallbox Manager diagnostic logging

In **Settings → Devices & services → Wallbox Manager → Configure**, choose
**Diagnostic level** (**Diagnosestufe**). The current `diagnostic_level` option
defaults to 0. Level selection is applied by the existing runtime.

| Level | Current coverage |
| --- | --- |
| 0 | Operational errors remain logged. |
| 1 | OCPP lifecycle and notable errors or blocks, including hardware-disabled NETZ actions. |
| 2 | Contextual NETZ schedule/action/dispatch, OCPP and reserve events, plus the approved PV decision and execution records. |
| 3 | Adds detailed OCPP read events. PV still follows its Level-2 decision and eligible-cycle sample contract. |

Filter local HA logs for `WBMGR` and the `subsystem=ocpp`, `subsystem=netz`
or `subsystem=pv` field. Correlate by entry/station/connector and available
bounded lifecycle context. NETZ records distinguish business timing and
dispatch deferral. PV records distinguish proposals, submitted commands,
acknowledgements, confirmed offers, observed phase and measured response.
Unchanged eligible active PV cycles may produce the compact `pv_sample`.
Ordinary passive planning and HA state remain change-oriented.

See [NETZ diagnostics](specifications/grid-profile.md#approved-netz-diagnostics-amendment--7-november-2026),
[PV-DIAG-01–14](specifications/pv-decision-diagnostics.md) and the
[current requirements baseline](specifications/v0.4-current-requirements.md).
The [previous guide](https://github.com/ekkehard-lutz/wallbox-manager/blob/c14f671263c3399d7e5e65147b1137ce0f78f490/docs/diagnostic-logging.md)
documents historical v0.3 trace families and reasons.
