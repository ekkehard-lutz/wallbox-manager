# Wallbox Manager v0.4.0 release validation

## Accepted source and reconciliation

| Record | Revision |
| --- | --- |
| beta.24 implementation | `b8194667799e0addce80709e34422c6eb9d937d6` |
| beta.24 release commit | `6a4f033ceada732646fd58a6158f904f353f1632` |
| beta.24 tested tree | `68c5459b7b8740dad3103f0ed8d754dcdab8863f` |
| Original main | `443f4ca88efa2c28f80f0baf025d8a28823232db` |
| Original develop with field acceptance | `0dc729a9aeb33dfe5df4de7b4d05103f7a41ca3c` |

The beta.24 release descends from the original beta.23 integration into
`develop` at `a19c457091ab093f4d6012db9cc9805d12a8a43a`. The stable
preparation merges the beta.24 tag and the final field acceptance history.
The only change under `custom_components/` is the manifest version,
from `0.4.0-beta.24` to `0.4.0`. Tests, dependencies, service schemas,
translations, the workflow and HACS metadata match the beta.24 tree.
The remaining tree differences are README, the preserved field report,
these validation notes and the stable release notes.
[Beta.24 CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/43734927580)

The reference check used openWB/core revision
`849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
`packages/control/chargepoint/chargepoint.py`. Its plug and charging
interlock checks and unplug-triggered transaction stop are station-side.
Historical WBM `v0.3.2-beta.18` at
`87963014c9af73416c438807f523a3da2a98bd22` provides a reference for absolute
deadlines in `custom_components/wallbox_manager/grid_timing.py` and session
journal persistence in `custom_components/wallbox_manager/session_storage.py`.
[Accepted source cross-check](https://github.com/ekkehard-lutz/wallbox-manager/blob/6a4f033ceada732646fd58a6158f904f353f1632/docs/v0.4/beta.24-candidate.md#source-checks) ·
[Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py#L154-L188)

## Automated validation

The release gate runs the same Python, Ruff, JavaScript and browser checks
as beta.24 and checks the stable package metadata and documentation.

| Check | Beta.24 comparison |
| --- | --- |
| Python with mocked Stationary | 1,188 passed locally |
| Python on GitHub Actions | 1,187 passed, one optional Stationary fixture skipped |
| Lovelace card | 96 unit checks and one headless Chrome layout check |
| Formatting and syntax | Ruff lint and format, JavaScript syntax, and Git whitespace |
| Package and documentation | Manifest, HACS metadata, service schemas, translation key parity, and local Markdown links and anchors |

The local Python suite uses simulated Home Assistant, loopback OCPP and a
mocked Wallbox Stationary fixture pinned to
`279287c3c1d8863e803144eccf773ab3f10889a5`. Standard GitHub runners omit the
optional Stationary checkout. The release's GitHub verification records the
exact main commit and Actions runs, annotated tag, tree, and source archive
verification.
[GitHub release](https://github.com/ekkehard-lutz/wallbox-manager/releases/tag/v0.4.0)

## Physical acceptance

The physical evidence applies to the installed beta.24 runtime. The
operator used Home Assistant Core 2026.10.3, HAOS 18.4, Wallbox Stationary
and a BYD Seal U, with 16 A caps for both phase modes. See the field report
for F01, F02a, F02b, F03, FR-01 and FR-02 results and the remaining
boundaries, including the brief EVSE boot current during a full power cycle.
Stable release validation used the automated suite and inherited field
evidence.
[Field acceptance report](field-acceptance.md) ·
[Release notes and installation](release-notes-v0.4.0.md#hacs-installation-and-rollback)
