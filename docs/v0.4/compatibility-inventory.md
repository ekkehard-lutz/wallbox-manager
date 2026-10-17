# Passive v0.4 compatibility inventory (recorded before deleting beta.18 code)

Reference: immutable `v0.3.2-beta.18`, commit
`87963014c9af73416c438807f523a3da2a98bd22`. Source inspection, not a live HA registry dump.
`E` denotes the existing config-entry ID; `S`, `V`, `C` the station, EVSE and
connector strings. JSON below uses compact separators except the profile storage
key, which retains Python's default spaced JSON. Domain remains wallbox_manager.

## Identity and metadata contracts

All entities use the existing device identifier `(wallbox_manager, E + ':' + S)`.
No entity_id is assigned by the integration: HA's existing registry entry retains
its name, entity_id, disabled flag, device association and statistics identity.
Translation keys equal the role unless noted. Station diagnostics have diagnostic
category; electrical and session measurements have no category.

| Domain / roles | Unique ID | Meaning / metadata |
| --- | --- | --- |
| sensor: voltage_l1/l2/l3 | `E:observation:[S,V,C,role,null]` | V, voltage, measurement |
| sensor: current_l1/l2/l3 | same | A, current, measurement |
| sensor: power, power_l1/l2/l3 | same | W, power, measurement |
| sensor: energy | same | cumulative imported kWh, energy, total_increasing; never session energy |
| sensor: connector_state, charging_state | same | observed enum; connector available/occupied/reserved/unavailable/faulted and charging idle/connected/preparing/charging/suspended_vehicle/suspended_station/finishing/unknown |
| binary_sensor: connected | `E:S:connected` | connectivity, always available, false when disconnected |
| sensor: protocol_version, connection_generation, boot_generation, revision, discovery | `E:S:role` | protocol/diagnostic evidence, discovery enum unknown/advertised/verified/unsupported/degraded |
| sensor: control_authority | `E:S:control_authority` | unknown/local/remote, actual observed authority |
| sensor: capabilities | `E:capability:[S,V,C,key]` | supported_phases, minimum_current, current_step, phase_switching (EVSE scope, C=null); maximum_current[_1/_2/_3], zero_current, enable_disable (connector scope); current-valued keys use A; diagnostic; evidence/source attributes |
| switch: charging_enabled | `E:control:[S,V,C,"charging_enabled"]` | observed physical permission; both actions rejected in passive milestone |
| button: take_control | `E:S:take_control` | explicitly unavailable; no backend takeover |
| number: desired_charging_power | `E:control:[S,V,C,role]` | local desired W, 0..100000, step100 |
| number: phase_switch_deviation_pct | same | local preference %, 0..25 step1 |
| number: allowed_current_1p/2p/3p | same | local limits A, 0..100000 step0.001, exact_current_limit_a retained |
| select: power_approximation | same | up/down/nearest local preference |
| select: charging_profile | same | NETZ/PV_SURPLUS/PV_OPTIMUM/PV_MAXIMUM; selection only, no execution |
| select: pv_approximation | same | up/down local preference |
| number: soll_power | same | stored power_kw, kW step0.1 |
| number: min_soc, soll_soc_speicher, soc_hysterese, optimum_lower_soc, optimum_upper_soc | same | %, existing configured values; cross-field SoC validation retained for edits |
| number: optimum_max_discharge_w | same | W 0..100000 step100 |
| number: estimated_daily_house_consumption_kwh | same | kWh 0..1000 step0.1 |
| number: regulation_interval, pv_start_delay, pv_stop_delay | same | seconds; interval1..300, delays0..3600; saved only |
| text: grid_start_delay, grid_duration | same | optional hh:mm, stored seconds; never arm a timer |
| sensor: session_id/started/ended/duration/power/energy/start_meter/end_meter | `E:session:[S,V,C,key]` | translation session_key; id, timestamps, seconds duration, W power (measurement), kWh session/register energies (no state_class) |
| binary_sensor: session_active | `E:session:[S,V,C,"active"]` | actual transaction lifecycle, no invented session |
| select: active_wallbox | `E:active_wallbox` | intentionally removed; global coordination is outside v0.4 |

V/C can be null for station/EVSE measurement scopes. Scope is preserved exactly:
MeterValues evse_id1 is EVSE scoped, transaction samples with explicit connector
are connector scoped. A convenient alias must never change an existing unique ID.

Common public joins: wallbox_manager_role/entry, station_id, evse_id,
connector_id, wallbox_manager_scope, wallbox_manager_target/targets. Provenance:
source, observed_at, received_at, valid_until; runtime_incarnation, connected.
Physical enum attrs: state_fresh, state_represents, physical_observability,
cp_observability, physical_limit_since. The new approved PV-OCPP-05 removes the
old CP ageing algorithm; explicit generation reset still invalidates live state.
Session attrs: session_id/active, external_transaction_id, start_known,
power_availability_reason, duration_sampled_at/valid_until and acquisition times.
Configuration execution-result attributes are deferred/null, explicitly report
execution_ready=false and passive_only, rather than loading a regulator.

## Storage and native HA layout

Config flow VERSION3; host/port entry data; sensor mappings, regulation options
and reference fallbacks in options. Wallbox subentries contain station/evse/
connector/references. Device registration already uses the physical identifier
above, often with no subentry; migration must also handle associated entries.
Detach devices/entities to the parent **before** removing a subentry: HA's public
async_remove_subentry clears registry associations and can delete attached entries.
Reject ambiguous multiple configured targets without modifying anything.

Store version1 `wallbox_manager.E.profiles`: mapping keyed by
`json.dumps([S,V,C])`, holding profile, power_kw, min_soc, approximation,
soll_soc_speicher, optimum lower/upper/discharge/daily consumption and optional
grid_start_delay/grid_duration. Central options override regulation fields.
Preserve every unrelated stored key/value; no destructive normalization on reads.
Manual power/current preferences use HA restore state in beta.18.

Store version1 `wallbox_manager.E.sessions`: records/latest/next_transaction_id,
record scope [S,V,C], real external ID, stable session_id, dates, energy_start_wh,
energy_end_wh, energy_charged_wh, power. Read last session for display; leave this
legacy file untouched for rollback. New passive session data must use a separate
store. Ownership, battery recovery, economic and PV-day stores stay untouched.
No Recorder/statistics API or deletion is necessary.

## Planned reuse and small module design

Extract only the station/device, observation/control/session/capability ID formats;
timestamp/unit/phase parsing rules; optional duration conversion. Reimplement
protocol lifecycle and passive sessions without legacy model dependencies.
Retain card CSS/layout and edit interactions as explicitly requested; rewrite
instance discovery and presentation. Metadata/translations remain public assets.

New modules: entry lifecycle; config flow; migration/binding; observation store;
OCPP2.1 transport/parser; local settings; common entity identity; thin HA sensor,
binary_sensor, number, select, text, switch and button platforms; frontend resource
registration. No control/solver/profile-policy/ownership packages.
