# v0.4.0-beta.10 release validation

## Verified integration inputs

Both remote feature heads were verified before integration and matched the approved
commits exactly. Neither develop branch had an additional develop-only commit:

| Repository | Original develop | Approved feature |
| --- | --- | --- |
| Stationary | `d5bb336e3d53165c76ecf8798239b31a9c75f3aa` | `e9f7e22db70f54c7ca86bef778326f6d0f8680df` |
| WBM | `e9f6cc387a8be777844355036c42a5727ced0eb8` | `212afafc7f2a34911d1042d024cf0eaae26f57d0` |

Stationary non-fast-forward merge: `ce823cf52faa511475ef87a776c7a0dce28e1184`.
Its parents are the original develop and approved feature above; its tree is
identical to the approved feature tree. It was validated, pushed to develop and
verified remotely. No Stationary release, tag or version change was made.

WBM non-fast-forward merge: `13eff0ec4ae015d61c56aab461a5a799a18366f9`.
Its parents are the original develop and approved feature above; its clean tree
was identical to the approved feature tree. It retains the full specifications,
NETZ implementation and reviewed reserve-recovery fix through ordinary ancestry.

The separate release-preparation commit changes only:

- `custom_components/wallbox_manager/manifest.json`: version beta.9 → beta.10.
- `README.md`: current release/version and coordinated compatibility notice.
- `docs/v0.4/release-notes-beta.10.md`: release notes and acceptance boundary.
- This validation record.

No executable changes, dependency changes, storage/config-entry version changes,
main reconciliation or unrelated merge resolution accompany release preparation.

## Local validation results

| Check | Result |
| --- | --- |
| Stationary full maintained pytest + interactive CSMS | **735 passed, 612 subtests passed**, 63.08 s |
| Stationary targeted OCPP/runtime, changed tests and CSMS Ruff | Passed |
| Stationary full Ruff relative to documented approved-base archive | **142 existing findings on both trees; zero introduced** |
| Stationary full format relative to that archive | **76 existing files** need formatting versus 77 on the approved base; no newly unformatted file |
| Stationary Python compilation and Git whitespace | Passed |
| WBM full maintained pytest | **255 passed**, 33.63 s; 5 existing dependency deprecation warnings |
| WBM frontend/card suite | **18 passed** |
| WBM Ruff lint and formatting | Passed |
| WBM Python compilation: integration and tests | Passed |
| WBM JavaScript syntax | Passed |
| Manifest version/metadata | **0.4.0-beta.10**; domain, codeowners, dependencies and other metadata unchanged |
| English/German translation-key parity and JSON parsing | Passed |
| HACS metadata/content preflight | Passed: all **37 tracked integration files**, required modules, frontend, branding, notices and translations present |
| Approved-source comparison | Every integration file except the manifest is byte-identical to the approved WBM feature |
| Git whitespace and clean merge-tree checks | Passed |

The HACS archive gate compares the final committed Git source archive's integration
subtree with the validated candidate file hashes, checks beta.10 in the archived
manifest and rejects generated/runtime files in the installed integration subtree.
HACS uses the tracked source archive; no release ZIP asset or local investigation
file is required. The exact final commit and annotated tag identities, archive hash,
CI run links and remote publication results are reported after those gates finish.

## Cross-repository contract check

An offline conformance harness loaded the actual Stationary DeviceModel inventory
into WBM's actual Adapter initialization. Both maximum diagnostics were discovered
at Connector / EVSE 1 / connector 1, with MinimumCurrent, CurrentStep and
SupportedPhaseModes at EVSE 1. No legacy phase-specific maximum name was advertised.
WBM resolved an 11 kW request to 3p/16 A, emitted its full guarded TxProfile and had
it validated against the OCPP 2.1 schema and actual Stationary profile parser.
Readback confirmed the offered point; a second reconciliation emitted no profile.
The zero-current profile preserved phases by omitting numberPhases and also parsed
successfully. Both lockout reason/remainingSeconds responses passed OCPP schemas;
WBM interpreted 31 seconds as 36 seconds to retry and missing durations as 60 seconds.
This harness used no network connection or hardware backend.

These results complement maintained simulated OCPP/HA tests; they do not establish
physical charging, phase-relay, CP or installed home-battery acceptance.

## Publication gates and unchanged main refs

Publication requires passing GitHub CI on the exact final develop release commit,
then an annotated `v0.4.0-beta.10` tag pointing to that commit, with applicable tag
CI also passing. Publish only as prerelease, latest=false, draft=false, using the
prepared notes. No beta.10 tag/release existed at preflight.

The original main heads recorded for final comparison are:

- Stationary: `2baf0daa8c446dad7570e5daa5f0f1cba486e915`.
- WBM: `09c6148da7a2e033e36ca2280cebe300cb217542`.

No main ref is merged, synchronized or pushed. Old feature branches/releases are
retained. Publication deploys neither component and changes no installed software,
configuration, HA state, service or charging hardware. Manual deployment and
physical acceptance remain separate, as detailed in the release notes.
