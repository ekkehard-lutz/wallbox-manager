# PV regulation decision diagnostics — Level 2 contract

**Decision:** 25 October 2026. **Status:** approved sparse full transitions, five-value context and the later bounded active-cycle sample exception below. **IDs:** PV-DIAG-01–10. Complements [PV regulation V3](pv-regulation.md), [WBM-ARCH-01–12](architecture-responsibility.md) and current [passive diagnostics](../v0.4/recorder-availability-report.md). Historical beta.16 status was requirements-only. Sparse diagnostics were published in beta.17; the explicitly approved five-value expansion below is implemented for beta.18.

## Approved pause-context extension — 27 October 2026

**Source:** Operator task, PV Target SoC Sensor and Extended Pause Diagnostics.
This is a later observability-only amendment to PV-DIAG-01–10. The historical
26 October eleven-key schema below is preserved as provenance; its exact-key
restriction is now superseded **only for unchanged eligible pause decisions**.

Keep the same full/compact split and existing semantic signature. When the
existing decision has mode `STOP` **or its operating point has zero amps**, add
exactly these four keys to the existing compact `pv_sample`:

| Field | Captured-tick meaning |
| --- | --- |
| `soc_pct` | Actual observed battery SoC; no zero fallback. |
| `target_soc_pct` | Canonical effective target T from that planning/control cycle. |
| `pv_surplus_proven` | Existing `strict_surplus` evidence used by the controller; absent evidence is false, never proven surplus. |
| `economic_stopped` | Existing economic STOP provenance after that tick's budget decision, captured before persistence. |

For battery-free PV_SURPLUS the first three fields are `null` (battery policy
inapplicable); the economic state remains a boolean. Missing powers remain
`null`. The original five powers, their signs and their captured-snapshot
contract are unchanged. Values survive asynchronous persistence without rereading
HA sensors, live target or settings. Positive FAST/BALANCE samples keep exactly
the previous eleven keys. Full `pv_decision` records, reasons and thresholds are
unchanged; the first semantic STOP/restart transition remains full. Target drift
still emits the independent `pv_target`, never a full decision solely for digits.

Never include `soc_stop_pct`, `balance_restart_pct` or `fast_restart_pct` in compact
samples. H (`soc_hysterese`) is also absent there. No existing PV configuration
record reliably supplies H historically, so emit sparse `pv_config` with H on
the first eligible Level-2 decision in a context and when captured H changes.
Context boundaries are the existing profile/release/generation/transaction
lifecycle, integration startup/reload and disabling/re-enabling diagnostics.
It uses the existing decision path, without another scheduler or source read.
No power dump is attached to this configuration-only record. Repeated H produces
no repeated configuration record. Changes during inactivity are recorded on the
next eligible decision, not as a periodic passive stream.

The unchanged policy is strict SoC STOP `SoC < T-H`; STOP→BALANCE strictly
`SoC > T+H/2` with proven PV surplus; STOP→FAST inclusively `SoC >= T+H`.
Minimum-positive-point BALANCE STOP enters the existing SoC STOP mode. Economic
STOP can instead hold zero amps while the SoC mode remains FAST or BALANCE;
`economic_stopped` distinguishes this independent inhibition. A zero-current hold
needs no new pause state machine.

Levels 0/1, inactive/passive/release-OFF silence and Level-3 inheritance remain.
At five seconds there are still at most about 720 **decision-related** records
per continuous eligible hour, plus independently meaningful target/input changes
and sparse configuration context. This amendment increases pause-record size,
not the number of per-cycle records. No change to charging behavior is approved.

The [sensor specification](pv-target-soc-sensor.md#hardware-evidence-and-scope)
records the operator's 27 October PV_OPTIMUM hardware findings and external
Fronius restart. Acceptance extends PV-DIAG-10 with 300 STOP cycles, snapshot
mutation during persistence, moving T/SoC, sparse H, economic zero-point and
minimum BALANCE pauses, missing evidence/nulls and identical control outputs.

## Approved five-power-value expansion — 26 October 2026

**Operator rationale:** Level-2 diagnostics must reconstruct the entire available
energy-system power situation at each relevant regulation decision, including
EV, grid, PV, battery and household power, so inverter/home-battery/household/EV
interaction can provide evidence for future FAST optimization. This beta.18
approval broadened causal inputs without permitting unchanged-cycle records;
the later explicit exception below narrowly supersedes that suppression.

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
PV-DIAG-02/05–10 retain their side-effect-free contract, subject to the later
explicit unchanged-cycle exception below.

## Approved unchanged-cycle exception — 26 October 2026 (after beta.18)

**Source:** Explicit operator task, PV Level-2 Cycle Samples and Home Battery
Card Cleanup. This is a new bounded exception to beta.18's suppression of
unchanged decisions, not a reinterpretation of the earlier sparse policy.

> The regulator's behavior must be analyzable across consecutive regulation cycles, including cycles in which the decision and requested charging point remain unchanged. In particular, EV, grid, PV, battery and household power must remain observable throughout a configured downward-regulation delay and during the physical response of the inverter, home battery and vehicle.

For each completed **eligible active** regulation cycle reaching the existing
valid decision logging path, choose exactly one decision-related INFO record at
diagnostic level >=2:

- Changed existing semantic signature: preserve the complete `pv_decision`,
  including reason, thresholds/context, changed points/mode and all five powers.
- Unchanged signature: `pv_sample` contains **only** `event`, `station`, `evse`,
  `connector`, `profile`, `mode`, `ev_actual_w`, `grid_w`, `pv_w`, `battery_w` and
  `household_w`. Omit reason, thresholds, point structures and other metadata.

Full and compact records are mutually exclusive for that decision. Independently
significant target/input transitions may still occur in the same cycle.
Measurement digits never affect the signature. Preserve the captured five-value
snapshot across persistence awaits, canonical watts/signs, normalization and JSON
`null` rules. Apply uniformly to PV_MAXIMUM, PV_OPTIMUM and both battery-equipped
and battery-free PV_SURPLUS wherever the existing contract permits a valid
decision. Held `response_pending`, `startup_hold`, `increase_interval`,
`decrease_delay`, operating-point and valid STOP decisions follow the same rule.

Release OFF, passive target planning, NETZ and inactive/unavailable contexts that
do not reach a valid decision produce **no recurring decision/sample records**.
Retain change-driven target/input events in those contexts. No separate clock,
listener, acquisition, controller state, persistence, worker, entity, Recorder
publication or OCPP request is introduced. Logging failure cannot affect charging.
Levels 0/1 remain unchanged; Level 3 includes these records without a second stream.

## Objective and existing levels

Full Level-2 records explain changed decisions and remain sparse and semantic.
Compact `pv_sample` is the approved per-cycle exception for the five-power context
of unchanged eligible active decisions. This does not log each sensor callback
or create physical measurement samples. Preserve existing OCPP diagnostic taxonomy.

| ID | Normative diagnostic contract |
| --- | --- |
| **PV-DIAG-01** | Level 0 reports genuine faults; Level 1 adds significant initialization/connection/recovery events. Level 2 adds material PV target, SoC mode, FAST/BALANCE, economic STOP/restart and divergence transitions, plus the approved compact unchanged-cycle context. Level 3 includes these records and may expose bounded selected debug detail. Preserve existing subsystem, severity filtering and OCPP events. |
| **PV-DIAG-02** | Full decision records remain **semantic and change driven**. A changed existing signature emits one full `pv_decision`; an unchanged eligible active decision emits exactly one compact `pv_sample` instead. Never emit both for the same decision. Repeated sensor reports alone emit neither. Legitimate independent target/input transitions remain separate; do not duplicate OCPP ACK/observation events. |
| **PV-DIAG-03** | Use bounded stable keys and non-secret station/EVSE/connector identity, profile and mode. Full records retain reason, relevant previous mode, reference and operating points. The compact exception uses the eleven base keys plus the four approved pause-only keys where applicable, with no reason, threshold, point structure or transaction ID. The logging facility supplies timestamp. |
| **PV-DIAG-04** | Subject to the approved five-power-value expansion above, include **only additional measurements actually used to cause the decision**, in their original canonical units and consistent signs, plus the required comparison threshold or reference. **Target/SoC transition:** actual SoC, target T and hysteresis thresholds involved; **FAST adjustment:** signed grid exchange, actual selected EV power, old/new requested operating point and applied comparison band; **BALANCE adjustment:** effective signed home-battery power, actual selected EV power, actual SoC/target when deadband bias caused it, old/new operating point, and one-step direction; for a both-positive charge/discharge pair that actually changes the decision, log a compact transition reason rather than a full sensor dump; **economic Wh STOP:** measured raw grid import, threshold `M/2`, accumulated Wh and configured Wh limit (default 50), STOP/reset provenance and reason; **economic restart:** measured battery charge power (>0 W) or FAST-only house load excluding EV and its configured strict W threshold (default 500 W), if that branch caused the decision; **dynamic target:** newly selected T, planning phase and only the deciding forecast/household/capacity inputs. Include a missing-value/reason marker instead of invented zero. Do not attach battery/PV/forecast/etc. fields unrelated to the specific decision. |
| **PV-DIAG-05** | Never dump raw sensors, OCPP MeterValues, callbacks, generation vectors, readiness checks, credentials/tokens/URLs, transaction IDs or unrestricted attributes. The only repeated Level-2 measurement context is the bounded `pv_sample` with five powers and, during pauses, the four approved SoC/economic fields. Level 3 must not add a competing stream or acquisition/polling loop. |
| **PV-DIAG-06** | Keep the existing semantic decision signature per connector: mode, reason, feasible point and economic/action state, **not floating-point measurement digits**. Measurement drift alone produces compact samples, never new full transitions. An unchanged validation failure stays silent after its transition; recovery logs once. No extra full/compact record for partial stages of the same decision. |
| **PV-DIAG-07** | Significant target/point changes should record meaningful differences at the **real actuator resolution** (e.g. a changed discrete ampere/phase selection), not sub-watt noise or shifting fractions that resolve to the same executable point. SoC-target or planning-phase transitions remain event-worthy even if the actual charging point has not yet changed. No arbitrary numeric quantization may alter sensor values, policy calculations or the Recorder; event-only formatting can be compact. |
| **PV-DIAG-08** | Full records remain **operationally sparse**. Each otherwise unchanged eligible active decision emits one compact sample, including held delay/pending states. Inactive/non-authoritative contexts without a valid decision, release OFF and passive planning create no cyclic samples; unchanged HA callbacks create none. No independent heartbeat, summary, timer or second diagnostic stream is permitted. |
| **PV-DIAG-09** | Logging is side-effect free, bounded in CPU and log volume, and must not create HA entities, Recorder rows, additional OCPP requests or await persistence. Errors in formatting/serialization must not interrupt the regulator, invalidate release or trigger control actions. Existing log-level configuration applies; avoid abuse of WARNING severity for ordinary optimization events and do not alter global HA log levels solely to make debug visible. Explicit error/warning remains reserved for actionable faults. |
| **PV-DIAG-10** | Acceptance: FAST/BALANCE, SoC/target, technical-point and economic STOP/restart sequences remain explainable from full transitions plus samples. After an initial full steady decision, replay >=300 unchanged eligible cycles and assert exactly one compact sample per cycle and **zero additional full decision events**, including harmless noise. Verify mutually exclusive full/sample records, all PV profiles including battery-free SURPLUS, held reasons, levels 0/1 suppression, inactive/passive silence, per-cycle captured snapshots, signs/nulls, unique bounded keys, no secrets/raw blobs, no polling/OCPP/persistence/Recorder effects, identical regulation outputs and logging-failure isolation. |

## Illustrative Level-2 records (not an API schema)

These examples demonstrate **minimum per-cause context**, not required keys, exact values or a promise of an implemented algorithm.

- **FAST choice changed:** `event=fast_point_change reason=grid_import grid_w=640 ev_actual_w=3650 old=3p/7A new=3p/6A`.
- **BALANCE correction:** `event=balance_point_change reason=battery_discharging battery_w=-720 ev_actual_w=3200 soc_pct=43.1 target_soc_pct=42.0 old=1p/15A new=1p/12A`.
- **SoC transition:** `event=soc_mode_change from=BALANCE to=FAST soc_pct=46.2 target_soc_pct=44.0 upper_pct=46.0 reason=upper_threshold`.
- **Economic STOP:** `event=economic_stop reason=import_budget grid_import_w=1550 threshold_w=690 estimated_wh=50.8 budget_wh=50 action=request_0A`.

An unchanged both-positive pair normalized to 0 W, a held 1p/6 A point or a stable valid STOP/restart decision produces compact samples on eligible active cycles, not repeated full transitions. Changed STOP/restart retains the full cause/threshold record. No extra physical sampling or smoothing is introduced.

A full Level-2 decision log includes the five approved power fields and its deciding measurements/thresholds. A compact sample uses the eleven base keys plus the four approved pause-only fields when applicable, and omits thresholds/reason. Neither lists every source in the snapshot.

## Precedence, migration, and unresolved decisions

- Existing Level 0–3 passive/OCPP semantics are not deleted; PV level-2 decision events augment existing real status transitions, rather than creating a new log category number.
- The diagnostics text in [PV-QA-01](pv-regulation.md#pv-qa-01--do-not-confuse-diagnostics-with-effectiveness-approved) previously mentioned many fields as generally available. Interpret that as **diagnosable on demand**, not a mandate to log the entire field inventory in every Level-2 event. If detailed trace is needed, use bounded Level 3.
- Historical v0.3.2 beta detailed/debug dumps remain excluded. The compact active-cycle record, with the later four-field pause extension, is the sole approved unchanged-decision exception.
- Preserve the implemented semantic signature, point resolution and INFO severity.
  This exception does not approve retuning target significance or control behavior.
