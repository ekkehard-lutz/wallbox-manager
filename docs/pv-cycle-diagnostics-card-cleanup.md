# Compact PV cycle diagnostics and home-battery caption refinement

Date: 26 October 2026. Baseline: published `v0.4.0-beta.18`, identical source tree
to verified `origin/develop` **e4d076e4089139cd179f1893d9f5fa8048df6bc9**.
Feature branch: `feature/pv-cycle-diagnostics-card-cleanup`. Scope: implement,
validate, commit and push only; manifest stays **0.4.0-beta.18**.

## Approved requirement changes

The explicit operator task introduces a later, narrow exception to beta.18's
unchanged-decision suppression. Normative changes are in
[PV-DIAG-02/05/06/08/10](specifications/pv-decision-diagnostics.md), with aligned
01/03 descriptions, the [specifications index](specifications/README.md) and a
new dated [decision-history entry](specifications/decision-history.md).
Earlier sparse-only decisions remain historical rather than being rewritten as
though they always authorized compact cycle output.

The operator needs five-power context throughout downward delay and the physical
response of inverter, battery and vehicle, even when the offered point is held.
Each existing eligible active decision emits exactly one of:

- Changed semantic signature: the same complete INFO `pv_decision` as beta.18.
- Unchanged signature: INFO `pv_sample` with exactly identity, profile/mode and
  the five power fields, without reason, thresholds, points or extra metadata.

Independent target/input events may occur in the same cycle. There are no cyclic
samples for release OFF, passive planning, NETZ or unavailable contexts without
a valid decision. All battery PV profiles and eligible battery-free PV_SURPLUS
share the same path. Level 0/1 remains silent for these records; Level 3 reuses
this stream. The five-value captured snapshot, canonical watts/signs, storage
normalization, household exclusion and null semantics are unchanged.

## Minimal implementation and compatibility

`pv.py:decision_event()` retains the existing signature and full-transition
branch and adds an `else` calling the existing logger with `event=pv_sample` and
the same captured powers. `diagnostic()` accepts an optional reason and emits
that key only when supplied, so compact records contain exactly eleven keys.
No new scheduler, listener, reading, OCPP request, persistence, controller state,
entity, publication or worker is introduced. Existing serialization/logging
exception isolation remains. No additional rounding changes full or compact
measurement precision.

For [WBM-HS-UI-02](specifications/lovelace-home-battery-display.md), the card
removes the `battery-soc-label` element and its assignment. The heading is now
directly above the percentage pair, without an empty caption placeholder. All
other battery behavior remains, including German/English one-decimal percentages,
arrow, profile-specific reference, independent missing sides, accessible name and
tooltip. The upper vehicle/charging-state caption stays. Real Chrome geometry
checks cover both languages at 640, 400, 320 and 280 px, including direct sibling
structure, eight-pixel heading gap, desktop value alignment and narrow stacking.

Production diff against beta.18 is restricted to **pv.py** and
**www/wallbox-manager-card.js**. FAST/BALANCE mathematics, SoC transitions, all
seven parameter defaults, import Wh accounting and STOP/restart, technical point
selection, NETZ, authority/release, held source handling, entity identities,
configuration/journal schemas, dependencies and manifest are byte-identical.
Stationary and installed hardware retain physical electrical safety ownership.

## Illustrative synthetic records

These illustrate full versus compact output, not a hardware measurement trace.
The full event retains its causal context and changed operating point:

```text
WBMGR subsystem=pv {"battery_w":-1700,"connector":"1","deadband_w":100,"ev_actual_w":2200,"event":"pv_decision","evse":"1","grid_w":294,"household_w":400,"mode":"FAST","old_point":[1,10],"profile":"PV_MAXIMUM","pv_w":3000,"reason":"grid_import","requested_point":[1,8],"station":"Wallbox01"}
```

An otherwise unchanged eligible decision emits only the compact context:

```text
WBMGR subsystem=pv {"battery_w":-1700,"connector":"1","ev_actual_w":2200,"event":"pv_sample","evse":"1","grid_w":294,"household_w":400,"mode":"FAST","profile":"PV_MAXIMUM","pv_w":3000,"station":"Wallbox01"}
```

## Reference inspection

Rechecked the existing public openWB checkout at revision
**809ac7425348dc4a0a2e080e742dfcd2f3cfc992**:
`packages/main.py:handler10Sec()` copies/updates input data and performs control
on the existing configured cadence; `packages/control/bat_all.py:
get_charging_power_left_diff()` treats storage response and signed power in its
own policy. These are inspected source, not a requirement to copy its algorithm
or diagnostics. The engineering lesson is to observe the controller's input
context at its existing decision cadence without another acquisition/control
loop; this task's exact log scope comes from operator approval.

Historical WBM `v0.3.2-beta.18` (peeled commit
`87963014c9af73416c438807f523a3da2a98bd22`) was re-examined in
`custom_components/wallbox_manager/pv_input_diagnostics.py`: it documents tapping
consumed inputs without polling sensors or running a regulator twice. Its Level-3
subsystem, freshness handling and old runtime are not restored. The preceding
[FAST analysis](pv-fast-power-diagnostics.md) retains manufacturer/source/inference
separation and the open FAST optimization questions; no control proposal is
implemented here. Snapshot values do not prove physically simultaneous sampling.

## Validation

Regression coverage includes
full/sample exclusivity, five consecutive samples, all profile/mode schemas,
changing signs/nulls and captured snapshots across persistence awaits, held delay
reasons, levels 0–3, no I/O/publication/persistence, output equivalence and logging
failure isolation. The 300-cycle test now expects 300 compact samples and no
repeated full transitions; invalid-source/passive-planner silence remains tested.
Frontend tests retain all existing profile/reference/missing-data behavior.

Final local validation on this source:

| Check | Result |
| --- | --- |
| Complete Python suite with coverage | **828 passed**, none skipped, 5 existing dependency warnings; **150.02 s**. |
| Coverage | **93.27%**, 4,040 statements, 3,768 covered, 272 missed. |
| Ruff lint / format check | Passed; 166 local Python files formatted. |
| JavaScript syntax | Passed. |
| Frontend unit suite | **80 passed**, none failed/skipped. |
| Real Chrome layout suite | **1 passed**, both languages and four widths. |
| Translation parity | **151 leaf keys** match in strings/en/de; translations unchanged. |
| Local documentation links/anchors | **74 passed** across changed Markdown and this report. |
| Production compatibility | All files except the two identified runtime files are byte-identical to beta.18; PV AST outside the two diagnostic methods is identical; card source differs only by the caption element/assignment removals. |
| Full-event compatibility | A representative full transition serializes byte-for-byte identically through beta.18 and the feature logger; the repeated decision yields exactly eleven sample keys. |
| Whitespace | Working and staged `git diff --check` passed. |

Commands include `.venv/bin/pytest -q --cov=custom_components.wallbox_manager
--cov-report=term-missing --cov-report=json:/tmp/wbm-cycle-coverage.json`,
`.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`,
`node --check custom_components/wallbox_manager/www/wallbox-manager-card.js`,
`node tests/test_wallbox_card.cjs`, `node tests/test_wallbox_card_layout.cjs`
and `git diff --check`. Metadata checks parse translation keys, compare source
and AST to immutable beta.18, and resolve changed-document targets/anchors.
Existing warnings concern HA web.Application inheritance and backoff's deprecated
asyncio.iscoroutinefunction calls. Exact final-commit CI status/URL is recorded
in the completion report after pushing, rather than by a self-referential commit.

Changed files: `custom_components/wallbox_manager/pv.py`,
`custom_components/wallbox_manager/www/wallbox-manager-card.js`,
`docs/specifications/pv-decision-diagnostics.md`,
`docs/specifications/lovelace-home-battery-display.md`,
`docs/specifications/README.md`, `docs/specifications/decision-history.md`,
`tests/test_pv_power_diagnostics.py`, `tests/test_pv_runtime.py`,
`tests/test_wallbox_card.cjs`, `tests/test_wallbox_card_layout.cjs` and this report.

No independent review, merge, tag, release, installation, deployment, Home
Assistant/Stationary restart, live setting change or hardware command is performed.
Existing untracked investigation files and `pv2.log` remain outside this change.
