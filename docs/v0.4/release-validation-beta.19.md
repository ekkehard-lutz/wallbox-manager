# v0.4.0-beta.19 release validation

## Starting state and preserved feature

Approved feature **29d19eacf4dd24c468ed818338da4cb56ea9a799** is preserved intact.
Exact feature [Actions](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/40341029729)
passed. Fetched refs matched the expected feature, develop
`e4d076e4089139cd179f1893d9f5fa8048df6bc9` and main
`780f64bfb089e22f2699ffe0351cb43e0440506b`. The feature's parent is the beta.18
development commit; that develop tree equals the published beta.18 tree. No
beta.19 tag/release existed. Existing tag objects and release IDs/dates plus
latest stable were recorded for post-publication preservation checks.

Preparation is on `release/v0.4.0-beta.19`. Only manifest version, README,
release notes/report and genuine publication-status documentation corrections
are added after the approved feature. Requirements distinguish historical
sparse-only logging, the later compact exception, unchanged regulation, OPEN FAST
optimization and separate hardware acceptance. The new release authorization does
not rewrite the earlier feature-only approval as historical publication permission.

## Production compatibility against beta.18

Exactly three production files differ:

1. `pv.py`: reviewed compact-cycle diagnostics. Existing full records and signature
   remain; unchanged eligible decisions call the same structured logger for an
   eleven-key sample with the same captured powers. Only the two diagnostic
   methods differ; all other PV AST nodes are identical to beta.18.
2. `www/wallbox-manager-card.js`: reviewed removal of the battery caption element
   and its text assignment; all other frontend source is identical to beta.18.
3. `manifest.json`: version only, `0.4.0-beta.18` to `0.4.0-beta.19`.

The first two files are byte-identical to approved commit 29d19ea. All other
production files, all approved tests, dependencies and HACS metadata remain
unchanged from that commit. No retired v0.3.2 source is restored. FAST/BALANCE,
SoC, PV_OPTIMUM planning, phase/current selection, Wh accounting/economic
STOP/restart, NETZ, authority/release, metering/held observations, seven defaults,
entity identities, source mappings and configuration/journal schemas remain
byte-identical to beta.18. `pyproject.toml` retains tooling version `0.0.0`.

The operator reports `pv_decrease_delay_cycles=2` already configured for a
separate supervised experiment. Its persisted value and all other user settings
are preserved; the default remains 0. There is no migration or live setting
change. Repeated valid STOP samples with release ON are accepted; no additional
suppression is introduced. See [release notes](release-notes-beta.19.md).

## Candidate validation

Tests use
localhost OCPP simulators and real headless Chrome, never physical hardware.
The full suite covers all PV modes/profiles, full/sample exclusivity, five-value
captured snapshots, nulls/signs, level filtering, passive/NETZ silence, bounded
payloads/no secrets, no diagnostic I/O/publication/persistence, logging failure
isolation, unchanged control outputs, 300-cycle sampling and economic/SoC rules.
Card tests cover the caption-only change, upper live label, accessibility,
localization, all profile references, missing values and responsive geometry.

## Executed final-candidate results

| Check | Result |
| --- | --- |
| Complete Python suite with coverage | **828 passed**, none skipped, 5 existing dependency warnings; **149.77 s**. |
| Coverage | **93.19%** (terminal rounded 93%): 4,040 statements, 3,765 covered, 275 missed. |
| Ruff lint / format | Passed; 168 local Python files formatted. |
| JavaScript syntax | Card and both frontend test files passed. |
| Complete frontend unit suite | **80 passed**, no failures/skips. |
| Real headless Chrome layout | **1 passed**, German/English and desktop/narrow widths. |
| Translation parity | All **151 leaf keys** match strings/en/de. |
| Local documentation links/anchors | **110 passed** across changed Markdown and release documents. |
| Manifest/version/HACS | Correct beta.19 prerelease version; all other fields, dependencies and HACS metadata unchanged. |
| Source compatibility | Only the three approved production files differ from beta.18; reviewed runtime/tests preserved byte-for-byte; no restored legacy modules or settings/schema change. |
| Whitespace | Working and staged `git diff --check` passed. |

Coverage differs from the feature run's 93.27% solely because three unchanged
`grid_battery.py:close()` cancellation statements (lines 159, 160, 162) were not
entered during this run's async teardown. No source or tests changed there;
all 828 tests passed. The five warnings remain HA web.Application inheritance
and backoff's deprecated asyncio.iscoroutinefunction usage.

Commands: `.venv/bin/pytest -q --cov=custom_components.wallbox_manager
--cov-report=term-missing --cov-report=json:/tmp/wbm-beta19-coverage.json`,
`.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, JavaScript `node
--check` for the card and both test files, `node tests/test_wallbox_card.cjs`,
`node tests/test_wallbox_card_layout.cjs` and `git diff --check`. Metadata checks
compare immutable beta.18/reviewed feature source and AST, translation leaf keys,
manifest fields and local Markdown file/heading targets. Local process/socket
permissions support only the test peers/browser, not hardware operations.

## Publication gates and final verification

The task separately authorizes release preparation, CI gates, normal history-
preserving merges, annotated tag and GitHub prerelease publication. Wait for
success on the exact release-branch commit, then exact develop and main commits.
Before main push, require `git diff --exit-code develop main` exit 0 and identical
trees to the exact CI-validated develop SHA. Refresh remote refs and reconfirm
identity before tagging. Do not reset, force-push, squash, rebase or overwrite tags.

Annotation: `Wallbox Manager v0.4.0-beta.19 — PV cycle diagnostics and Home Battery card refinement`.
The annotated tag must peel to exact validated main and have the same tree and
manifest `0.4.0-beta.19`. Wait for tag CI before publication. Publish as
`prerelease=true`, `draft=false`, not latest stable, without binary assets.
Inspect the standard GitHub source archive for HACS structure and source identity.
Read back published release, tag object/peel and manifest through GitHub; verify
remote develop/main/tag trees, CI and historical tag/release preservation.
Feature and release branches are retained. Final commit/tree/tag identities and
Actions results are recorded in the release body and completion report to avoid
self-referential hashes in this source tree.

## Engineering evidence and deployment boundary

The [feature report](../pv-cycle-diagnostics-card-cleanup.md) records actual
openWB source revision **809ac7425348dc4a0a2e080e742dfcd2f3cfc992**, inspected
`packages/main.py:handler10Sec()` and
`packages/control/bat_all.py:get_charging_power_left_diff()`, and historical WBM
`v0.3.2-beta.18` diagnostics at peeled commit
`87963014c9af73416c438807f523a3da2a98bd22`. That inspected source and the earlier
[manufacturer/inference comparison](../pv-fast-power-diagnostics.md) remain
engineering references, not imported runtime or new regulation requirements.

Publication does not establish physical FAST/BALANCE response or hardware
acceptance. Installation, HA reload/restart, browser cache intervention, WBM/live
delay edits, authority/release changes, charging commands, Stationary operation
and inverter/battery changes are excluded. The operator installs and measures
separately; future FAST optimization remains open. Unrelated local investigation
files and `pv2.log` are preserved and excluded from the release.
