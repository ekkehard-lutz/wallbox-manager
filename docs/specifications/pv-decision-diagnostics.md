# PV regulation decision diagnostics — Level 2 contract

**Decision:** 25 October 2026. **Status:** approved *scope and log-minimization intent*, with exact event taxonomy and numeric filtering thresholds subject to implementation review. **IDs:** PV-DIAG-01–10. Complements [PV regulation V3](pv-regulation.md), [WBM-ARCH-01–12](architecture-responsibility.md) and current [passive diagnostics](../v0.4/recorder-availability-report.md). Historical beta.16 status was requirements-only. Sparse diagnostics were published in beta.17; the explicitly approved five-value expansion below is implemented for beta.18.

## Approved five-power-value expansion — 26 October 2026

**Operator rationale:** Level-2 diagnostics must reconstruct the entire available
energy-system power situation at each relevant regulation decision, including
EV, grid, PV, battery and household power, so inverter/home-battery/household/EV
interaction can provide evidence for future FAST optimization. This is broader
than immediately causal inputs and does not authorize continuous measurements.

The PV FAST Analysis and Complete Level-2 Power Diagnostics task explicitly
supersedes the measurement-subset restriction in PV-DIAG-04 for five fields.
Every relevant PV decision, STOP/restart, target and mode-transition event in
PV_MAXIMUM, PV_OPTIMUM and battery-equipped PV_SURPLUS includes:

| Field | Canonical watts contract |
| --- | --- |
| `ev_actual_w` | Actual measured selected EV charging power; never offered power. |
| `grid_w` | Grid import minus export: positive import, negative export. Both source values must be available. |
| `pv_w` | Positive total PV production from `leistung_pv`. |
| `battery_w` | Existing signed storage normalization: charge minus discharge; positive charging, negative discharging. Both positive yields effective zero; either unavailable yields `null`. |
| `household_w` | `max(0, leistung_verbraucher - ev_actual_w)`, following Fronius total consumption including selected EV. Either unavailable yields `null`. |

Use the decision's existing input snapshot, including across persistence awaits.
Target validity changes outside a tick use the held observations at that event.
Unavailable values are JSON `null`; measured zero remains zero. Include each key
exactly once, alongside the existing cause-specific thresholds and technical
points. Do not substitute planning-only EV fallback power for unavailable selected
EV diagnostics. This bounded context is explicitly approved even when one field
is not a direct cause of that event; other unrelated source dumps remain excluded.
The expansion does not change semantic signatures, filtering, event identifiers,
measurement acquisition, polling, Recorder publication or control behavior.
PV-DIAG-02/05–10 retain their sparse, side-effect-free contract.

## Objective and existing levels

Level 2 (**relevant state changes**) must leave enough information to reconstruct **why a charging-regulation decision changed**, with the **minimum actual relevant measurements and reference values**. A log is for decisions, not a time series. Sensor measurements continue to be observable in HA; Level 2 does not reproduce Recorder, each sensor update, each PV tick or unchanged "holding" statuses. Keep the preexisting OCPP/connection/permission/authority/phase level taxonomy and events.

| ID | Normative diagnostic contract |
| --- | --- |
| **PV-DIAG-01** | Level 0 reports genuine faults; Level 1 adds significant initialization/connection/recovery events; **Level 2** adds material **PV target, SoC mode, FAST/BALANCE decision, economic stop/restart and actual requested/applied divergence transitions**; Level 3 may expose more bounded debug detail on explicit selection. This is an additive PV event family, not a reclassification of all raw OCPP input as a Level-2 event. Retain existing subsystem identity, severity filtering and existing OCPP diagnostics. |
| **PV-DIAG-02** | Level-2 decision events are **change driven**, not tick- or input-event driven. Emit when a canonical decision state, reason, target SoC, materially distinct requested feasible operating point, or economic state **actually changes**; log a relevant unsuccessful/denied transition only when its cause or outcome changes. An unchanged decision or repeated sensor report emits nothing. Pure command ACK and physical observation may be reported in existing OCPP families: avoid duplicate PV events for the same cause. |
| **PV-DIAG-03** | Each event contains minimal context: timestamp from logging facility, entry/connector's stable non-secret identifier (without transaction ID), PV profile, SoC mode **previous → new** when changing, event kind, reason code, and affected reference or operating point as relevant. Use a bounded, consistent structured payload with machine-readable stable keys. Avoid prose-only or huge nested snapshots. |
| **PV-DIAG-04** | Subject to the approved five-power-value expansion above, include **only additional measurements actually used to cause the decision**, in their original canonical units and consistent signs, plus the required comparison threshold or reference. **Target/SoC transition:** actual SoC, target T and hysteresis thresholds involved; **FAST adjustment:** signed grid exchange, actual selected EV power, old/new requested operating point and applied comparison band; **BALANCE adjustment:** effective signed home-battery power, actual selected EV power, actual SoC/target when deadband bias caused it, old/new operating point, and one-step direction; for a both-positive charge/discharge pair that actually changes the decision, log a compact transition reason rather than a full sensor dump; **economic Wh STOP:** measured raw grid import, threshold `M/2`, accumulated Wh and configured Wh limit (default 50), STOP/reset provenance and reason; **economic restart:** measured battery charge power (>0 W) or FAST-only house load excluding EV and its configured strict W threshold (default 500 W), if that branch caused the decision; **dynamic target:** newly selected T, planning phase and only the deciding forecast/household/capacity inputs. Include a missing-value/reason marker instead of invented zero. Do not attach battery/PV/forecast/etc. fields unrelated to the specific decision. |
| **PV-DIAG-05** | Never include every raw sensor, OCPP MeterValues packet, value-change callback, full source-generation vectors, repeated readiness check, user tokens/credentials, URL, access token, full transaction ID or unrestricted entity attributes. A compact cause code can express "response pending", "unavailable source", "grid import" or "SoC limit" without copying the underlying protocol trace. Level 3 may offer opt-in bounded detailed measurement/source diagnostics, but must not introduce another acquisition/polling loop. |
| **PV-DIAG-06** | Deduplicate on **semantic decision signature** per bound connector: mode, reason, effective target/feasible point and economic/action state, **not every floating-point measurement digit**. A changing sensor value with no decision transition does not generate a new event. Repeated validation failure produces one event on transition into failure and one on recovery, not a warning every five seconds. Do not emit a separate event for each partial processing stage of the same canonical tick. |
| **PV-DIAG-07** | Significant target/point changes should record meaningful differences at the **real actuator resolution** (e.g. a changed discrete ampere/phase selection), not sub-watt noise or shifting fractions that resolve to the same executable point. SoC-target or planning-phase transitions remain event-worthy even if the actual charging point has not yet changed. No arbitrary numeric quantization may alter sensor values, policy calculations or the Recorder; event-only formatting can be compact. |
| **PV-DIAG-08** | Level 2 must be **operationally sparse**: hundreds of stable PV ticks, hours of unchanged HA state and an inactive/non-authoritative WBM do **not** create recurring Level-2 PV logs. A ramp across materially different applied/selected current steps may create one event per **actual change** and is acceptable; unchanged HOLD and repeated pending states are suppressed. Do not introduce fixed periodic summary/heartbeat logs merely to prove the regulator is alive. Existing genuinely changed connection/authority events remain. |
| **PV-DIAG-09** | Logging is side-effect free, bounded in CPU and log volume, and must not create HA entities, Recorder rows, additional OCPP requests or await persistence. Errors in formatting/serialization must not interrupt the regulator, invalidate release or trigger control actions. Existing log-level configuration applies; avoid abuse of WARNING severity for ordinary optimization events and do not alter global HA log levels solely to make debug visible. Explicit error/warning remains reserved for actionable faults. |
| **PV-DIAG-10** | Acceptance: a replay showing FAST increase, stable HOLD, grid-import correction, BALANCE charge→discharge adjustment, SoC FAST/BALANCE/STOP transitions, changed optimum T, economic configured-Wh STOP, positive-charge/FAST low-house-load restart and allowed/blocked outcomes must be explainable from **Level-2 event sequence alone**, including *why* the key decision occurred and what operating point was requested. Replay an identical steady state for >=300 cycles and assert **zero additional PV Level-2 events**; small harmless measurement noise within same decision must not add events. Check no secrets, no raw packet blobs, no duplicate OCPP confirmations, bounded payload, correct locale-independent numeric field values and no regulation CPU/Recorder churn. |

## Illustrative Level-2 records (not an API schema)

These examples demonstrate **minimum per-cause context**, not required keys, exact values or a promise of an implemented algorithm.

- **FAST choice changed:** `event=fast_point_change reason=grid_import grid_w=640 ev_actual_w=3650 old=3p/7A new=3p/6A`.
- **BALANCE correction:** `event=balance_point_change reason=battery_discharging battery_w=-720 ev_actual_w=3200 soc_pct=43.1 target_soc_pct=42.0 old=1p/15A new=1p/12A`.
- **SoC transition:** `event=soc_mode_change from=BALANCE to=FAST soc_pct=46.2 target_soc_pct=44.0 upper_pct=46.0 reason=upper_threshold`.
- **Economic STOP:** `event=economic_stop reason=import_budget grid_import_w=1550 threshold_w=690 estimated_wh=50.8 budget_wh=50 action=request_0A`.

An unchanged both-positive source pair normalized to 0 W, a held 1p/6 A decision or a stable restart eligibility state does **not** produce repeated Level-2 PV logs. Actual policy STOP or restart emits one relevant cause/threshold record. No additional sampling or smoothing is used just for logging.

A Level-2 log includes the five approved power fields above, plus the measurements and thresholds that explain that particular decision; it does not list every source in the snapshot.

## Precedence, migration, and unresolved decisions

- Existing Level 0–3 passive/OCPP semantics are not deleted; PV level-2 decision events augment existing real status transitions, rather than creating a new log category number.
- The diagnostics text in [PV-QA-01](pv-regulation.md#pv-qa-01--do-not-confuse-diagnostics-with-effectiveness-approved) previously mentioned many fields as generally available. Interpret that as **diagnosable on demand**, not a mandate to log the entire field inventory in every Level-2 event. If detailed trace is needed, use bounded Level 3.
- Historical v0.3.2 beta detailed/debug logs and repeated per-tick snapshots are **not** an acceptable Level-2 output contract.
- **Open implementation choices:** minimum significance threshold for a dynamic T change (including time-bound planning), exact semantic event/signature fields, treatment of transient recalculated power candidate changes that quantize to the same feasible current, and structured logger severity for ordinary PV Level-2 event visibility. Decide them using deterministic tests and the user's installed HA logging settings; do not silently turn on unrestricted WARNING traffic.
