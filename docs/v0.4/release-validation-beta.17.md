# v0.4.0-beta.17 release-candidate verification

## Scope and ancestry

Prepared on `release/v0.4.0-beta.17` from reviewed implementation
`f6b834672128ae8c68500fc7eb8a1c3bd48d2aef`, after fetching origin branches/tags
and verifying the feature branch matched that SHA. The feature branch is unchanged.
This preparation changes only the integration manifest version, README, release
notes, this verification record and release-relevant implementation documentation.
All Python/JavaScript runtime sources, tests, approved specifications, entity
identities and OCPP dependencies are unchanged from the reviewed starting commit.
The deliberately independent `pyproject.toml` tooling version remains `0.0.0`.

Fetched branch relationships before preparation:

| Reference | SHA / relationship |
| --- | --- |
| `origin/feature/pv-regulation-v3-runtime` | `f6b834672128ae8c68500fc7eb8a1c3bd48d2aef`, exact reviewed start |
| `origin/develop` | `abdcb753dab5063e4d04fa612dc9d245f2f0e1b3`, ancestor of reviewed start; no incoming commits |
| `v0.4.0-beta.16` | annotated tag peels to `abdcb753dab5063e4d04fa612dc9d245f2f0e1b3`, included intact |
| `origin/main` | `09c6148da7a2e033e36ca2280cebe300cb217542`, historical v0.3.2-beta.9 line |

Main has four commits outside candidate ancestry: `09c6148` (beta.9 prerelease
merge), `20ee763` (v0.3.1 merge), `df5919e` (v0.2.0 release), `55b594a` (v0.1.0
release). These are historical release/merge ancestry, not newer v0.4 changes.
The common ancestor is `376a7373c9bb34caaed413280342911470626066`. No main merge,
rebase, cherry-pick, squash, conflict resolution or ref overwrite was performed.
There are 43 feature commits after develop before release preparation, including
approved decision history and all five implementation/correction commits:

- `7285f08` — Add shared PV cycle tuning and storage-charge mapping.
- `e9ed620` — Implement shared PV V3 regulation, economics and sparse diagnostics.
- `d394d6b` — Expose canonical PV targets and document V3 validation.
- `06053d4` — Correct PV V3 target validity, restart durability and zero-ideal bias.
- `f6b8346` — Decouple PV target planning from charging release.

## Compatibility assessment

Source comparison uses the actual published beta.16 tag, not a hypothetical
historical PV runtime. Beta.16 settings/mappings and identity generation were
compared with current `settings.py`, `migration.py`, `pv_migration.py`, `pv.py`,
`pv_economics.py` and setup lifecycle. Beta.16 has no V3 runtime or journal reader.
The prior beta.18 importer is relevant only if genuine older documents remain;
no old economic or day state is fabricated on a normal beta.16 installation.

The [release notes](release-notes-beta.17.md#compatibility-and-migration) enumerate
preserved data, intentionally retired delay keys/owned entities, unchanged NETZ
reserve recovery, release-OFF restart semantics and the deprecated discharge-limit
entity. The existing entity identity generator is byte-identical to beta.16.
V3 preserves recognized old Wh and STOP provenance; invalid identity/history
inhibits recovery. It never resets exhausted economics solely to permit upgrade.

Rollback is not symmetric: beta.16 does not interpret new V3 records and cannot
recover removed delay values. Full pre-upgrade backup and reconciliation of any
post-backup charging, STOP and reserve history are required before re-enabling
charging after rollback. Neither reinstalling beta.16 nor restoring an older backup
alone proves that later exhausted budgets were preserved. No reverse migration or
automatic entity/Recorder reconstruction is promised. See the prepared
[HACS/rollback procedure](release-notes-beta.17.md#hacs-upgrade-and-rollback).

No runtime redesign was made. In particular, independent OFF planning, unsmoothed
BALANCE, all seven parameter defaults, transaction/late-response fencing and the
accepted potential STOP delay from unusually slow journal I/O are unchanged.
Stationary remains the electrical-safety owner; no hardware acceptance is inferred.

The implementation report retains actual openWB revision
`809ac7425348dc4a0a2e080e742dfcd2f3cfc992` and inspected
`packages/control/algorithm/surplus_controlled.py` / `packages/control/bat_all.py`.
The release check reconfirmed that checkout and historical beta.18 local-day behavior
in `pv_optimum.py` at `4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`.
No upstream/historical runtime was restored.

## Candidate validation

Results are recorded below from new runs with manifest **0.4.0-beta.17**.
The full suite includes FAST/BALANCE/1p-6 A behavior, Wh STOP/reset/restart,
phase lockouts and late acknowledgements, reload persistence, OFF-state planning
and no-command tests, NETZ and reserve handling, obsolete registry/settings
migration and sparse diagnostics. Source remains unchanged while running checks.
Localhost OCPP peers and real headless Chrome use the required local socket
permissions; neither connects to the physical station.

| Check | Actual candidate result |
| --- | --- |
| `.venv/bin/pytest -q --cov=custom_components.wallbox_manager --cov-report=term-missing` | **786 passed**, 0 failed, 5 dependency warnings, **136.09 s**; **93%** coverage, 4,027 statements / 279 missed |
| `.venv/bin/ruff check .` | All checks passed |
| `.venv/bin/ruff format --check .` | 161 files already formatted (final recheck) |
| `node --check custom_components/wallbox_manager/www/wallbox-manager-card.js` | Passed |
| `node tests/test_wallbox_card.cjs` | **78 passed**, 0 failed / skipped |
| `node tests/test_wallbox_card_layout.cjs` | **1 passed**, 0 failed / skipped, real headless Chrome |
| `git diff --check` | Passed |
| Manifest/version/HACS metadata | Passed: only manifest version changes; domain, dependencies and HACS config preserved; README and release notes agree |
| Translation JSON/key parity | Passed: all **151 leaf keys** match `strings.json` in each of `en` and `de`; obsolete-key coverage included in Python suite |
| Entity identities / release source scope | Passed: identity generator unchanged from beta.16; Python and JavaScript runtime sources unchanged from `f6b8346` |
| Updated-document local links | Passed |

No Python tests were skipped. The five warnings are existing dependency warnings:
Home Assistant's `web.Application` inheritance and four backoff uses of deprecated
`asyncio.iscoroutinefunction`. They are not runtime controller failures. All checks
were newly run for this candidate; the implementation baseline was not reused as
validation evidence. Metadata assertions also parsed the version as a prerelease,
compared all other manifest fields to the reviewed starting commit and HACS metadata
to beta.16, and checked local documentation links.

## Publication boundary and remote checks

Before preparation, all 36 GitHub release records were inspected through the
connected GitHub API: beta.16 is the latest published prerelease, and no beta.17
release (including a visible draft) exists. Fetched tags likewise contain no
`v0.4.0-beta.17`. The manifest uses `0.4.0-beta.17`; the future tag must include
its `v` prefix. HACS configuration is unchanged from beta.16 and uses source archives.

The final preparation commit is to be pushed to the release branch and its exact
remote SHA verified. Remote Actions results must be checked against that exact SHA;
local success must not be substituted for pending/unavailable CI. The final operator
report records the resulting SHA and remote CI URL/status without a self-referential
commit hash in this file. Eventual merge/publication commit and tag checks remain
separate gates, as does final operator approval.

No merge, tag, release creation/publication, HA/HACS install or restart, Stationary
update/restart, deployment, authority/current/relay/CP operation or live hardware
command is authorized or performed. Pre-existing untracked investigation documents
and `pv2.log` are preserved and excluded. Physical switching/uptake, complete station
bootstrap, source timing/skew and live HA CPU acceptance remain external acceptance
limitations. This candidate does not establish production or hardware acceptance.
