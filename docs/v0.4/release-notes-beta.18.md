# Wallbox Manager v0.4.0-beta.18

**Beta.18 is an observability release, not a new charging-regulation algorithm.**
It extends the reviewed beta.17 runtime with five-value energy-system context at
relevant Level-2 PV decisions. Manifest: `0.4.0-beta.18`; annotated tag:
`v0.4.0-beta.18`. The standard tagged GitHub source archive is the HACS package;
no additional binary asset is required.

## Complete available power situation at a decision

PV_MAXIMUM, PV_OPTIMUM and battery-equipped PV_SURPLUS use the same field names
and semantics on existing target, mode/point decision and SoC/economic STOP/restart
events. These values provide context even when they are not immediate causes:

| Field | Canonical measurement / sign (W) |
| --- | --- |
| `ev_actual_w` | Actual measured selected EV power from canonical OCPP/session observations; never offered current converted to watts. |
| `grid_w` | Mapped grid import minus export; positive import, negative export. Both channels must be available. |
| `pv_w` | Mapped `leistung_pv`, total positive PV production. |
| `battery_w` | Mapped storage charge minus discharge; positive charging, negative discharging. The existing both-positive-pair normalization produces effective zero; either unknown channel produces `null`. |
| `household_w` | `max(0, leistung_verbraucher - ev_actual_w)` under the Fronius total-consumption-including-EV contract. |

Values come from the decision's existing captured input snapshot, including
across persistence awaits. Out-of-tick target validity changes use held
observations at that event. Last-value-held behavior is unchanged: silence does
not expire a value. Unavailable/missing measurements are JSON `null`, never
fabricated zeros or substitute planning/offer values. Keys appear once per event.
A decision snapshot is not a guarantee of simultaneous physical Fronius/OCPP
sampling; those systems can deliver observations asynchronously.

Logging stays sparse and semantic. Changes to reason, mode, target, point or
economic state produce the existing relevant events; repeated unchanged decisions
and harmless measurement noise do not create periodic Level-2 logs. Event names,
reasons, thresholds, point information, diagnostic levels and signatures remain.
No additional sensor polling, control cycle, OCPP command, persistence operation,
Recorder publication or background worker is introduced by diagnostics.

## Preserved regulation and compatibility

- FAST/BALANCE, SoC thresholds, economic Wh integration/STOP/reset/restart and
  phase/current point selection are unchanged from beta.17.
- NETZ, OCPP authority, transaction fences, command confirmation, charging release
  and independent hardware enable are unchanged.
- All **seven** beta.17 PV parameters retain their defaults: increase interval
  **3 cycles**, startup hold **10 cycles**, grid deadband **100 W**, decrease
  delay **0 cycles**, import budget **50 Wh**, EV uptake **50%**, FAST economic
  restart household threshold **500 W**. No live setting is changed.
- No entity identity, translation, source mapping, dependency, resource URL,
  data/schema or journal migration changes. No new mandatory measurement mapping
  is added; missing context is logged as `null`.
- Stationary and installed inverter/battery hardware retain physical electrical
  safety ownership. No Stationary update, restart or battery command is required.

## FAST hardware investigation remains open

The reported beta.17 PV_MAXIMUM sequence repeatedly explored up near zero grid
exchange and reduced, sometimes by multiple amperes, after brief positive import,
operating around 1p/8–10 A. Concurrent PV, battery and household values were absent.
The approximately 4,800 W practical discharge capability is empirical context,
not measured current battery output or a new software limit.

The new context is intended to distinguish battery response lag, genuine discharge
limits, changing PV/household demand and asynchronous observations. The existing
shared `pv_decrease_delay_cycles=2` is a reasonable separately supervised test
**after baseline capture**; it also affects BALANCE. This release changes neither
its default nor a live value and approves no new FAST algorithm, cap, filter,
timeout, grid-target controller or safety loop. See the
[FAST analysis and inspected manufacturer/source references](../pv-fast-power-diagnostics.md).

## HACS upgrade and rollback

Installation is a **separate operator action**. Publication does not install,
reload or restart Home Assistant, change authority/release/settings, issue
wallbox commands or perform hardware acceptance.

For an authorized upgrade from beta.17, select prerelease **v0.4.0-beta.18** in
HACS, use the normal operator-controlled HA restart, and verify manifest version
**0.4.0-beta.18**. Preserve existing configuration, mappings and V3 journals.
Beta.18 adds no migration relative to beta.17, so an operator-controlled package
rollback to beta.17 uses the same schema; do not erase accumulated Wh/STOP history
or replay saved charging intent. Follow the existing
[beta.17 backup, earlier-version migration and rollback precautions](release-notes-beta.17.md#hacs-upgrade-and-rollback)
when coming from or returning to versions before beta.17. A package-only downgrade
to beta.16 does not restore its retired keys or interpret V3 economics.

After separate installation, the operator will capture a new Level-2 PV_MAXIMUM
FAST log before approving further optimization. Physical EV/battery response,
measurement timing, full station bootstrap and live HA performance acceptance
remain distinct from simulated tests.

## Evidence

- [Release validation](release-validation-beta.18.md)
- [Approved five-power-value specification](../specifications/pv-decision-diagnostics.md)
- [Requirements and decision status](../specifications/README.md)
- [FAST diagnostics and analysis](../pv-fast-power-diagnostics.md)
