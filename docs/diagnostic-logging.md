# Wallbox Manager diagnostic logging

Open **Settings → Devices & services → Wallbox Manager → Configure** and choose
**Diagnostic level** (German: **Diagnosestufe**).

| Level | Output |
| --- | --- |
| 0 — Off / Aus | No dedicated trace. Normal operational warnings/errors remain active. |
| 1 — Events and errors / Ereignisse und Fehler | Deduplicated decisions, lifecycle transitions and meaningful command events. |
| 2 — Events with relevant data / Ereignisse mit relevanten Daten | The same events with explanatory measurements and context. |
| 3 — Full diagnostic trace / Vollständige Diagnosespur | Complete cyclic PV records and detailed recovery stages, plus lifecycle events. |

The persisted integer `diagnostic_level` replaces the legacy boolean
`pv_diagnostic_logging`. Legacy false/absent migrates to 0, true migrates to 3;
an explicit new level wins. Options-only level changes take effect live without
control reload, OCPP reconnect or commands. The default is 0; no card control is
needed. The level applies to PV, recovery, physical state, ownership and battery
reserve diagnostics.

Deduplication is centralized per entry, connector and event family. Identity uses
mode/phase/reason, discrete phase/current operating points, permission and control
states. Raw watts, voltage drift, SoC drift, timestamps and countdowns are payload,
not identity. Level 2 attaches relevant context only when a semantic event is new.
Real command attempts/outcomes remain visible; reuse of a confirmed point does not
create a fake command event. Changing levels clears the dedup baseline.

`WBMGR subsystem=control` events expose explicit Enable requests, pending reasons,
retries, confirmation, cancellation and failure, plus planner/SoC transitions.
Level 2 adds request epoch, generation, fence reason and preparation context.
`enable_pending` does not mean confirmed hardware ON. The charging switch always
represents readback. Dedicated collection/serialization failures cannot change
control behavior. Genuine command failures also have normal ERROR logging at 0.

Records are INFO lines with a stable prefix followed by compact, sorted JSON:

```text
WBMGR subsystem=pv {"decision":...}
WBMGR subsystem=recovery {"stage":...}
WBMGR subsystem=reconnect {"connected":...,"ownership":...}
```

At Level 3, PV records preserve the existing evaluation cadence and fields; see
[PV diagnostics](pv-surplus-profile.md#opt-in-wallbox-manager-diagnostics).
Recovery records work with NETZ, PV_SURPLUS and profiles using the same recovery
path. There is no raw OCPP frame logging, transaction identifier, credential or
URL in recovery records. Unrecognized PhaseRotation values are redacted to
`unrecognized`; protocol statuses are normalized.

```sh
ha core logs | grep 'WBMGR'
ha core logs | grep 'WBMGR subsystem=pv'
ha core logs | grep 'WBMGR subsystem=recovery'
```

## Recovery sequence

| Stage | Evidence |
| --- | --- |
| `start` | Station/EVSE/connector, profile, persisted enabled intent, actual permission, ownership state, matching active transaction boolean, generation and profile epoch |
| `attempt` | Actual electrical reconciliation started |
| `phase_evidence` | Existing physical evidence source (including NotifyEvent), absent/present, each observation valid/expired/unknown and expiry |
| `phase_readback` | Separate GetVariables request, normalized status/value, parsed phases, stored/discarded observation and freshness |
| `composite_schedule` | Separate GetCompositeSchedule request (EVSE, 60 seconds), response status, schedule EVSE/start/unit, period count, current and phase count |
| `electrical_evidence` | Physical/schedule phases, current, voltage values/freshness, phase evidence after readback, capability envelopes, current limits, profile, requested power and fence revisions |
| `fence` / `rejected` | Existing validation branch that prevents adoption |
| `adoption` | `result=point_adopted`, `phases`, `current_a`, `voltages_v`, `power_w`, `charging_command_sent=false` |
| `waiting` | Reason, `pending=true`, existing retry interval; identical waiting states logged only once per recovery |
| `resume` / `complete` | Existing persisted permission-on/off path |

All records include station, EVSE and connector. Actual electrical retry attempts
retain their own readback sequence; this instrumentation adds no requests or
retries. The existing five-second phase lifetime is unchanged. A rejected read
can leave existing fresh NotifyEvent evidence usable; a rejection record alone
therefore does not imply adoption must fail. Consult the subsequent evidence and
adoption/rejection records. `charging_command_sent=false` describes adoption
itself; the existing subsequent profile reconciliation still runs unchanged.

## Stable reason values

* Initial/runtime/fence: `runtime_unavailable`, `capability_evidence_missing`,
  `charging_enabled_unknown`, `adapter_unavailable`, `command_fence_stale`,
  `runtime_closed`, `profile_not_permitted`, `connection_generation_changed`,
  `authority_changed`, `no_authority`, `charging_enabled_changed`,
  `generation_changed`, `point_command_pending`, `electrical_inputs_changed`,
  `profile_recovery_context_changed`.
* Phase readback: `phase_readback_not_supported`, `phase_response_rows_invalid`,
  `phase_readback_rejected`, `phase_response_scope_invalid`,
  `phase_value_invalid`, `phase_readback_timeout`, `phase_readback_failed`,
  `phase_readback_stale`. Rejected and UnknownVariable remain distinct in
  `status`; invalid accepted values have `phases=null`. Observation results are
  `observation_stored` or `observation_discarded`, with `fresh` and `valid_until`.
* Schedule: `composite_schedule_not_supported`, `transaction_missing`,
  `transaction_changed`, `composite_schedule_rejected`,
  `schedule_evse_mismatch`, `schedule_unit_unsupported`, `schedule_time_invalid`,
  `schedule_periods_ambiguous`, `schedule_period_invalid`,
  `schedule_current_negative`, `composite_schedule_timeout`,
  `composite_schedule_unavailable_or_invalid`. Exceptions include only their
  class in `error_type`, never their message or raw response.
* Adoption validation: `readback_rejected_or_stale`,
  `phase_evidence_unavailable`, `schedule_phase_missing`,
  `schedule_phase_mismatch`, `voltage_missing`, `voltage_stale`,
  `electrical_resolution_rejected` (with existing `blocked` / `solver_reason`).
  Phase absence versus expiry is exposed in the accompanying phase evidence.
* Profile recovery: `restored_permission_off`, `recovery_missing_profile`,
  `persisted_permission_on`, `recovery_waiting_measurements`,
  `recovery_waiting_electrical`, `recovery_waiting_runtime`.
* Diagnostic collection only: `diagnostic_snapshot_failed`.

## Next hardware test

Select **Diagnosestufe 3**, select NETZ at 4 kW with charging enabled, and
confirm 1 phase / 17 A before a full Home Assistant restart. Keep the station on
its separate test commit supporting explicit Connector.PhaseRotation.Actual
GetVariables. Collect `ha core logs | grep 'WBMGR subsystem=recovery'` after
restart. Compare the explicit phase response, schedule response, evidence and
final adoption/rejection sequence with the successful simulated peer path.

These diagnostics do not change recovery criteria, parsing, evidence lifetimes,
solver behavior, fencing, ownership, profile intent, charging commands or retries.

## PV regulator input trace (v0.3.2-beta.8)

This beta adds diagnostics only. It does **not** fix power oscillation, SoC target
tracking, asynchronous power balances, minimum hold, or command deduplication.
Levels 0–2 keep their existing event projection. The additional fields below are
restricted to level 3; no metering, sensor polling or OCPP requests are added.

### Existing mappings and coverage

Mappings are existing integration options; no migration or new mandatory mapping
is introduced. `external.<reference>.entity_id` identifies the configured entity
at runtime. The repository does not contain the user's live HA configuration.

| Measurement | Existing source | Consumption / level-3 coverage |
| --- | --- | --- |
| Battery SoC (%) | `soc_speicher_aktuell` | Shared policy for all three PV profiles; optional for PV_SURPLUS |
| Minimum reserve (%) | `min_soc_speicher` | Existing battery policy reader; mapping is shown, but this separate reader is not a regulator power sample |
| Battery discharge (W) | `storage_discharge_power` | FAST in all applicable profiles; also validated by PV_OPTIMUM in BALANCE |
| Maximum discharge (W) | Profile setting `optimum_max_discharge_w` | Configured limit, not measured discharge or a dynamic BMS limit |
| Battery charge / signed power | No existing mapping | Explicit null; cannot infer charge from nonnegative discharge |
| Dynamic battery discharge limit | No existing provider | Explicit null |
| Grid import / export (W) | `grid_import_power`, `grid_export_power` | FAST in all applicable profiles; also validated by PV_OPTIMUM in BALANCE |
| Signed grid power (W) | Derived from both consumed inputs | Import minus export; null if either input is unavailable or unused |
| PV power (W) | `leistung_pv` | Existing raw and smoothed inputs for all PV power paths |
| Total consumption (W) | `leistung_verbraucher` | Includes selected EV; existing raw and smoothed inputs |
| Selected EV power (W) | Scoped `session_power` sensor | Existing entry/station/EVSE/connector/runtime identity selection |
| Non-EV load / surplus (W) | Existing calculation | `site_load_w = smoothed consumption - measured EV`; `surplus_w = smoothed PV - site_load_w` |
| Capacity / remaining forecast | `storage_capacity`, `remaining_pv_energy` | Existing Optimum planner and validation; normalized Wh |

In particular, FAST under PV_MAXIMUM now exposes its actual battery and grid
inputs. PV_SURPLUS without SoC still works without battery/grid mappings.
Unused optional mappings are **not read just for logging**: `not_read` means
configured but not consumed in this evaluation, not that the sensor is broken.
`not_configured`, `missing`, `unknown`, `unavailable`, `stale`, `future`, and
`expired` remain distinguishable. A valid zero stays numeric zero.

### Per-evaluation records

The existing `WBMGR subsystem=pv` JSON event contains `regulator_evaluations`, an
ordered list of actual `pv_request` evaluations within the cycle. Dispatch fences
can evaluate again before writing: each evaluation gets its own `evaluation_id`
(cycle start timestamp plus local index), consumed `input_reads`, calculations,
mode, target, L/M/U thresholds, request, direction and reason. No persistent ID
counter is needed. The top-level `evaluation_id` identifies the latest evaluation.

Each `input_reads` entry includes its `reference`, entity identity, raw value/unit,
normalized value/unit, and timing. Repeated reads are retained in order because
the policy validator and power calculation may consume the same entity separately.
The top-level `external` and `wallbox_power_sources` expose the latest consumed
states for compatibility; they are not refreshed from HA after the calculation.
An unsuccessful later evaluation does not inherit earlier calculation values.

| Field | Meaning |
| --- | --- |
| `calculations.raw_pv_power_w`, `smoothed_pv_power_w` | Actual raw and averaged PV input |
| `calculations.raw_consumption_power_w`, `smoothed_consumption_power_w` | Actual raw and averaged total consumption |
| `calculations.measured_power_w` | Selected EV measurement; not a setpoint |
| `calculations.site_load_w`, `surplus_w` | Existing calculated non-EV load and surplus, including negative/inconsistent results |
| `request_w` | Returned regulator/policy request before discrete selection and minimum hold |
| `regulator.previous_request_w` | FAST internal request before this exact call; null when absent/unused |
| `regulator.previous_updated_at_monotonic`, `now_monotonic`, `updated_at_monotonic` | FAST ramp timing in process-local seconds |
| `regulator.previous_import_since_monotonic`, `import_since_monotonic` | FAST import-grace state before/after the call |
| `regulator.interval_s`, `grid_deadband_w`, `import_grace_s` | Existing FAST tuning: configured interval, 100 W, 3 s |
| `regulator.wallbox_power_w`, `battery_discharge_power_w`, `battery_max_discharge_power_w`, `grid_import_power_w`, `grid_export_power_w` | Exact normalized FAST call arguments |
| `battery_soc_pct`, `battery_discharge_power_w` | Validated consumed measurements; null if not consumed/invalid |
| `battery_max_discharge_power_w` | Existing configured maximum, separately named from actual power |
| `battery_charge_power_w`, `battery_power_signed_w`, `battery_dynamic_discharge_limit_w` | Null with explicit unsupported-mapping/provider reason |
| `grid_net_power_w`, `grid_net_basis` | Signed import minus export and derivation status; this does not claim synchronized measurements |

The reserved signed battery convention is positive discharge, negative charge.
Beta.8 cannot populate signed battery power: the existing discharge input is zero
while charging, so negating or treating it as signed would be misleading.
The existing top-level `observed_net_grid_import_w` is retained for compatibility;
it is clipped and historically defaults absent inputs to zero. Use the new
nullable `grid_net_power_w` for signed-grid analysis.

The existing `desired`, `executable`, `selected`, `commanded` and `applied` remain
separate. `applied_basis` explicitly describes an acknowledged or reconciled offer,
**not physically measured power**. Compare it with `measured_power_w` and telemetry.
`control_generation`, scope and timestamps help correlate control command events.
The latest PV evaluation is not necessarily the original sample that caused a
command: inspect the ordered evaluation list when fences re-plan.

`phase_lockout_evidence` reports retained specific station-rejection evidence;
`phase_lockout` and the existing retry countdown remain separate. A retry deadline
is not the end of the station's phase lockout. `reenable_lockout_evidence` reports
unknown, with a null station deadline: no new 300/600-second timer is implemented.

### Timestamp semantics

All wall-clock times are timezone-aware ISO 8601. Each consumed input reports:

- `source_observed_at` / `source_received_at`: explicitly supplied `observed_at` /
  `received_at` attributes only. Missing or invalid source times stay null;
  `source_timestamp_status` explains availability. No HA timestamp is relabeled
  as a device timestamp.
- `last_updated` / `last_reported`: HA state-change and report times. Older state
  objects without `last_reported` report null for that field.
- `timestamp` / `age_basis`: the exact HA time used by the existing freshness
  check. No freshness rule is changed.
- `evaluated_at` / `age_s`: the original reader's evaluation time and report age.
- `valid_until`: the source's explicit validity, if present.
- `effective_valid_until`: the earlier of report-time freshness expiry and
  explicit validity. Live inputs use 90 s; remaining PV forecast uses 900 s.
- `reading_accepted`: whether the existing reader accepted units/value/time;
  `normalized_value` is null on rejection. Further policy checks can still reject
  a numerically readable negative value; consult the evaluation reason.

The selected session-power sensor now exposes source times from its already
selected matching observation, without a new observation lookup or poll. External
integrations may not expose source timestamps; that limitation remains visible.
`started_at` / top-level `evaluated_at` describe diagnostic cycle/context timing;
use the individual input's time for measurement-age comparisons.

### Next PV_MAXIMUM hardware test

1. Install beta.8, restart HA, and confirm integration version `0.3.2-beta.8`.
2. In Wallbox Manager Configure, select diagnostic level **3**. Ensure INFO logs
   for `custom_components.wallbox_manager` are retained. Do not enable raw OCPP
   payload logging merely for this test.
3. Verify existing PV, total-consumption, SoC, reserve, discharge, grid-import and
   grid-export mappings. Record the configured discharge limit and smoothing /
   regulation intervals. Do not add a substitute zero sensor for a missing input.
4. Reproduce PV_MAXIMUM with reserve 78%, H=2%, target 80%, L/M/U=79/81/82% and
   an initial SoC above 82%, if those are the intended test conditions. Keep the
   existing wallbox/vehicle limits and safety configuration.
5. Start capture before enabling charging. Retain complete `WBMGR` lines through
   startup, FAST, the transition below 81%, and at least 15 minutes of BALANCE,
   including an interval after any phase lockout ends. Note real household-load
   changes and whether the device follows the commanded phase/current.
6. On HA OS, collect the available logs with `ha core logs | grep 'WBMGR'` and
   save them outside the integration repository. For longer tests, ensure the HA
   log file/retention covers the whole test; the CLI output is not an unlimited
   historical recording. Do not filter out control/physical-state events.
7. Verify that FAST records contain `regulator_evaluations`, battery/grid call
   arguments and per-input timestamps. Preserve full JSON lines without wrapping
   or truncation. Restore the normal diagnostic level after capture.

The local `.analysis/pv_maximum_test.log` is excluded and must not be committed.

### Deferred regulation requirement

For the next optimization phase, a **hard charging-power limit takes precedence
over minimum-positive hold**. If no positive point is permissible, a deliberate
zero-current pause is acceptable even if it triggers the existing 600-second
reenable lockout. This is agreed future behavior, **not implemented in beta.8**.

## Reconnect and physical-state transitions

The same switch enables `subsystem=reconnect`. Records are emitted only when the
reported state changes, not for every unchanged voltage sample or permission poll.
They include transport connectivity, connection/boot generations, fresh observed
authority, ownership status and retained-proof boolean, permission intent versus
actual permission, and connector/charging observation values and freshness.

* `connected=false`, `prior_ownership_retained=true`: temporary outage, execution fenced.
* New connection/boot generation with unknown authority: reconnect awaiting evidence.
* Fresh `authority=remote`, `ownership=restored_ownership`: existing proof reconciled.
* `ownership=ownership_rejected_local`, no retained proof: Local invalidated ownership.
* `physical_states` changes: incoming CP evidence or a permission transition changed
  the validity of the raw observation. The canonical HA charging sensor separately
  exposes confirmed OFF as described in [runtime state](metering-runtime-state.md).

`charging_command_sent=false` in these observer records means the notification
handler sent no command. Subsequent `subsystem=recovery` records describe electrical
readback/adoption and permission reconciliation; ordinary profile diagnostics retain
their existing command reporting. No additional switch or polling was introduced.
