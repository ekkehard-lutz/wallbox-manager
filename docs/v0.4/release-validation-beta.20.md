# v0.4.0-beta.20 release validation

## Verified starting state and preserved feature

Approved feature branch `feature/pv-target-sensor-pause-diagnostics` points exactly
to **f4df7ac34a7f87acc170ee008362a43c067e2e65**. Its
[feature CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/40555902996)
succeeded on that exact SHA. Fresh remote refs matched the reviewed baseline:

| Ref | Commit / tree |
| --- | --- |
| Initial develop | `5c1187d1ced7cdc36e323fd726c7beff1cdbf08d` |
| Initial main / peeled beta.19 | `3f07b17b478bcda6e6f9171a4eee2feb13725fcd` |
| beta.19 annotated tag object | `ae10679af472fdfea38a2044159a385d250b0410` |
| Initial develop/main/beta.19 tree | `0b782648f7b60c2d35ad9398c49d4d2f1ac191dd` |

The approved feature is one commit ahead of initial develop, without divergence.
Beta.19 exists as a published prerelease. No beta.20 tag, release or release
branch existed. Preflight recorded all 40 historical annotated/lightweight tag
refs and 39 GitHub releases, including release IDs, tags, publication/update times
and bodies. Latest stable remains `v0.3.1`. Unrelated untracked investigation files
and `pv2.log` remain untouched in the original main checkout.

`release/v0.4.0-beta.20` starts from verified origin/develop and merges the feature
with a normal non-fast-forward merge. There is no reset, rebase, squash,
force-push or published-history rewrite. The approved implementation and tests
are preserved byte-for-byte. Only manifest version, current README, these release
notes/report and genuine publication-status entries in the specification index
and decision history are added after the feature merge. Approved normative
requirements, existing implementation reports and historical release notes remain
unchanged.

## Production compatibility

Exactly six production files differ from published beta.19:

1. `pv.py`: the approved canonical-target notification and captured pause/sparse
   configuration diagnostics, unchanged from feature f4df7ac.
2. `sensor.py`: approved normal target sensor registration/projection, unchanged
   from feature f4df7ac.
3. `strings.json`: approved sensor name, unchanged from feature f4df7ac.
4. `translations/en.json`: approved English sensor name, unchanged from feature.
5. `translations/de.json`: approved German sensor name, unchanged from feature.
6. `manifest.json`: only version `0.4.0-beta.19` → `0.4.0-beta.20`.

Every other production file is byte-identical to beta.19. In particular,
`pv_core.py`, `pv_soc.py`, `pv_plan.py`, `pv_economics.py`, `grid.py`,
`grid_point.py`, `settings.py`, `control.py`, `ocpp.py` and the existing frontend
JavaScript are unchanged. Dependencies, HACS metadata, config schema, tooling
version `0.0.0`, existing HA entity identities, settings/defaults and journal
formats are unchanged. All tests match the reviewed feature exactly.

The [feature source audit](../pv-target-sensor-pause-diagnostics.md#source-comparison-and-unchanged-control)
records control-tick AST equality after excluding diagnostic-only capture/calls,
and an identical full-decision branch. Beta.20 preserves that approved runtime.
FAST/BALANCE selection/mathematics and delays, PV_OPTIMUM target calculation,
strict SoC STOP and strict/inclusive restart comparisons, economic import budget,
phase/current restrictions, authority, release and OCPP dispatch remain unchanged.
The new sensor has no charging-control role. No extra polling, timer, acquisition,
configuration/persistence migration or Stationary/Fronius change is introduced.

The original source inspection is retained: actual openWB/core revision
`809ac7425348dc4a0a2e080e742dfcd2f3cfc992`,
`packages/main.py:handler10Sec()` and
`packages/control/bat_all.py:get_charging_power_left_diff()`; historical WBM
`v0.3.2-beta.18`, peeled `87963014c9af73416c438807f523a3da2a98bd22`,
`sensor.py`, `pv_soc.py`, `pv_input_diagnostics.py`. This release imports no legacy
or openWB runtime and changes no inspected control policy.

## Exact candidate validation

The complete suite uses Python 3.14 and local HA/OCPP simulated peers.
`WB_SOURCE_ROOT=/home/elutz/Projekte/wallbox-stationary` provides the read-only
companion source for the optional simulated contract test. Real headless Chrome
checks card layout. No physical device or installed HA instance is contacted.

Target tests cover normal registration, scoped unique ID/device, translations,
percent/measurement metadata, every profile's validity, passive planning with
release OFF, exact dynamic target changes, configuration/mapping invalidation,
recovery and reload without duplicates. The 300-cycle replay proves no redundant
HA/Recorder-significant writes or attributes and no additional reads/calculations,
timers or OCPP activity.

Pause tests cover full-transition/compact exclusivity, all four pause fields and
five original powers, sparse H, excluded derived thresholds, moving SoC/target,
captured snapshot mutation across persistence, missing/null evidence, SoC STOP,
minimum BALANCE STOP, zero holds, economic FAST/BALANCE STOP, battery-free
SURPLUS, positive-point schema preservation, inactive/level filtering, 300-cycle
volume bounds and control continuation despite logger failure. Existing exact
SoC, economic, point-selection, delay, authority/release and journal regressions
remain part of the full suite.

### Executed results

| Check | Final candidate result |
| --- | --- |
| Complete Python suite with coverage | **858 passed**, no skips, 5 existing dependency warnings; **155.92 s**. |
| Statement coverage | **93.27%**: 3,809 / 4,084 statements covered, 275 missing; matches the approved feature baseline. |
| Ruff lint / formatting | Passed; 168 local Python files formatted. |
| JavaScript syntax | Card and both frontend test files passed. |
| Complete frontend unit suite | **80 passed**, no failures/skips. |
| Real headless Chrome layout | **1 passed**, both languages and desktop/narrow geometry. |
| Translation parity | All **152 leaf keys** match strings/en/de. |
| Local documentation links/anchors | **156 passed** across all documents changed from beta.19, including README and new release documents. |
| Manifest / HACS / tooling | Version exactly 0.4.0-beta.20; every other manifest field, dependencies, HACS metadata and tooling version unchanged. |
| Source / tests | Exactly the six-file production allowlist; feature runtime/tests preserved byte-for-byte; only six release-specific files differ from the approved feature. |
| Whitespace | Working and staged `git diff --check` passed. |

The five warnings are the existing HA web.Application inheritance and backoff
asyncio.iscoroutinefunction deprecations. No new skip, failure or coverage
regression occurred. The original feature CI reported 857 passed and one expected
optional companion-source skip. Hosted CI does not contain that adjacent checkout;
local candidate validation explicitly provides it and passes all 858 tests.
Release/develop/main/tag CI must succeed on their exact pushed SHA before the
next gate. Final Actions URLs and observed results appear in the published
release body and final completion report.

Commands: `WB_SOURCE_ROOT=/home/elutz/Projekte/wallbox-stationary
/home/elutz/Projekte/wallbox-manager/.venv/bin/pytest -q
--cov=custom_components/wallbox_manager --cov-report=term-missing
--cov-report=json:/tmp/wbm-beta20-coverage.json`, `ruff check .`,
`ruff format --check .`, `node --check` for the card and both frontend tests,
`node tests/test_wallbox_card.cjs`, `node tests/test_wallbox_card_layout.cjs`,
local Markdown link/anchor, translation-leaf and manifest assertions, exhaustive
Git source comparison against feature/beta.19, and `git diff --check`.

## Publication gates and identities

The release task explicitly authorizes publication after each successful gate:

1. Commit/push the release candidate and require successful CI for its exact SHA.
2. Refresh refs and ensure no unexpected remote change, then merge the candidate
   into develop with a normal non-fast-forward merge. Require identical candidate
   and develop trees, push, and await successful exact-SHA develop CI.
3. Refresh refs, merge develop into main non-fast-forward, and require main,
   develop and candidate file-tree equality and manifest 0.4.0-beta.20. Push main
   and await its exact-SHA CI.
4. Recheck absence of the new tag; create an annotated `v0.4.0-beta.20` pointing to
   validated main, verify object/peel/tree/version, push and await tag push CI.
5. Publish **Wallbox Manager v0.4.0-beta.20** as `prerelease=true`, `draft=false`,
   not latest stable, using standard source archives without binary uploads.
6. Read back release/tag through GitHub; verify main/develop/tag trees, manifest,
   source archive/HACS structure, sensor/tests/docs and historical preservation.

Final candidate/develop/main/tag object/peel/tree identities and all exact-SHA
Actions URLs are recorded in the published release body and final completion
report, avoiding self-referential commit/tree hashes in this candidate file.
Feature and release branches remain for traceability. No existing tag/release
may be replaced or rewritten. The final source tree is the CI-validated release
candidate tree; later merge commits change history only.

## Hardware and deployment boundary

This is GitHub publication only. No HA/HACS install, integration reload, HA
restart, live configuration change, manager-release/authority change, OCPP
charging command, Stationary restart/update, Fronius modification or hardware
test occurs. Existing manager charging release still resets OFF after integration
reload/restart; the operator must authorize charging again if required during a
separately managed installation. Stationary owns physical/electrical safety.

The operator's 27 October PV_OPTIMUM analysis motivates observability; expected
moving T, correct SoC STOP, recovery below the moving restart threshold and an
external Fronius inverter restart do not authorize new regulation behavior.
Automated success is not hardware acceptance. See
[upgrade/rollback and operator boundary](release-notes-beta.20.md#hacs-upgrade-and-rollback).
