# v0.4.0-beta.14 release validation

## Approved input and merge

The approved fix head and remote head both matched
`7836f0707f3f7c6863fcfbdcac67229e6451f78e`. Develop was exactly
`620de00b5d0e7c13b60403a16638e70555b5bd83`, its ancestor: zero develop-only
and one fix-only commit. The reviewed correction changes only Settings provenance,
the deterministic initialization regression matrix, the original capability test
and the correction report. Its exact-commit
[CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/40120918450)
passed 463 Python tests with one companion-source skip, 38 frontend tests,
Ruff and formatting. No unreviewed source changes were included.

Tracked trees were clean before preparation. All eight pre-existing untracked
files were hash-recorded and excluded. Merge
`3aa56308b5cd753415af4ead2c507eeeae8b468d` was an explicit `--no-ff` merge
with parents the develop baseline and approved fix head; no conflicts occurred.
Its entire tree equals the approved fix tree. Develop was pushed; both
`feature/grid-nominal-power-steps` and `fix/grid-initialization-approval-race`
are retained.

## Release-only preparation and immutable history

Only the manifest version, current README release references, beta.14 release
notes and this validation record change after the reviewed merge. Implementation,
tests, specifications, frontend, translations, services, dependencies, integration
entity identities and HACS configuration remain unchanged from the approved fix.

Beta.14 had no local/remote tag or GitHub release before preparation. Published
beta.12 remains the previous prerelease. Beta.13 was an unpublished failed tag
candidate and is preserved exactly: annotated object
`230ca2bb9db4e9b4521467588265f7c37e9e4c13`, peeled commit
`620de00b5d0e7c13b60403a16638e70555b5bd83`. No overwrite, deletion or rerun
of that tag is part of this release.

Stable/latest is **v0.3.1**, release ID 399530100. Beta.14 must be
`prerelease=true`, `draft=false`, `make_latest=false`; stable/latest must remain
unchanged. The annotated beta.14 tag must point to the exact final develop release
commit. Self-referential commit/tag SHAs, final CI URLs and publication metadata
are reported after verification rather than embedded into their own source commit.

## Validation gates

Candidate checks completed on 24 October 2026:

| Check | Result |
| --- | --- |
| Complete Python suite | **464 passed**, 74.86 s; five existing HA/backoff deprecation warnings |
| Complete frontend suite | **38 passed** |
| Full Ruff lint / formatting | PASS / PASS |
| JavaScript syntax / Python compilation | PASS / PASS |
| English/German translation-key parity | PASS |
| Manifest / services / HACS metadata | PASS; version **0.4.0-beta.14**, identities/dependencies unchanged |
| Candidate integration package | **38 files**; byte-identical to reviewed fix except manifest version |
| Required modules, card, services, translations, branding and notices | PASS |
| Runtime/generated/untracked exclusion and original file hashes | PASS |
| Git whitespace | PASS |

The local full suite includes
the actual companion Stationary Domain/DeviceModel with mocked hardware; GitHub
without that checkout skips only the corresponding source-dependent test.

HACS consumes the tracked tag archive, with no separate release ZIP asset.
Require every integration file to match the reviewed fix except the version-only
manifest change. Verify required modules, services, frontend, translations,
branding/notices and absence of generated/runtime/untracked files. Compare the
committed archive with the downloaded GitHub tag archive byte-for-byte by file.

Both exact release-commit CI and annotated-tag CI must conclude success before
publication. If tag CI fails, retain the tag unchanged and do not publish or
conceal the failed candidate. After both pass, verify remote develop, annotated
tag object, peeled target, tagged manifest, published release and source archive.

## Deployment boundary

WBM main remains `09c6148da7a2e033e36ca2280cebe300cb217542`.
Stationary remains at develop `0f97ebf6f4d83e0b56f33d3f4bb68f9642c92313`,
with main `2baf0daa8c446dad7570e5daa5f0f1cba486e915` and an unchanged checkout.
No Stationary source, tag or release changes are included. Historical WBM/openWB
source comparisons remain recorded in the reviewed implementation/correction
reports; release preparation adds no runtime logic.

No Home Assistant installation/reload/restart, Stationary restart, runtime/INI
configuration change, authority/current/phase operation or physical hardware
command is part of this task. Physical acceptance and HACS installation remain
separate. See release notes for saved NETZ draft/approval and frontend cache
considerations.
