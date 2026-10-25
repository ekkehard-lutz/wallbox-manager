# v0.4.0-beta.11 release validation

## Integration inputs and scope

Fetched `origin --prune` on `pc-ekke-01` and checked both remote WBM heads before
any merge. They matched the approved inputs exactly:

- Original develop: `063ef61a6d8407fd80913c0bca31b83b3ef1bfab`.
- Approved feature: `24767915eaa0b7e6d6dc287ec74d71727b5e4333` on
  `fix/grid-zero-approximation-lockout`.
- Matching remote Stationary develop:
  `ce823cf52faa511475ef87a776c7a0dce28e1184`.

The older local WBM develop was fast-forwarded to the verified remote develop.
It was an ancestor of the approved feature, with no develop-only changes.
Non-fast-forward merge `9ad6144364fb290916508013708fe87f9e3f079b` has exactly the
original develop and approved feature as its parents. Its tree is identical to
the approved feature tree; the merge was pushed and verified remotely. Complete
specification, implementation, test and independent correction history is retained.

The separate release-preparation commit changes only:

- `custom_components/wallbox_manager/manifest.json`: beta.10 → beta.11.
- `README.md`: current release link/version and SET overview.
- `docs/v0.4/release-notes-beta.11.md`: release notes and acceptance boundary.
- This validation record.

Every other integration file and every test is identical to the approved feature.
No new executable behavior, dependency, storage/config-entry version, Stationary
change or unrelated merge resolution is included in release preparation.

## Maintained local validation

| Check | Result |
| --- | --- |
| Complete Python suite | **363 passed**, 44.43 s; five existing dependency deprecation warnings |
| Frontend/card regressions | **27 passed** |
| Ruff lint and formatting | Passed |
| Python compilation, integration and tests | Passed |
| JavaScript syntax | Passed |
| Manifest | **0.4.0-beta.11**; all other metadata and dependencies unchanged |
| Translations | English/German key parity, JSON parsing and SET scope labels passed |
| HACS metadata and candidate content | Passed: all **37 tracked integration files**, modules, card, branding, notices and translations present; no generated/runtime files |
| Approved-source comparison | All executable sources and tests unchanged from approved feature; manifest version-only difference |
| Git whitespace | Passed |
| User files | Eight pre-existing untracked files retained; SHA-256 hashes unchanged |

Before tagging, verify the final committed source archive against candidate
integration file hashes, including its beta.11 manifest. After tag push, verify
the GitHub tag source archive against those same hashes. HACS consumes this
tracked source archive; no separate release ZIP is required. Local untracked
investigations and generated files are excluded from the integration package.

## Cross-repository OCPP compatibility

Use the exact matching Stationary commit above in an offline harness with no
network connection or hardware backend. Its actual DeviceModel inventory is
loaded through WBM's actual Adapter discovery. Verify the identical capability
name sets, connector-scoped `MaximumCurrent`/`MaximumAsymmetryCurrent`, EVSE-scoped
minimum/step/supported modes, and absence of legacy phase-specific maximum names.

WBM's actual guarded dispatch is schema-validated and passed through Stationary's
actual profile parser for 3p/16 A and zero-current pause with omitted phases.
Supported readback confirms the point and a second reconciliation sends no
duplicate profile. Both recognized lockout reason/remainingSeconds payloads are
OCPP-schema-valid and retain remaining+5 / missing-duration 60 s retry semantics.
The harness uses approved-setting fixtures; actual Settings migration,
atomic/durable approval, timing and draft equivalence are covered by the full
HA integration suite, including the 16 reviewed reevaluation regressions.

Historical/source references rechecked: `v0.3.2-beta.18` at
`4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`,
`custom_components/wallbox_manager/solver/operating_point.py`; actual openWB/core
`ed5f7038c5c9be525e05429d3a82ec7571680bf7`,
`packages/modules/common/evse.py` (`set_current`, lines 114–126). No historical
runtime or physical safety logic is imported. WBM-ARCH-01–12 retain precedence
over historical descriptions; WBM-GRID-01–30 define this NETZ release.

## Publication gates and unchanged references

Require passing GitHub CI on the exact final develop release commit before
creating the annotated `v0.4.0-beta.11` tag. Verify the tag object and peeled
commit, then require applicable tag CI to pass before publication. Publish with
`prerelease=true`, `draft=false`, `make_latest=false`. No beta.11 tag or release
existed at preflight. Exact commit/tag identities, archive checks and CI/release
URLs are reported after publication; they cannot be embedded in their own commit.

Original remote main references, retained for final comparison:

- WBM: `09c6148da7a2e033e36ca2280cebe300cb217542`.
- Stationary: `2baf0daa8c446dad7570e5daa5f0f1cba486e915`.

Stationary develop, main, files and installed software remain unchanged. Its
existing workstation checkout is not synchronized or modified. WBM main is not
merged, reconciled or pushed. Old tags, releases and feature branches are retained.
No software/configuration is deployed; no Home Assistant, service restart or
actual hardware interaction occurs. Physical acceptance described in the release
notes remains pending and will be performed manually and separately.
