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
