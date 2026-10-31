# v0.4.0-beta.21 release validation

## Preflight and approved source

Independent review approved feature
`a024c8ebfa6ae76a9e5d3ac993bdb75baafe8414`, including predecessor
`e61a16d4c4c3fe671a2ccb2086c48c0b76979219`. Fresh branch/tag fetch matched every
operator-specified baseline. The feature has exactly those two commits beyond
initial develop, with no divergence. Both approved CI runs were read back:

- [Final feature/follow-up CI: success](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/41187886025), exact SHA `a024c8ebfa6ae76a9e5d3ac993bdb75baafe8414`.
- [Earlier implementation CI: success](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/41183932738), exact SHA `e61a16d4c4c3fe671a2ccb2086c48c0b76979219`.

| Verified ref | Commit / tree |
| --- | --- |
| Initial develop | `d5cb55b16b1e684e241e12e94326a310f303f0c1` |
| Initial main / peeled beta.20 | `84790e42932aff7de0c4e6fdc55392dcb481bba0` |
| beta.20 annotated tag object | `cab78234f888c3f6229e6ab584b7ac946dbf241f` |
| Initial develop/main/beta.20 source tree | `daeae5026e41109021cbdd2d4240ada022377edb` |
| Approved feature tree | `2bd47c5198805ff3f6c6b08934fed9c2c62c7bfc` |
| Release-branch feature merge | `c5d31f7e71d5d619dfb0d53bedb6b1191bf29604`; tree identical to approved feature |

No beta.21 branch, tag or published release existed. Preflight saved all **41 tag
refs** and complete records of **40 GitHub releases**, including IDs, bodies,
publication/update times and flags. Published beta.20 is release ID `425873399`.
Latest stable is `v0.3.1`, release ID `399530100`; publication must preserve it.
The eight unrelated untracked investigation files/logs remain untouched.

`release/v0.4.0-beta.21` was created from verified origin/develop, followed by a
normal history-preserving non-fast-forward feature merge. No reset, rebase,
squash, force-push or published-history rewrite was used. Release changes are
limited to the explicit six-file version/documentation allowlist below.

## Included functionality and requirements

This release packages the exact reviewed safe explicit Start/Stop/Apply API,
profile/runtime/revision fences, multi-card and stale-click protection, prompt
manager-release publication under PV STOP, transaction-independent PV
pre-authorization, durable PV Start acknowledgement, canonical target
synchronization and read-only NETZ planned-reserve preview.

The approved status layout, battery caption and directional power remain
byte-identical to the final feature. **Power is kW with exactly two locale-aware
decimals**, explicitly accepted by the operator; battery percentages retain one.
Semantic colors and bilingual accessible labels distinguish storage charge/
discharge and grid import/export, with neutral unavailable/ambiguous/zero pairs.
No extra typography or CSS change is made during release preparation.

Verified inclusion of all **35 approved IDs**:

- WBM-ACTION-SAFE-01–09 and WBM-RELEASE-PREP-01–06.
- WBM-HS-UI-SYNC-01–04 and WBM-HS-UI-NETZ-01–06.
- WBM-HS-LAYOUT-01–04 and WBM-HS-POWER-01–06.

The normative specification documents are unchanged from the reviewed feature;
only release-status references are added to the index and non-normative history.
WBM-GRID-15 ownership/write/restoration, WBM-TIME-01–24, WBM-PV-TARGET-01–06,
current strict SoC comparisons and economic STOP/restart specifications remain
unchanged. No new requirement or behavior was authorized for this release.

The two retained reports provide root causes, API/compatibility details, exact
requirement-to-test traceability, Recorder evidence and hardware scenarios:

- [Safe actions, publication and reserve preview](../charge-action-state-reserve-preview-review.md).
- [PV Start acknowledgement and status-power follow-up](../charge-action-status-power-followup-review.md).

## Source invariance and complete diffs

Every tracked runtime, test and other non-release file is checked byte-for-byte
against approved feature `a024c8e`, including `grid.py`, `pv.py`, the served card,
service schema, translations and both implementation reports. Only the manifest
version changes in production; every other manifest field/dependency, HACS
metadata, tooling version, configuration/default and journal schema is preserved.
All tests remain unchanged: no assertion was weakened to pass release validation.

Seven modules are additionally checked against published beta.20 by exact Git
blob identity: `pv_core.py`, `pv_soc.py`, `pv_plan.py`, `pv_economics.py`,
`grid_point.py`, `control.py`, `ocpp.py`. FAST/BALANCE, target formula, hysteresis,
economic import budget, phase/current constraints and Stationary protocol remain
unchanged. The approved interface/publication changes relative to beta.20 are
preserved, not redesigned. No Stationary or Fronius update is part of beta.21.

The feature source audit inspected actual openWB/core revision
`4e6b2224420fc25ab87aa7534201fcf9e250cdfa`,
`packages/main.py:HandlerAlgorithm.handler10Sec()` and
`packages/control/bat_all.py:get_charging_power_left_diff()`, and historical WBM
`v0.3.2-beta.18`, peeled `87963014c9af73416c438807f523a3da2a98bd22`, including
`pv_soc.py`, `pv_optimum.py`, `sensor.py`, `pv_diagnostics.py`. Release preparation
retains this source evidence and imports no external/historical runtime.

The complete feature and release file lists follow below. Exact diffs can also
be reproduced with `git diff v0.4.0-beta.20 a024c8e` and
`git diff a024c8e v0.4.0-beta.21`; the latter contains only release files.

## Exact release-candidate validation

| Check | Executed candidate result |
| --- | --- |
| Complete Python suite | **895 passed, 0 skipped**, five existing dependency warnings; **190.40 s** |
| Statement coverage | **93.51%**: 3,891 / 4,161 statements covered; 270 missing |
| Frontend unit suite | **92 passed**, no failures/skips |
| Real headless Chrome | **1 passed**, covering all **96** language/width/profile/direction combinations |
| Ruff lint / formatting | Both passed |
| JavaScript syntax | Card and both frontend test files passed |
| Translation parity | **158 leaf keys** identical in strings/en/de |
| Documentation | **142 local links/anchors** passed across all Markdown changed from beta.20 and new release documents |
| Manifest/HACS | Exactly version 0.4.0-beta.21; all other manifest fields, dependencies, HACS metadata and tooling unchanged |
| Source/tests | All non-release feature files byte-identical; seven beta.20 core control modules byte-identical; six-file release allowlist passed |
| Requirements | All **35** approved requirement IDs present; preserved specifications unchanged |
| Whitespace | Working and staged diff checks passed |

Coverage differs from the approved feature's 93.58% by exactly three unchanged
statements: `grid_battery.py` lines 172, 173 and 175, cancellation/import/gather in
`GridBattery.close()` when a task is still pending. The previous feature run
covered that timing-dependent teardown branch; this candidate run did not. All
other executed/missing statement sets match the feature coverage JSON exactly.
The full suite passes without a configured coverage-threshold failure, and the
entire file is byte-identical to the approved source. This is recorded as observed
run variation, not hidden by rerunning or modifying tests.

Commands: `WB_SOURCE_ROOT=/home/elutz/Projekte/wallbox-stationary
.venv/bin/pytest -q --cov=custom_components.wallbox_manager --cov-report=term-missing
--cov-report=json:/tmp/wbm-beta21-coverage.json`, `.venv/bin/ruff check .`,
`.venv/bin/ruff format --check .`, `node --check` for the card and both frontend
test files, `node tests/test_wallbox_card.cjs`,
`node tests/test_wallbox_card_layout.cjs`, release allowlist/blob comparisons,
manifest/translation/requirement/document-link assertions and `git diff --check`.
The five warnings are existing HA/backoff dependency deprecations. Hosted CI
normally reports 894 passed plus one expected optional companion-source skip;
local validation explicitly supplied that source and passed all 895 tests.

The suite uses Python 3.14, a local HA harness and simulated localhost OCPP peers.
`WB_SOURCE_ROOT=/home/elutz/Projekte/wallbox-stationary` supplies read-only
companion source for the optional contract test; no physical device is contacted.
Real headless Chrome covers 96 language/width/profile/direction combinations.
Existing local HA reload tests run only inside this harness.

Recorder regressions include zero target/Select/action writes over 300 unchanged
active SoC STOP cycles, zero reserve-preview writes over 300 unchanged repetitions,
and zero Select writes over 300 rounds of four changed source powers. One mapping
change produces one mapping publication. No source power sensor, polling,
additional regulator pass or periodic diagnostic stream was added. Dedicated target
sensor identity and change-only notification behavior are unchanged.

## Publication gates and final identities

Each next step requires success for the exact preceding commit SHA:

1. Commit/push the release candidate; await successful release-branch CI.
2. Refresh and verify original remote refs, merge candidate into develop with
   `--no-ff`, verify tree equality, push and await successful develop CI.
3. Refresh/verify refs, merge develop into main with `--no-ff`, verify candidate/
   develop/main tree equality, push and await successful main CI.
4. Recheck new-tag absence; create an annotated beta.21 tag on the validated main
   commit, verify tag object/peel/tree/version, push and await tag push CI.
5. Publish **Wallbox Manager v0.4.0-beta.21** with `prerelease=true`, `draft=false`,
   not latest stable, standard source archives and no binary upload.
6. Independently read back the release, tag and branch refs; verify identical
   trees, source archive/HACS structure, manifest, service/card/docs and exact
   preservation of all historical tags/releases and latest stable.

Final candidate/develop/main/tag object/peel/tree identities and exact-SHA CI URLs
are recorded in the published release body and completion report. This avoids
self-referential commit/tree hashes in the candidate documentation. Feature and
release branches remain for traceability. No earlier tag or release is replaced.
If a gate fails, publication stops before the next stage.

## Compatibility, limitations and hardware boundary

`button.press` remains a legacy state-dependent toggle; direction-safe callers
must use `wallbox_manager.charge_action` with current context. A successful PV
Start acknowledges durable manager release, not physical charging. Missing power
sources show neutral display; unavailable ownership evidence shows unavailable
reserve preview. Planned reserve is never a confirmed Fronius write.

Existing release resets OFF after integration reload/restart; installation does
not silently resume prior charging authorization. The operator must explicitly
Start again if desired during a separately managed installation. Frontend resource
cache handling and actual source delivery/timing require operator acceptance.
See [upgrade/rollback guidance](release-notes-beta.21.md#hacs-upgrade-and-rollback).

Remaining separately authorized hardware acceptance includes Start under persistent
SoC STOP after an older electrical failure; unplugged pre-authorization and later
connection; multiple browser/stale-action scenarios; moving target during STOP/
release OFF; planned versus observed reserve before/during confirmed NETZ charging,
restoration/external edits/reload recovery; and directional power in the site's
HA theme and actual source transition timing. Automated success is not physical
acceptance.

This task performs GitHub publication only: no HACS installation, deployed file
copy, live HA integration reload/restart, live setting/profile/release/authority
change, hardware OCPP command, Stationary update/restart or Fronius/inverter/battery
operation. The operator installs and tests hardware separately.

## Complete approved feature diff versus beta.20

| Status | Path |
| --- | --- |
| M | `custom_components/wallbox_manager/battery.py` |
| M | `custom_components/wallbox_manager/button.py` |
| M | `custom_components/wallbox_manager/grid.py` |
| M | `custom_components/wallbox_manager/grid_battery.py` |
| M | `custom_components/wallbox_manager/pv.py` |
| M | `custom_components/wallbox_manager/select.py` |
| M | `custom_components/wallbox_manager/services.yaml` |
| M | `custom_components/wallbox_manager/strings.json` |
| M | `custom_components/wallbox_manager/translations/de.json` |
| M | `custom_components/wallbox_manager/translations/en.json` |
| M | `custom_components/wallbox_manager/www/wallbox-manager-card.js` |
| A | `docs/charge-action-state-reserve-preview-review.md` |
| A | `docs/charge-action-status-power-followup-review.md` |
| M | `docs/specifications/README.md` |
| M | `docs/specifications/charge-release.md` |
| M | `docs/specifications/grid-profile.md` |
| M | `docs/specifications/lovelace-home-battery-display.md` |
| M | `docs/specifications/operational-display-and-timing.md` |
| M | `docs/specifications/pv-target-soc-sensor.md` |
| M | `tests/test_home_battery_display.py` |
| A | `tests/test_pv_start_status_followup.py` |
| A | `tests/test_safe_charge_actions.py` |
| M | `tests/test_wallbox_card.cjs` |
| M | `tests/test_wallbox_card_layout.cjs` |

## Release-specific diff allowlist versus approved feature

- `README.md`
- `custom_components/wallbox_manager/manifest.json`
- `docs/specifications/README.md`
- `docs/specifications/decision-history.md`
- `docs/v0.4/release-notes-beta.21.md`
- `docs/v0.4/release-validation-beta.21.md`
