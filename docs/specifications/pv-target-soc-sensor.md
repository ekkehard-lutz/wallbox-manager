# Dedicated PV target SoC sensor — approved 27 October 2026

This operator-authorized observability amendment complements
[PV-SOC-01–03](pv-regulation.md#2-shared-battery-soc-state-machine),
[WBM-ARCH-12](architecture-responsibility.md) and the
[Recorder publication contract](../v0.4/recorder-availability-report.md).
It does not change target planning, charging policy or execution.

| ID | Approved contract |
| --- | --- |
| WBM-PV-TARGET-01 | Register one normal sensor for the configured station/EVSE/connector. Project exactly the existing canonical `PV.attributes()["effective_target_soc"]`; never recalculate, round or substitute zero. Preserve the card's existing value. |
| WBM-PV-TARGET-02 | Native unit `%`, state class `measurement`, no battery device class, default enabled with ordinary HA Recorder/history support. English name `PV Target SoC`, German `PV Ziel-SoC`. Entity IDs belong to HA. |
| WBM-PV-TARGET-03 | Use the existing `unique_id()` design: entry ID, family `pv`, JSON `[station,evse,connector,"effective_target_soc"]`. Associate with the existing entry/station device. Initial setup and reload register exactly one such entity; all existing entity identities remain unchanged. |
| WBM-PV-TARGET-04 | Available precisely when the canonical effective target is valid: reserve+H for PV_MAXIMUM, canonical dynamic/latched PV_OPTIMUM target, configured valid battery-equipped PV_SURPLUS target. NETZ and battery-free PV_SURPLUS have no target. Missing/invalid mandatory formula inputs, profile invalidation or changed mappings make it unavailable until the existing planner establishes a valid target. Actual battery SoC is independent of target-formula validity. |
| WBM-PV-TARGET-05 | Planning/availability is independent of charging release, OCPP connectivity, authority and transaction. With release OFF, retain valid passive planning on the existing scheduler. Forecast and configuration changes take effect through that planner; no extra reads, calculation, timer or OCPP request. |
| WBM-PV-TARGET-06 | Publish only on exact value changes, availability transitions and normal registration/recovery. Reuse the canonical state notification mechanism with a narrow target key. Do not subscribe to general grid/session updates or attach volatile power, timestamps, planning phase or station connectivity. Generation-wide notifications with unchanged target cause no HA write. At least 300 unchanged cycles produce zero redundant target sensor writes or Recorder-significant attributes. |

The sensor exposes a calculated policy reference, not measured battery charge.
The actual SoC remains supplied by the existing battery integration. It can stay
available when actual SoC is unknown but the formula inputs are valid. PV_OPTIMUM
requires valid reserve, forecast energy and capacity even in its latched phases;
profile bounds and H follow existing validation. Valid held inputs never expire
solely because time passed. Recovery occurs on the next existing planning cycle
(or normal initial setup), with the original numerical precision.

## Hardware evidence and scope

The operator's 27 October 2026 PV_OPTIMUM hardware analysis reports expected
changes of the dynamic target, a correct SoC STOP and battery recovery that did
not reach the moving restart threshold within the recorded period. Continuous
actual-SoC/target visibility is required for future validation. The Fronius
inverter restart was an external hardware event, not a WBM regulation fault.
These are operator-provided findings, not a new hardware test by this task.

[Pause diagnostics](pv-decision-diagnostics.md#approved-pause-context-extension--27-october-2026)
provide the complementary captured-tick log context. Preserve strict STOP
`SoC < T-H`, STOP→BALANCE `SoC > T+H/2` with proven surplus and STOP→FAST
`SoC >= T+H`, plus independent economic STOP/restart. FAST/BALANCE mathematics,
planning, settings/defaults, technical restrictions, OCPP and journal formats
remain unchanged. No release, merge, deployment or hardware action is authorized.
