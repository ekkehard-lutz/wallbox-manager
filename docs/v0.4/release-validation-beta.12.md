# v0.4.0-beta.12 release validation

## Approved inputs, ancestry and merges

Live feature heads matched the independently approved commits exactly:

- WBM: `75f1c37a00fc5a615eb28c479e70e6ad4c88fd35`, including release-OFF recovery.
- Stationary: `8015fd3f4a4c066d903b647a243349f56ae27c0f`.

Remote WBM develop `6451a0167fc2ae35dff54a10c1a718b052d12289` was an ancestor
of its feature (0 develop-only / 4 feature-only commits). Remote Stationary develop
`99ec6a7550049774e40c3f0b00d13c768cee6ca2` was an ancestor of its feature
(0 develop-only / 2 feature-only commits). The stale local Stationary develop was
fast-forwarded before merging. No divergence, conflict or additional unreviewed
code was included. Tracked working trees were clean; WBM's eight known untracked
files were hash-recorded and preserved.

Both explicit `--no-ff` merges were pushed:

- WBM merge: `2721190ae84003dc90a8e4979a4c264f4508ed57`, with parents
  original WBM develop and the approved WBM feature head.
- Stationary merge / **exact companion revision to deploy**:
  `0f97ebf6f4d83e0b56f33d3f4bb68f9642c92313`, with parents original remote
  Stationary develop and the approved Stationary feature head.

Both merge trees are byte-identical to their respective approved feature trees.
Stationary receives no further source changes, version, release or tag.

Approved WBM feature [push CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/40107248385)
completed successfully on exactly `75f1c37a00fc5a615eb28c479e70e6ad4c88fd35`.
Before either merge, the actual paired Domain/DeviceModel contract and release-OFF
recovery suite passed **20 tests** against the two approved source trees.

## Release-only preparation

Only these files change after the reviewed WBM merge:

- `custom_components/wallbox_manager/manifest.json`: version beta.11 → beta.12 only.
- `README.md`: current release version/link.
- `docs/v0.4/release-notes-beta.12.md`.
- This validation record.

All other integration sources, frontend, translations, tests, HACS configuration,
dependencies and storage/config-entry versions remain identical to the approved
WBM feature. Namespace preflight confirmed beta.11 was the newest prerelease and
no beta.12 remote tag or GitHub release existed. Existing stable latest is v0.3.1;
beta.12 must be `prerelease=true`, `draft=false`, `make_latest=false`.

## Maintained verification

| Check | Result |
| --- | --- |
| WBM full Python suite | **390 passed**, 55.59 seconds; five existing dependency deprecation warnings |
| WBM frontend/card regressions | **27 passed** |
| Ruff lint / formatting / JavaScript syntax | PASS / PASS / PASS |
| WBM Python compilation | PASS, integration and tests |
| Manifest / translations | **0.4.0-beta.12**; otherwise unchanged manifest, English/German key parity and migration surfaces PASS |
| HACS candidate packaging | **37 tracked integration files** verified against approved source; required modules/card/branding/notices/translations present; no generated/runtime files |
| Stationary full maintained Python suite | **817 passed + 607 subtests**, 64.30 seconds |
| Stationary production-source compilation | PASS |
| Stationary Ruff lint | **141 findings**, identical count and no new normalized findings against established baseline |
| Stationary Ruff formatting | **77 files require formatting**, same established baseline; no unrelated cleanup |
| Merged-source comparison | Both merge trees exactly equal approved feature trees; WBM release preparation differs only in the four listed files |
| Git whitespace | PASS in both repositories |
| Original user files | Eight pre-existing untracked files retained, SHA-256 hashes unchanged |

Stationary's full Ruff/formatting checks remain nonzero because of those inherited
findings; they are not reported as globally passing. Full regression includes the
actual paired Stationary Domain/DeviceModel OCPP compatibility and all 19 release-OFF
recovery/fencing cases. No physical wallbox was contacted.

Exact final commit/tag objects, committed/remote archive comparison and CI/release
URLs are reported after publication, since they cannot be embedded into their
own source commit.

Stationary baseline policy: run the complete maintained Python regression suite
and production-source compilation; compare Ruff findings to the established
baseline without unrelated cleanup. The established baseline is 141 lint findings
and 77 files requiring formatting. Checkout-basename-dependent N999 messages must
be normalized when comparing an archived baseline to the real repository.

HACS uses the annotated tag's tracked source archive, without a separate release
ZIP. Validate every tracked integration file/hash, manifest version, required
modules/card/translations/branding/notices and absence of runtime/generated files.
Repeat comparison against the exact committed source archive and the downloaded
GitHub tag archive. Untracked workstation files are excluded.

## Publication gates and hard deployment boundary

Require successful GitHub CI on the exact final develop release commit before
creating/pushing the annotated `v0.4.0-beta.12` tag. Verify the annotated tag object
and peeled commit against the beta.12 manifest. Check applicable tag CI and publish
only a GitHub prerelease with stable/latest selection unchanged. Retain both feature
branches. Stop after publication; physical installation is manual and separate.

Original main references, required unchanged after publication:

- WBM: `09c6148da7a2e033e36ca2280cebe300cb217542`.
- Stationary: `2baf0daa8c446dad7570e5daa5f0f1cba486e915`.

No merge into main, Stationary tag/release, `/etc/wallbox/wallbox.ini` edit,
wallbox1 deployment, HACS installation, Home Assistant reload/restart, production
service operation or physical hardware command is performed. Local simulated OCPP
sockets and mock hardware are test infrastructure only. See release notes for
paired migration, default-OFF behavior, accepted limitations and rollback warnings.
