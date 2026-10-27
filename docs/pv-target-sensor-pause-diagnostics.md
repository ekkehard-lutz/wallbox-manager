# PV target sensor and extended pause diagnostics

Date: 27 October 2026. Branch:
`feature/pv-target-sensor-pause-diagnostics`.
Base: latest fetched `origin/develop`,
`5c1187d1ced7cdc36e323fd726c7beff1cdbf08d`; published comparison:
`v0.4.0-beta.19`, `3f07b17b478bcda6e6f9171a4eee2feb13725fcd`.
The original checkout contains unrelated untracked investigation documents and
`pv2.log`. They remain untouched. Implementation uses a clean isolated checkout.

## Implementation and requirement trace

[WBM-PV-TARGET-01–06](specifications/pv-target-soc-sensor.md) defines the new
sensor, applying WBM-ARCH-05/06/12 and existing entity identity/Recorder contracts.
[PV-DIAG-01–10](specifications/pv-decision-diagnostics.md#approved-pause-context-extension--27-october-2026)
records the four-field pause extension and sparse H provenance. PV-SOC-01–03
are cross-referenced with their existing comparisons and formula semantics intact.
The specifications index and decision history record the operator's approval.
The existing Recorder report gains a dated extension without rewriting historical
benchmark results or release claims.

Normal sensor platform setup adds one `PVTarget` entity. Its unique ID uses the
existing helper:

`<entry_id>:pv:["<station>","<evse>","<connector>","effective_target_soc"]`

The HA entity registry controls the entity ID; the device is the existing
entry/station wallbox. English/German names are `PV Target SoC` / `PV Ziel-SoC`.
The unit is `%`, state class `measurement`, with no battery device class,
Recorder exclusion or forced updates. Existing entity identities are unchanged.

The value is exactly `PV.target`, also exposed by the existing
`PV.attributes()["effective_target_soc"]`. PV_MAXIMUM uses canonical reserve+H;
PV_OPTIMUM uses the existing dynamic/latched target; battery-equipped PV_SURPLUS
uses the existing valid configured target. NETZ and battery-free PV_SURPLUS have
no target. Invalid/missing mandatory formula inputs and profile/mapping
invalidation make the sensor unavailable; valid existing planning restores it.
Actual SoC is not a formula input. Valid planning remains available with release
OFF and without OCPP connection, authority or transaction. No zero fallback or
precision loss is introduced. The existing card consumes the same target.

The canonical target property publishes its one scoped state key only when the
exact value or `None` availability changes. The sensor watches that key. Since
existing State generation resets broadcast all subscribed keys, the entity also
compares its last published target to avoid a redundant write on those broadcasts.
It exposes stable identity attributes only, without station connectivity,
acquisition timestamps, power, budget or planning phase. There is no extra
calculation, acquisition, polling, worker or timer.

Existing `decision_event()` retains its semantic signature and complete full
transition construction. Its unchanged branch adds four fields when the existing
decision mode is STOP or its point has zero amps. Power, SoC, target, surplus
proof and H come from that tick's captured inputs/configuration. Economic STOP
is captured after the existing budget decision, before persistence. The strict
surplus result is reused. Missing evidence is false; battery-free SURPLUS has
null SoC/target/surplus-proof fields and its real economic boolean. No derived
SoC thresholds or H occur in compact samples.

No existing reliable historical H configuration event was found. A sparse
`pv_config` records H only at the first eligible Level-2 decision in a lifecycle
context or a real H change. Startup/reload, profile/release/generation/transaction
changes and disabling/re-enabling diagnostics re-establish context. Inactivity
logs no periodic configuration event; a changed H is recorded at the next
eligible decision. No separate stream is added. The decision-related maximum
remains about 720/hour at five seconds, apart from independent target/input
transitions and sparse configuration context.

## Illustrative serialized records

Positive FAST sample (BALANCE has the same unchanged eleven-key schema with
`mode="BALANCE"`):

```json
{"event":"pv_sample","station":"Wallbox01","evse":"1","connector":"1","profile":"PV_OPTIMUM","mode":"FAST","ev_actual_w":1380.0,"grid_w":0.0,"pv_w":3284.5,"battery_w":1300.0,"household_w":604.5}
```

Unchanged SoC STOP:

```json
{"event":"pv_sample","station":"Wallbox01","evse":"1","connector":"1","profile":"PV_OPTIMUM","mode":"STOP","ev_actual_w":0.0,"grid_w":-3.4,"pv_w":3284.5,"battery_w":2657.2,"household_w":567.9,"soc_pct":52.2,"target_soc_pct":55.8047,"pv_surplus_proven":true,"economic_stopped":false}
```

Unchanged economic STOP while SoC still permits FAST:

```json
{"event":"pv_sample","station":"Wallbox01","evse":"1","connector":"1","profile":"PV_SURPLUS","mode":"FAST","ev_actual_w":0.0,"grid_w":0.0,"pv_w":900.0,"battery_w":0.0,"household_w":900.0,"soc_pct":60.0,"target_soc_pct":50.0,"pv_surplus_proven":false,"economic_stopped":true}
```

Sparse context (identity/profile/mode supplied by the existing emitter):

```json
{"event":"pv_config","station":"Wallbox01","evse":"1","connector":"1","profile":"PV_OPTIMUM","mode":"STOP","soc_hysterese":2.0}
```

These illustrate the schema, not additional hardware evidence. The operator's
27 October PV_OPTIMUM analysis reports expected dynamic T changes, correct SoC
STOP, recovery below the moving restart threshold and an external Fronius inverter
restart. No regulation fault or new control requirement follows from that event.

## Source comparison and unchanged control

Inspected actual [openWB/core source](https://github.com/openWB/core/tree/809ac7425348dc4a0a2e080e742dfcd2f3cfc992)
at revision `809ac7425348dc4a0a2e080e742dfcd2f3cfc992`, obtained read-only:

- `packages/main.py:handler10Sec()` copies module input state and runs the existing
  configured control cadence before processing/publishing results.
- `packages/control/bat_all.py:get_charging_power_left_diff()` uses signed battery
  flow, SoC and its own battery-priority/hysteresis policy. Its policy is not WBM's
  policy and is not imported. It supports observing the existing input context
  at the decision cadence, not adding another measurement or control loop.

Historical WBM `v0.3.2-beta.18`, peeled revision
`87963014c9af73416c438807f523a3da2a98bd22`, was examined in `sensor.py`,
`pv_soc.py` and `pv_input_diagnostics.py`. The latter taps consumed inputs without
polling or double-running regulation. Its old runtime, Level-3 stream, freshness
rules and inclusive legacy SoC boundaries are not restored.

The exhaustive production diff against beta.19 is restricted to `pv.py`,
`sensor.py`, `strings.json`, `translations/en.json`, `translations/de.json`.
An AST comparison removes only added pause-context assignments and diagnostic
call keywords from `tick()`: its remaining control AST equals beta.19 exactly.
The full changed-decision branch is AST-identical. All other unchanged PV methods,
including target mathematics, persistence, dispatch and scheduling, are identical.
Existing sensor/platform AST is unchanged apart from registering the new class;
existing translation content is identical after removing the single added key.

`pv_core.py`, `pv_soc.py`, `pv_plan.py`, `pv_economics.py`, `grid.py`,
`grid_point.py`, `control.py`, `ocpp.py`, `settings.py`, manifest, frontend and all
other production files are byte-identical. Therefore FAST/BALANCE point selection,
PV_OPTIMUM mathematics, strict SoC<T-H STOP, strict SoC>T+H/2 surplus-qualified
BALANCE restart, inclusive SoC>=T+H FAST restart, economic budget/restart,
minimum-point STOP, delays, phase/current restrictions, settings/defaults,
OCPP command selection/dispatch and persistence formats remain unchanged.

## Changed files

Production: the five files listed above. Tests: `test_pv_target_sensor.py`,
`test_pv_pause_diagnostics.py`, `test_pv_power_diagnostics.py` under `tests/`.
Documentation: this report; `docs/specifications/README.md`,
`architecture-responsibility.md`, `decision-history.md`,
`pv-decision-diagnostics.md`, `pv-regulation.md`, `pv-target-soc-sensor.md` under
`docs/specifications/`; `docs/v0.4/recorder-availability-report.md`.

## Validation

The complete suite uses the repository's existing Python 3.14 virtual environment
and local simulated OCPP peers. `WB_SOURCE_ROOT` points to the existing read-only
companion Stationary checkout so its simulated contract test is included. No
Home Assistant installation or physical station is contacted.

| Check | Result |
| --- | --- |
| Complete Python suite with coverage | **858 passed**, no skips, 5 existing dependency warnings, **156.66 s**. |
| Statement coverage | **93.27%** (3,809/4,084); `pv.py` 94.92%, `sensor.py` 98.83%. All new sensor lines are covered. |
| Recorder/volume replays | 300 passive unchanged cycles: zero redundant target writes, unchanged attributes and exact pre-existing planner-call count; 300 active STOP cycles: zero target writes, one full transition then 300 compact samples, one sparse H context, no repeated OCPP requests. |
| Pause coverage | SoC STOP, minimum BALANCE STOP/zero hold, economic FAST and BALANCE STOP, battery-free SURPLUS, moving target/SoC, missing surplus evidence/nulls, persistence-time mutation, sparse/re-established H and logger failure with successful zero-current control. |
| Behavioral regression | Existing exact-boundary SoC, economic, FAST/BALANCE point and timing tests pass. Logging-level/failure traces retain identical regulation outputs. Control AST and protected production file comparisons pass against beta.19. |
| Ruff lint and format | Pass; 166 Python files formatted. |
| JavaScript syntax and frontend unit tests | Pass; **80 tests**. |
| Chrome layout | **1 passed**, real headless Chrome. |
| Translation parity and manifest | Pass; English/German leaf parity; unchanged manifest remains **0.4.0-beta.19**, with existing domain/dependencies. |
| Changed-document local links/anchors | **116 passed**. |
| Whitespace and staged diff | `git diff --check` and staged equivalent pass. |

Commands: `WB_SOURCE_ROOT=/home/elutz/Projekte/wallbox-stationary pytest
--cov=custom_components/wallbox_manager --cov-report=term-missing
--cov-report=json:/tmp/wbm-pv-target-coverage.json -q --tb=short`, `ruff check .`,
`ruff format --check .`, `node --check
custom_components/wallbox_manager/www/wallbox-manager-card.js`,
`node tests/test_wallbox_card.cjs`, `node tests/test_wallbox_card_layout.cjs`.
Additional source/AST, JSON leaf/manifest and Markdown link/anchor assertions
validate the exhaustive comparisons described above. The first isolated full
run passed 856 tests with one optional companion-source skip; the final full run
explicitly includes that source plus economic BALANCE pause coverage and has no
skips. Coverage is statement coverage; no live Recorder database, CPU benchmark
or hardware charging acceptance is claimed.

The pushed commit SHA and its GitHub Actions result/URL are supplied in the final
completion report, avoiding a self-referential commit hash in this file. The
branch is ready for independent review; no PR merge, release/tag, manifest-version
change, deployment, HA reload/restart, live configuration change, Stationary
modification/restart or physical hardware operation occurs in this task.
