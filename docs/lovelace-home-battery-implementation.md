# Lovelace home-battery display implementation

Implementation of approved WBM-HS-UI-01–15 on
`feature/lovelace-home-battery-status`, based on `develop` commit
`9a119b40c87a8e3a36a18f61dac561594e264f1a`. Documentation commits `ea61e0f1`
and `ba03eca6` were cherry-picked first, preserving the development runtime.
No normative rule is changed. Independent review, merge, release, deployment and
hardware acceptance remain separate.

## Target versus implementation

| Requirements | Implementation and evidence |
| --- | --- |
| 01–02 | Home battery block sits beside Wallbox parameters, below the live status grid. Both sections share a grid row; a card-width container query stacks them at 320 px of content width or less. Chrome geometry tests cover 280, 320, 400 and 640 px cards in German and English, including unchanged actual → requested wallbox parameters and no horizontal overflow. |
| 03, 07–09 | Actual SoC and NETZ reserve read the configured HA entity states independently. Every available battery percentage uses exactly one HA-language decimal at the presentation boundary (for example German `69,3 % → 40,0 %`). Missing, invalid, restored, unknown and unavailable observations render `—`; valid zero and fractional percentages remain visible. OCPP offline does not invalidate HA observations. Accessible text distinguishes target SoC from current minimum reserve. No age expiry is introduced. |
| 04 | Battery-mapped PV_SURPLUS projects the existing saved `soll_soc_speicher` Number state. The saved Number must match the read-only backend validity envelope, and the full-precision observed reserve must not exceed its ceiling `T-H`. The backend enforces the existing target/hysteresis ranges and `T <= 100-H` for this projection. External reserve changes immediately hide a formerly valid target when `R+H > T`; no target is clamped or saved. Card refresh and direct HA Number changes update the reference. No local target edit or optimistic target exists. Without battery mappings the reference is `—`. |
| 05–06, 10 | **PV_OPTIMUM dynamic T and PV_MAXIMUM effective R + H are unavailable in the current backend.** Both display `—`. Current develop has no PV planner/regulator or effective target projection to reuse. The card does not infer targets from optimum limits, forecast, planner-phase-like metadata, reserve or hysteresis. Dynamic phase/day/forecast and R/H target-update acceptance is therefore limited to the explicitly approved unavailable fallback until a separate backend implementation exists. |
| 11–12 | All five editors, step buttons, input handlers and profile-dependent rendering paths are removed. The five Number entities, unique IDs, services, validation, persisted settings and backend consumers are unchanged. HA service tests edit every Number, reject fractional fixed targets, switch profiles and unload/reload the integration without losing values/identities. |
| 13–14 | Profile, NETZ power/time controls, unified action, Remote takeover, live/session readings, warnings and wallbox-parameter formatting retain their behavior. No reserve/approximation editor is reintroduced. Charging, reserve ownership/restoration, release, timing and OCPP code are unchanged. |
| 15 | Focused Node projection tests, real Chrome layout checks and real-HA entity/service tests supplement the existing full regression suite. Pending NETZ reserve 40 with observed 20 shows 20; applied 40 shows 40; external 65 shows 65; restoration to 20 follows the observed value. Multiple cards use per-entry mappings and support rebinding and language changes. |

## Minimal backend metadata

`select.charging_profile` exposes `battery_soc_entity_id` and
`battery_reserve_entity_id` from the existing entry options. Changed mappings publish through a removable options listener. The same select
now also exposes `pv_surplus_target` containing the saved target and its maximum
valid reserve (`T-H`), or `None` for invalid target/hysteresis configuration.
Target/hysteresis changes update this metadata through existing settings
notifications; formatting and reserve measurements add no backend publications.
The card receives the already event-fed HA states through its normal `hass`
updates. Battery measurement events do not republish this metadata. Tests verify
one publication for a mapping change and none for subsequent battery samples.
There is no new battery sensor, cached observation, editable target parameter, planner,
timer, polling task or Recorder measurement stream.

NETZ reads the mapped writable Number observation directly. It never reads
requested `min_soc`, journal `original`/`temporary`/`pending`, service completion
or override diagnostics as proof of an applied reserve. Existing reserve
confirmation and compare-before-restore tests remain applicable unchanged.

## Modified files

- `custom_components/wallbox_manager/www/wallbox-manager-card.js`: read-only
  display, accessible locale text, responsive lower row and removal of five editors.
- `custom_components/wallbox_manager/select.py`: stable mapping metadata and
  change-only mapping publication and read-only fixed-target validity metadata; no setter or profile-selection change.
- `tests/test_wallbox_card.cjs`: focused battery projection/editor and all-profile decimal-formatting tests,
  preserving all existing card regressions.
- `tests/test_home_battery_display.py`: HA Number APIs/identity/persistence and
  mapping-publication and target-envelope tests.
- `tests/test_wallbox_card_layout.cjs`: deterministic real Chrome geometry tests.
- `.github/workflows/tests.yml`: run the browser geometry regression in CI.
- This report and the two incorporated specification/index files.

`settings.py`, `number.py`, `battery.py`, `grid_battery.py`, `grid.py`,
`grid_point.py`, `control.py`, `ocpp.py`, `session.py` and the canonical observation
store are unchanged from the base. No configuration migration or role/access
policy was added. No `wallbox-stationary` file or hardware was modified.

## Historical and openWB source cross-check

Inspected WBM `v0.3.2-beta.18` (`4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`):
`custom_components/wallbox_manager/www/wallbox-manager-card.js`, `pv_soc.py`
and `pv_optimum.py`. The historical card consumed backend `optimum_target_soc`;
`pv_target()` performed profile-specific target selection/normalization and
`optimum_target()` owned daily planning. This confirms that effective dynamic
and MAXIMUM targets belong in the backend. Those modules are absent from the
current runtime and were not restored; historical freshness/safety logic does
not override WBM-ARCH-01–12.

Inspected actual [openWB/core revision
849340ec8d91f4e7cca08a7664cf9ec0187fb496](https://github.com/openWB/core/tree/849340ec8d91f4e7cca08a7664cf9ec0187fb496):
`packages/control/bat.py` and `packages/modules/common/store/_battery.py`.
`BatData.get.soc` is distinct from requested `Set` power-limit fields, and the
battery store publishes measured SoC from component state. This supports the
observation/request distinction. Its default-zero data model and publication
rounding are not copied: WBM preserves unavailable values and source fractions.
No openWB controller or battery-control path is imported.

## Validation

- `.venv/bin/pytest -q`: **530 passed**, 95.60 seconds. Five dependency
  deprecation warnings (Home Assistant/backoff); no failures. Includes existing
  reserve confirmation/restoration, NETZ phase/power, manager release, timing,
  transaction/lifecycle, availability and publication regressions.
- `node tests/test_wallbox_card.cjs`: **74 passed**.
- `node tests/test_wallbox_card_layout.cjs`: **1 passed**, covering eight
  language/card-width combinations in real headless Chrome.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/ruff format --check .`: passed.
- `node --check custom_components/wallbox_manager/www/wallbox-manager-card.js`:
  passed.
- `git diff --check`: passed.

The HA/OCPP simulation suite and Chrome require local sockets, so they ran with
sandbox escalation. All endpoints were local test fixtures; no installed HA
instance or charging hardware was contacted.
Hardware testing and deployment were not performed or authorized.


## Independent-review follow-up (25 October 2026)

Review of `e057520` correctly identified that a saved target's numeric range is
insufficient evidence of present PV-SOC-01 validity after an external reserve
change. The correction adds only a read-only validity envelope and a comparison
with the existing HA reserve observation. It does not run a SoC state machine,
select a replacement target, or change any Number validation/service/persistence
behavior. In particular, it does not call the setting-edit validator during
rendering (which could emit messages and conflate unrelated Optimum settings
with the fixed-target display). Missing reserve, invalid configuration, and a
Number/envelope mismatch all show `—` independently of actual SoC.

The new one-decimal requirement updates WBM-HS-UI-02/09/15. Tests cover German
and English, integers/trailing zero, valid zero, fractional values and rounding,
independent unavailable values, all profiles, and unrounded reserve comparison
at 38.01 versus a valid ceiling of 38. HA observations and stored values retain
full precision. Existing Chrome mobile/desktop geometry checks pass with the
updated text. PV_OPTIMUM and PV_MAXIMUM targets remain unavailable (`—`).

Rechecked the same pinned beta.18 `pv_soc.py` and actual openWB
`packages/control/bat.py` source for this follow-up. Their source/target
separation remains applicable; historical target clamping and openWB zero/default
or rounding behavior are not imported.
