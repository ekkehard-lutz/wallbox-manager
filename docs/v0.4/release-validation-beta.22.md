# v0.4.0-beta.22 release validation

## Preflight and reviewed sources

| Ref | Verified SHA |
| --- | --- |
| Initial develop | `824a60004b98cd72eec9bae3020101ef697149d9` |
| Initial main and peeled beta.21 | `a1c71bb8758733b886f8e87c3d1738749af7c429` |
| Annotated beta.21 tag object | `686484317e326f4a4f9c50b1eb22c1f13d95ac90` |
| Reviewed NETZ tip | `fb4a7bdb85d95b7c2ea2c603ea4a7f649ffcb6d8` |
| Reviewed PV tip | `1579fb70414ac137cb695588a6a0969538b5e431` |
| NETZ merge into release branch | `54dfdc85c0db411f27fc5c4e51f03c39d43ce227` |
| PV merge into release branch | `ab07eaa335ec3a1fcf4ce386bf9d9d36feae01cc` |

The tracked worktree was clean. Eight unrelated untracked files were left in
place. No beta.22 branch, tag or published release existed. The preflight
captured 83 tag ref lines including peeled refs and 41 release records.
Beta.21 was the most recent published prerelease. The latest stable release
was v0.3.1, ID 399530100.

Both feature tips had successful exact-SHA GitHub Actions runs.
[NETZ CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42368324519),
[PV CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42378628798)

## Semantic integration

Both features were merged into `release/v0.4.0-beta.22` with `--no-ff`.
In `grid.py`, the permission watcher retains NETZ action-generation and
re-enable behavior, clears the PV phase deadline on permission loss, and
uses the shared epoch invalidation path to drop stale PV ACK context.
The wire guard retains both diagnostic paths and both positive-command
fences. Reconciliation initializes NETZ execution and PV ACK state
separately. The specification index preserves both amendments.

The audit followed profile selection and release retirement through Settings,
explicit and legacy actions, the PV regular tick, and the shared OCPP wire
guard. The NETZ clock does not grant electrical authority. The PV phase wait
does not arm a NETZ schedule. Profile changes retire the previous release
before durable profile selection. The common epoch invalidation fences
pending commands and confirmation, while the settings lock and action
revision protect schedule and editor writes.

The added integration tests cover a waiting and active NETZ schedule,
draft retention and stale Apply, explicit Stop during PV phase wait with
and without permission loss, a delayed PV rejection after switching to
NETZ, and a timeout after PV ACK followed by NETZ readback. The feature
tests remain in the release tree.

## Combined local checks

| Check | Combined candidate | Published beta.21 |
| --- | --- | --- |
| Full Python suite | **954 passed**, five dependency warnings, 222.31 s | 895 passed |
| Statement coverage | **93.83%** (4,153/4,426 covered, 273 missing) | 93.51% |
| Focused feature and cross-profile suites | 59 passed | — |
| Frontend | 95 passed | 92 passed |
| Real headless Chrome | 1 passed, 96 layout and 48 action/theme combinations | 1 passed, 96 layout combinations |
| Static checks | Ruff lint and format, production/test JavaScript syntax, whitespace, manifest version-only check, 159 matching translation leaves and service field parity | Passed |
| Documentation | 146 local links and anchors across 14 Markdown files | Passed |
| Invariance | 35 protected integration blobs and all reviewed test files verified byte for byte | Reference |

Outside the feature union, production release edits are limited to the
manifest version. Protected runtime modules, dependency pins, HACS metadata,
Stationary and Fronius source are preserved. For the source basis and prior
behavior, see the [NETZ review](../netz-timing-readiness-diagnostics-review.md)
and [PV review](../pv-phase-lockout-continuation-review.md). The integration
cross-check used Stationary commit
`279287c3c1d8863e803144eccf773ab3f10889a5`, openWB/core
`849340ec8d91f4e7cca08a7664cf9ec0187fb496`
(`packages/control/chargepoint/chargepoint.py`), and WBM
`v0.3.2-beta.18` (`docs/phase-lockout-protocol.md`). These references preserve
the station's physical responsibility and atomic rejection boundary.

## Publication and physical acceptance

Each push gate is checked independently on its exact ref and SHA, in order
from candidate to develop, main and annotated tag. The candidate, develop
and main trees must be identical. Final identities and CI links are recorded
in the published release body so the source tree needs no post-validation edit.

The standard tagged source archive is the HACS package. Installation and
physical acceptance remain operator-controlled. The remaining scenarios are
a disabled NETZ Start and Stop with pending drafts, timed NETZ start and
retirement with a fresh second run, and atomic PV phase-lockout continuation
and fresh retry in both directions with actual Stationary responses.
