# v0.4.0-beta.13 release validation

## Approved input and merge

The approved feature and remote head both matched
`dacc58f182bb89099bb0fdd03415aa70c0bde9e1`. Develop was exactly the expected
`7fc8caff3d1a4f3ce6c768be9d6f597df06823ee`, its ancestor: zero develop-only and
two feature-only commits. Those are the reviewed nominal-power implementation
and its two-line battery-reserve restart-test synchronization correction.
The approved [feature CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/40117463598)
completed successfully on the exact approved SHA: 449 Python passed, one companion
source-dependent skip, 38 frontend passed, Ruff and formatting passed.

Tracked files were clean. All eight pre-existing untracked files were hash-recorded
and excluded from release preparation/packaging. The explicit non-fast-forward
merge is `e4bfb53a19639a60ce14937e3c18b04d29250a61`, with parents the develop
baseline and approved feature head. The merge has no conflicts and its entire
tree equals the approved feature tree. Develop was pushed and the feature branch
is retained.

## Release-only preparation and namespace

Only manifest version, current README release references, beta.13 release notes
and this validation record change after the reviewed merge. Implementation,
specifications, tests, frontend, translations, services, dependencies and HACS
configuration remain identical to the approved feature.

Before preparation, beta.12 was the newest prerelease and beta.13 had no local or
remote tag or GitHub release. Stable/latest was **v0.3.1**, release ID 399530100.
Publication must use an annotated `v0.4.0-beta.13` tag at the final release commit,
`prerelease=true`, `draft=false` and `make_latest=false`. Stable/latest must remain
unchanged. Exact self-referential commit/tag SHAs and CI/release URLs are verified
and reported after publication rather than embedded into this source commit.

## Validation gates

Candidate checks completed on 24 October 2026:

| Check | Result |
| --- | --- |
| Complete Python suite | **450 passed**, 69.18 s; five existing HA/backoff deprecation warnings |
| Complete frontend/card suite | **38 passed** |
| Full Ruff lint / format check | PASS / PASS |
| JavaScript syntax | PASS |
| Integration and test Python compilation | PASS |
| English/German translation-key parity | PASS |
| Manifest / service metadata / HACS configuration | PASS; version **0.4.0-beta.13**, dependencies and identities unchanged |
| Candidate HACS archive | **38 integration files**, byte-identical to approved source except version-only manifest change; required modules, services, card, translations, branding and notices present |
| Generated/runtime/untracked file exclusion | PASS |
| Git whitespace | PASS |

The local companion source allows the combined actual Stationary Domain/DeviceModel
OCPP test with mocked hardware; GitHub CI without that checkout skips only that
source-dependent test. The reserve restart fix drains HA tasks before checking
the journal, preserving production compare-before-restore behavior.

HACS uses the tracked tag source archive without a separate binary/ZIP asset.
Verify every packaged integration file/hash against the approved feature except
the version-only manifest change; require modules, frontend, translations,
services, branding and notices, and reject generated/runtime/untracked files.
Repeat the check against the committed archive and downloaded GitHub tag archive.

Require successful GitHub Actions on the exact final develop commit before
tagging. Verify tag CI separately before publishing the prerelease, and verify
tag object type, peeled commit, manifest, published release target and remote
develop all agree. Do not mutate the release candidate after validation.

## Unchanged repositories and deployment boundary

WBM main remains `09c6148da7a2e033e36ca2280cebe300cb217542`.
Stationary remains at develop `0f97ebf6f4d83e0b56f33d3f4bb68f9642c92313`,
with main `2baf0daa8c446dad7570e5daa5f0f1cba486e915` and a clean tracked tree.
No Stationary source, release or tag is changed. Historical WBM/openWB comparisons
remain recorded in the approved implementation report; no runtime was reworked
for this release.

No deployment, HACS installation, HA reload/restart, Stationary restart, INI change,
authority/current/phase setting or physical hardware command is part of this task.
Physical acceptance is separate. See release notes for frontend cache, entity
service, saved-draft and rollback considerations.
