# Wallbox Manager v0.4.0-beta.16

This **prerelease** packages the independently reviewed Lovelace home-battery
status and PV card simplification, including review corrections in `39511a6`.
It implements WBM-HS-UI-01–15. Publication does not install or update Home
Assistant or Stationary; practical testing is separately authorized.

## Home battery status

- A read-only **Hausspeicher / Home battery** block sits below the live charging
  state/power column beside **Wallboxparameter / Wallbox parameters**. Narrow
  cards stack the blocks without horizontal scrolling. Existing wallbox applied
  versus requested values, session data, messages and charging actions remain.
- The arrow compares actual battery SoC from the configured HA mapping with the
  applicable profile reference. It does not indicate charging direction, promise
  battery activity or issue a command. All available percentages use **exactly one
  locale-aware decimal place**, including trailing zeros: German
  `69,3 % → 40,0 %`, English `69.3 % → 40.0 %`, valid zero `0,0 %` / `0.0 %`.
  Rounding occurs only in the card; source measurements and Recorder precision
  are unchanged.
- **NETZ:** the reference is the **actually observed writable minimum storage
  reserve**, including an applied temporary override or external change. Requested
  `min_soc`, pending writes and the original journaled reserve are not treated as
  confirmation. For example, a requested 40 with an observed 65 displays 65.0 %.
- **PV_SURPLUS with battery:** the saved fixed target is displayed only when valid
  against the observed reserve and existing hysteresis:
  `R + H <= T <= 100 - H`. The backend provides a read-only configured-target
  validity envelope, and the card compares the full-precision HA reserve against
  it. With T=40 and H=2, R=38 is valid; externally changed R=38.01 makes the
  reference `—`, without changing or clamping the setting. This is a validity
  check, not a frontend planner or a new regulator.
- **PV_OPTIMUM and PV_MAXIMUM:** their effective target values remain **`—`**
  until the respective backend regulators provide them. No forecast calculation,
  daily planner, R+H target generator or deferred PV regulation is introduced.
- Each side handles missing, unknown, unavailable or invalid values independently
  as `—`, never zero. Battery-free PV_SURPLUS and NETZ gain no new battery
  dependency. An OCPP disconnect alone does not invalidate valid HA battery
  observations. Multiple cards use their own entry mappings.

## Simplified PV card editors

The following five editors and their card handlers are removed:

- `optimum_lower_soc`
- `optimum_upper_soc`
- `optimum_max_discharge_w`
- `estimated_daily_house_consumption_kwh`
- `soll_soc_speicher`

Their **Home Assistant Number entities, unique IDs, services/APIs, validation,
persistence and backend consumers remain available** in normal HA entity views.
This is a presentation change with no roles or access restrictions.

## Compatibility and unchanged behavior

Charging control, NETZ power/phase selection, timing and Start/Apply/Stop,
manager charge release, OCPP behavior/authority/transactions, hardware safety,
battery reserve ownership/restoration and stored settings are unchanged.
There is no settings migration, measurement cadence change, frontend polling or
measurement-driven backend publication for the new display.

This UI feature is compatible with the **existing beta.15 Stationary
implementation**, identified in the beta.15 notes as develop
`279287c3c1d8863e803144eccf773ab3f10889a5` (reviewed implementation
`c32e9c935601b8b9271200f1dc3f669dde43275e`). **No Stationary update is required.**
Stationary retains sole ownership of CP, relays, registers and physical safety.
No Stationary source, version, tag, service or hardware operation is included.

## HACS upgrade and rollback

1. During separately authorized practical testing, enable prerelease/beta versions
   in HACS and select **v0.4.0-beta.16** for Wallbox Manager. Install through HACS
   and perform the normal operator-managed HA restart. Verify the integration
   manifest version is **0.4.0-beta.16**. HACS uses the tagged repository source
   archive; no additional binary/ZIP release asset is needed.
2. Refresh the browser/app cache on every dashboard client for
   `/wallbox_manager/wallbox-manager-card.js`. Keep card JavaScript and backend
   from the same version because the new display consumes backend metadata.
   No entity recreation or dashboard entity-ID migration is required.
3. Existing saved settings and mappings remain. Reload/restart retains the
   existing manager-release-OFF behavior; never assume charging resumes. Review
   actual observed state before any separate explicit Start.
4. For a separately supervised rollback, select **v0.4.0-beta.15** through HACS,
   perform the normal HA restart and refresh all frontend caches. Keep manager
   release OFF during the operation and retain ordinary configuration/storage
   backups. Beta.15 restores the previous card presentation; no settings migration
   needs reversal. Do not replay saved release or schedule state as an automatic
   charging instruction. The compatible Stationary implementation can remain.

## Validation and review evidence

Local final candidate validation passed: **530 Python tests**, **74 card tests**,
**one real Chrome layout test** covering eight language/width combinations, Ruff
lint/format, JavaScript syntax, Git whitespace and manifest/version consistency
checks. The tagged source archive is checked against the exact release commit.
Existing charging, OCPP, reserve and timing regressions run alongside the display
and preserved-Number-API tests. Successful CI on the exact develop release commit
and its annotated tag is required before prerelease publication.

See the [approved specification](../specifications/lovelace-home-battery-display.md)
and [implementation report](../lovelace-home-battery-implementation.md).
No merge into main, deployment, HA reload/restart, service restart or hardware
operation is part of this publication task.
