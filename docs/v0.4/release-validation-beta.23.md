# v0.4.0-beta.23 release validation

## Reviewed sources and branch ancestry

| Ref | SHA |
| --- | --- |
| Prior published beta.22 and initial `main` | `7d6944b9f9428416dd138ce40e9d08b9741e5636` |
| Common beta.22 merge base | `e3b9bae69daa05f3481037b56e6fba87b9913275` |
| Common beta.22 and initial `main` tree | `160076f6050a7e1ee435d92d01892945c1efe3ba` |
| Initial `develop` | `59b6c234dcde4e2eb27bc330abc5c2244800e254` |
| Reviewed session recovery tip | `f039af8f26b2b1e3ad2d9b4b794c980a972b22dc` |
| Phase A `develop` merge and release branch baseline | `2cc3940286b1ca1ef0ecca668c40df0bdab1c344` |

Initial `main` preserves a separate release history with the same source tree
as the common beta.22 merge base. The beta.23 candidate starts at the verified
Phase A merge. The prior beta.22 prerelease and annotated tag were present,
beta.23 had no branch, tag or release, and the latest stable release was
v0.3.1. Local work was preserved using an isolated worktree.
[Beta.22 release](https://github.com/ekkehard-lutz/wallbox-manager/releases/tag/v0.4.0-beta.22) ·
[Reviewed session tip CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/43005390849) ·
[Exact Phase A merge CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/43054928038)

The release source includes the following reviewed changes. Each commit is an
ancestor of the Phase A merge and was outside the beta.22 tag history. The
review and regression links describe the evidence retained in this release.

| Scope | Implementing commit | Evidence |
| --- | --- | --- |
| F1 rejection certainty | `6111d6a` | [Wire and reconciliation regressions](../../tests/test_ocpp_rejection_certainty.py) |
| F3 economic journal recovery | `4d2992e` | [Journal regressions](../../tests/test_pv_journal_recovery.py) |
| F5 scoped fault handling | `489e4c4` | [Fault intent regressions](../../tests/test_critical_fault_intent.py) |
| R1 NETZ resume | `08d77bb` | [Resume regressions](../../tests/test_netz_resume.py) |
| R2 teardown | `92b86b4` | [Teardown regressions](../../tests/test_runtime_teardown.py) |
| R3 permission gaps | `b416ecd` | [Economic interval regressions](../../tests/test_pv_permission_intervals.py) |
| PV transaction rollover | `e55fe76` | [Budget rollover regressions](../../tests/test_pv_transaction_rollover.py) |
| Cleanup batches 1–4 | `a40a7a3`, `2687761`, `c14f671` | [Controls](inactive-controls-cleanup-review.md) · [Registry](entry-registry-cleanup-review.md) · [Journals](journal-import-cleanup-review.md) |
| Session recovery, registration and PV approvals | `2cc3940` | [Reviewed implementation](session-recovery-registration-review.md) |

The openWB cross-check used core revision
`849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
`packages/control/chargepoint/chargepoint.py` lines 154–188 for station-owned
interlocks and unplug-triggered OCPP stop. The mocked companion contract used
Wallbox Stationary `279287c3c1d8863e803144eccf773ab3f10889a5`. The
`v0.3.2-beta.18` session Store at
`87963014c9af73416c438807f523a3da2a98bd22`,
`custom_components/wallbox_manager/session_storage.py`, documents the
historical delayed-save and flush behavior. The release keeps the reviewed
current implementation and authority boundary.
[Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py#L154-L188)

## Local validation

Phase A passed 1,195 Python tests with zero skips and five dependency
deprecation warnings, plus all standard frontend and static checks. Its
merge tree matched the independently reviewed feature tip.

| Check | Candidate result |
| --- | --- |
| Full Python suite | **1,195 passed**, zero skipped, five warnings, 313.08 s |
| Statement coverage | **94.94%** (4,295 of 4,524 statements, 229 missed) |
| Focused release regressions | **1,048 passed**, zero skipped, five warnings, 291.80 s |
| JavaScript syntax and frontend | Production and test scripts parsed. **95 frontend tests passed**. |
| Headless Chrome | **1 passed**, exercising 96 layout and 48 action/theme combinations |
| Static and metadata | Ruff lint/format, manifest version-only check, HACS metadata, 152 matching localization leaves and service field parity |
| Documentation | 665 local links and anchors across 128 Markdown files |
| Git whitespace | Passed |

The local environment used Python 3.14.7 and Home Assistant 2026.8.0.
The five dependency warnings are one Home Assistant HTTP application subclass
deprecation and four Backoff uses of `asyncio.iscoroutinefunction`. GitHub's
standard runner skips the optional Stationary source contract when the
companion checkout is absent. The local full run included that contract.

The focused run covers journal preservation, replay and scope, OCPP
registration and reconnect, NETZ Start/Apply/Stop and scheduling, approvals,
current caps and phases, PV SoC and FAST/BALANCE, economic STOP and restart,
budget rollover, battery reserve recovery, current configuration and entity
identity, and teardown. Full Python validation includes the read-only
companion source fixture with mocked hardware.

Release preparation changes the integration manifest version to
`0.4.0-beta.23` and adds release documentation. Runtime code, tests, HACS
metadata, dependency pins, service schemas and entity definitions match the
Phase A merge byte for byte. OCPP remains pinned to `2.1.0`. The README's
version-specific guidance is conditional on GitHub publication so the tested
tree can be published as-is.

## Publication and physical acceptance

Each push gate checks its exact ref and commit for the candidate, final
`develop`, final `main` and annotated tag. The candidate, destination
branches and peeled tag must share the same tree. The published GitHub
prerelease records the exact commit, tree, tag and CI references. The
standard tagged GitHub archive supplies the HACS source.

This validation used local simulated HA and OCPP, mocked Stationary
hardware and a real headless browser. Installation and the 17 physical
acceptance scenarios are separate operator-controlled work. Quantitative
PV performance remains open. [Release notes and rollback](release-notes-beta.23.md#hacs-installation-and-rollback) ·
[Physical acceptance matrix](release-readiness-audit.md#13-outstanding-physical-acceptance-matrix) ·
[GitHub prerelease](https://github.com/ekkehard-lutz/wallbox-manager/releases/tag/v0.4.0-beta.23)
