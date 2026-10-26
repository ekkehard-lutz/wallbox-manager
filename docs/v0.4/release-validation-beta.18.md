# v0.4.0-beta.18 release validation

## Scope and ancestry

Reviewed diagnostics implementation **f09310d62a2310664331f5b852e99a79747e1f51**
is preserved intact. Feature documentation completion:
**3f6e1a06f2956208a69d0d64d76c5aac5b9ef9cc**. Preparation branch:
`release/v0.4.0-beta.18`. Fetched starting refs matched the operator's expected
feature, develop `04cf93d6e980fe17cf1905926f576328ce1ae33f` and main
`e37ac017ba61b094a75b398d7303142aea827066`. Beta.18 had no existing tag/release.
Historical tags/release identities were recorded before publication for final
comparison. Unrelated local investigation files and `pv2.log` are excluded.

Documentation now distinguishes approved requirements, beta.17 implementation,
the beta.18 five-value expansion, outstanding hardware acceptance and OPEN future
FAST optimization. The overall V3 draft status does not imply an absent runtime.
The seven beta.17 parameters and defaults remain unchanged. See
[release notes](release-notes-beta.18.md) and
[approved diagnostic contract](../specifications/pv-decision-diagnostics.md).

## Production source diff against v0.4.0-beta.17

Exactly two production files differ:

- `custom_components/wallbox_manager/pv.py`: the reviewed pure `power_context`
  projection and propagation of captured powers to existing target, input-condition
  and decision logging. Keyword-only context arguments preserve snapshot causality
  across awaits; dictionary merging ensures one key occurrence. Outside scheduled
  ticks, existing held observations provide event-time context. Control decisions,
  event signatures, polling, persistence and publication paths are unchanged.
- `custom_components/wallbox_manager/manifest.json`: version only,
  `0.4.0-beta.17` to `0.4.0-beta.18`.

All other production files are byte-identical to beta.17, including `pv_core.py`,
`pv_soc.py`, `pv_economics.py`, `pv_plan.py`, `grid_point.py`, `grid.py`, authority,
charge-release, identity generation, settings/defaults, migrations, translations
and frontend JavaScript. No retired v0.3.2 module is restored. `pv.py` is identical
to reviewed commit f09310d. HACS metadata/dependencies remain unchanged, and
`pyproject.toml` deliberately retains its independent tooling version `0.0.0`.
There is no data/schema migration.

## Final-source local validation

Tests use localhost OCPP
peers and headless Chrome, never the physical station. Focused coverage includes
five fields across all battery PV profiles and target/STOP/restart/mode events,
signs, nulls, unique keys, bounded payloads, captured snapshots across persistence
awaits, diagnostic I/O prohibition, FAST/BALANCE replay equivalence and the
existing >=300 unchanged-tick silence test. Existing economics, SoC, NETZ,
identity/migration and late-response suites are included.

## Executed local results

| Check | Final beta.18 source result |
| --- | --- |
| Full Python suite with coverage | **800 passed**, none skipped, 5 existing dependency deprecation warnings; **142.24 s**. |
| Coverage | **93.11%** (terminal rounded 93%): 4,037 statements, 3,759 covered, 278 missed. |
| Ruff lint / format | Passed; 165 local Python files already formatted. |
| JavaScript syntax | Passed for `wallbox-manager-card.js`. |
| Frontend unit tests | **78 passed**, no failures/skips. |
| Real headless Chrome layout | **1 passed**, no failures/skips. |
| Translation parity | All **151 leaf keys** identical in strings/en/de. |
| Manifest/version/HACS | Beta.18 parses as a prerelease; all other manifest fields, dependencies, HACS metadata and tooling version unchanged. |
| Entity identities | Every production identity/settings/migration/entity module identical to beta.17; identity compatibility tests included in full suite. |
| Source scope | Only reviewed `pv.py` diagnostics and manifest version differ in production; no added retired modules. |
| Documentation | **97** local links and heading anchors passed across changed Markdown documents, including release notes/report. Two stale historical decision-history anchors repaired. |
| Whitespace | Working and staged `git diff --check` passed. |

Commands: `.venv/bin/pytest -q --cov=custom_components.wallbox_manager
--cov-report=term-missing --cov-report=json:/tmp/wbm-beta18-coverage.json`,
`.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`,
`node --check custom_components/wallbox_manager/www/wallbox-manager-card.js`,
`node tests/test_wallbox_card.cjs`, `node tests/test_wallbox_card_layout.cjs`
and `git diff --check`. Metadata checks parse all translation leaf keys, compare
manifest/source/HACS files to immutable tags and reviewed commit, and resolve
local Markdown targets and heading anchors. Browser/OCPP execution uses the local
process/socket permissions needed by the test peers, not hardware access.
The warnings remain HA web.Application inheritance and backoff's deprecated
asyncio.iscoroutinefunction usage. No validation failure is left unresolved.

## Remote publication gates

The task explicitly authorizes preparation, normal non-squash merges, annotated
tag and GitHub prerelease publication in one workflow. Each stage remains a
separate gate: exact remote release branch SHA; successful Actions on exact
merged develop; main tree identical to that validated develop commit; successful
Actions on exact main; annotated tag peeling to exact main; tagged manifest;
tag Actions; independently read-back release classification and source identity.
No pending/failed CI authorizes proceeding.

The final operator report and GitHub release body record resulting commit/tree/tag
identities and Actions URLs, avoiding self-referential hashes in this source tree.
The tag annotation is `Wallbox Manager v0.4.0-beta.18 — Five-value PV power diagnostics`.
HACS uses the standard source archive; no binary asset is added. Release must be
published (`draft=false`), prerelease (`prerelease=true`), not latest stable.

## Acceptance and deployment boundary

Automated success does not establish real inverter/battery settling, available
discharge capability, asynchronous-source alignment, EV response, electrical
safety, complete station bootstrap or live HA CPU acceptance. The existing
[FAST analysis](../pv-fast-power-diagnostics.md) preserves the inspected openWB
revision `809ac7425348dc4a0a2e080e742dfcd2f3cfc992`, manufacturer documentation and
inference distinctions; historical v0.3.2-beta.18 behavior was examined there,
not restored. Future FAST changes require the operator's five-value hardware log
and separate approval. No HACS installation, HA restart/reload, deployment,
Stationary/inverter/battery setting change, authority/release change, live PV
setting change or wallbox command occurs in this publication workflow.
