# Wallbox Manager diagnostic logging

Open **Settings → Devices & services → Wallbox Manager → Configure**.
Enable **Diagnostic logging** (German: **Diagnoseprotokoll**) for troubleshooting.
The single persistent option defaults to disabled. The internal key remains
`pv_diagnostic_logging` so existing installations retain their saved choice.
No migration or second switch is required. Diagnostic content depends on the
active feature, profile and current activity. Disable it after troubleshooting.
Normal warnings and errors are independent of this option.

English help text:
> Enables detailed Wallbox Manager diagnostics for troubleshooting. Content depends on the active feature, profile and current activity. Disabled by default.

German help text:
> Aktiviert detaillierte Wallbox-Manager-Diagnosen zur Fehlersuche. Der Inhalt hängt von der aktiven Funktion, dem Profil und der aktuellen Aktivität ab. Standardmäßig deaktiviert.

Records are INFO lines with a stable prefix followed by compact, sorted JSON:

```text
WBMGR subsystem=pv {"decision":...}
WBMGR subsystem=recovery {"stage":...}
```

PV records preserve the existing evaluation cadence and fields; see
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

Enable **Diagnoseprotokoll**, select NETZ at 4 kW with charging enabled, and
confirm 1 phase / 17 A before a full Home Assistant restart. Keep the station on
its separate test commit supporting explicit Connector.PhaseRotation.Actual
GetVariables. Collect `ha core logs | grep 'WBMGR subsystem=recovery'` after
restart. Compare the explicit phase response, schedule response, evidence and
final adoption/rejection sequence with the successful simulated peer path.

These diagnostics do not change recovery criteria, parsing, evidence lifetimes,
solver behavior, fencing, ownership, profile intent, charging commands or retries.
