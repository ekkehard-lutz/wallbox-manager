# Inactive controls cleanup review

## Baseline and approved scope

The branch starts at `76420d9eccc2fa71b54484e413951e040feba862` on `develop`. Its [exact baseline CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42638084085) passed. The [approved cleanup inventory](https://github.com/ekkehard-lutz/wallbox-manager/blob/863a6aece4b80e09d2780212eca7cf9d68876743/docs/v0.4/code-parameter-cleanup-inventory.md) is at `863a6aece4b80e09d2780212eca7cf9d68876743`.

- **R01–R04, R07 and R13.** Retired the desired-power Number and its `target_w` preference, the PV approximation Select and its `approximation` preference, the maximum-discharge Number and its `optimum_max_discharge_w` preference, and the `power_smoothing_window` option. The desired-power restore removal also eliminates F4. An old `pv_diagnostic_logging` flag is ignored. Discovery still reports `unknown`, `verified`, `unsupported` and `degraded`.
- **V03–V05.** Retired `legacy_limit_migration`, `valid_until`, `enabled_valid_until`, `duration_valid_until`, `profile_status`, `control_status`, `profile_control_ready` and `push_only`. The current cap and command attributes retain their existing semantics.
- **S01–S03.** Reads use the canonical `State.value()` path. OptionsFlow uses one application-level Number validation path, with current runtime context when available and the shared validator for unloaded entries. The HA Number selector retains its existing input coercion. The frontend component-loaded callback remains registered, with its unused unsubscribe handle removed.

## Public settings and registration

| Surface | Before | After |
| --- | --- | --- |
| Number roles | 20 possible roles | 18. Removed `desired_charging_power` and `optimum_max_discharge_w`. Retained the three conditional `allowed_current_{1,2,3}p` roles, `soll_power`, `min_soc`, `soll_soc_speicher`, `optimum_lower_soc`, `optimum_upper_soc`, `estimated_daily_house_consumption_kwh` and the nine regulation Numbers below. |
| Select roles | 3 roles | 2. Removed `pv_approximation`. Retained `charging_profile` (NETZ, PV_SURPLUS, PV_OPTIMUM and PV_MAXIMUM) and NETZ `power_approximation` (up, down and nearest). |
| General options | 11 fields | Same 11 fields. `diagnostic_level`, `min_soc_speicher`, `soc_speicher_aktuell`, `leistung_pv`, `leistung_verbraucher`, `storage_discharge_power`, `storage_charge_power`, `storage_capacity`, `remaining_pv_energy`, `grid_import_power` and `grid_export_power`. |
| Regulation options | 10 fields | 9. Removed `power_smoothing_window`. Retained `regulation_interval`, `soc_hysterese`, `pv_increase_interval_cycles`, `pv_startup_hold_cycles`, `pv_decrease_delay_cycles`, `pv_grid_deadband_w`, `pv_grid_import_budget_wh`, `pv_ev_uptake_threshold_pct` and `pv_fast_restart_household_load_limit_w`. All nine remain available as Numbers that are disabled by default. |

The standard HA services for retained entities continue to work. The explicit `wallbox_manager.charge_action`, `wallbox_manager.step_power` and `wallbox_manager.edit_timing` entity services and their schemas are unchanged. Generic `button.press` on `charge_action`, `take_control` and the independent `charging_enabled` switch are retained. The Lovelace asset, URL and production JavaScript are unchanged.

## Persisted state and observations

An existing current-format profile can contain `target_w`, `approximation` or `optimum_max_discharge_w`. Setup excludes these keys from runtime settings. Existing unknown options, including `power_smoothing_window` and `pv_diagnostic_logging`, may pass through an ordinary save with their old values. The form ignores submitted retired keys and the runtime ignores saved retired options. The branch does not rewrite storage or remove historical registry records to erase these entries. Old registry records may still be visible as unavailable in an existing installation.

Current formats for scoped settings, NETZ approval/timing/release, battery reserve ownership, the PV economic journal and the passive session are unchanged. The cap Number keeps `exact_current_limit_a`. Stored values still normalize against the current station ceiling, including exact fractions read from existing data and explicit 0 A disabling of a mode. The removed Envelope dictionary served only the retired HA diagnostic. Current normalization and both current-cap restoration paths remain.

NETZ exposes `command_status`, `command_reason`, `execution_ready` and `execution_blocked_reason`. Scheduled start/end, duration, countdown, expiry and explicit Stop keep their existing lifecycle. Ordinary observations remain held within their generation, including the approved invalid-phase handling. Recorder publication rules are unchanged.

OCPP initialization previously returned `status`, `missing` and constant `push_only: true`. It now returns `status` and `missing`, including when projected in the HA discovery sensor. Boot, unsolicited reports, discovery, GetVariables, applied-point readback, the regular five-second `ChargingEnabled` read and connection/boot fencing retain their behavior.

## Policy and review boundaries

The charging policies remain unchanged. NETZ power/phase selection, scheduled window and Stop, and PV FAST, BALANCE and SURPLUS still use the same inputs and dispatch rules. BALANCE continues to use unsmoothed battery flows and the economic journal uses unsmoothed measured import. F3 journal validation, the saved `reset` field, same-ID recovery, new-transaction rollover and the complete closed-transaction ID set are retained. There is no new polling, control loop or hardware safety logic. Stationary retains electrical and physical safety ownership.

The source cross-check used WBM `v0.3.2-beta.18` at `87963014c9af73416c438807f523a3da2a98bd22` (`docs/phase-lockout-protocol.md:16–46`), which distinguishes lockout outcomes from actual phase feedback. Stationary `279287c3c1d8863e803144eccf773ab3f10889a5` (`ocpp_runtime/device_model.py:28–44,117–139,193–218`, `ocpp_runtime/phase_feedback.py:1–38` and `ocpp_runtime/transactions.py:132–174,214–237`) confirms the live device model, physical feedback and transaction boundary. The openWB comparison at `849340ec8d91f4e7cca08a7664cf9ec0187fb496` (`packages/control/chargepoint/chargepoint.py:154–188`) confirms station-side interlock checks and unplug handling. None of the removed settings or diagnostics participates in these interface contracts.

No additional production consumer of a removed key was found. Historical entry/registry adapters, PV and session importers, session serialization, OptionsFlow section localization fallbacks and unique-ID projection are outside the cleanup. [Diagnostic logging](../diagnostic-logging.md) and the affected [architecture](../specifications/architecture-responsibility.md), [passive OCPP](../specifications/passive-v0.4.md), [charge release](../specifications/charge-release.md), [grid profile](../specifications/grid-profile.md), [home battery UI](../specifications/lovelace-home-battery-display.md) and [PV regulation](../specifications/pv-regulation.md) requirements reflect the approved removals. Dated implementation and release records remain historical.

## Validation

- Full `python -m pytest -q` with integration coverage and the local Stationary source fixture passed 1,145 tests in 289.71 seconds. Statement coverage was 95% (4,517 statements, 232 missed). Five dependency deprecation warnings remain, one from the Home Assistant HTTP application subclass and four from Backoff's use of `asyncio.iscoroutinefunction`.
- The focused NETZ, PV, OCPP, fault, teardown, observation, options and frontend run passed 566 tests. The final diagnostic-level and locale assertions also passed a five-test targeted run.
- Ruff lint and format, JavaScript syntax, 95 card regressions and the headless Chrome layout fixture passed. Manifest and HACS metadata are byte-identical to the baseline. The three localization files have matching structures and exactly the approved key removals. All three service schemas, 56 local documentation links and anchors, and Git whitespace checks passed.

Validation uses simulated Home Assistant fixtures, local loopback OCPP peers and a read-only Stationary source fixture. No physical installation, wallbox, EV or inverter was contacted. No version, tag, release or deployment changes are part of this branch.
