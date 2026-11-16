# v0.4.0-beta.24 release validation

## Sources and publication evidence

| Source | Revision |
| --- | --- |
| Implementation baseline on `develop` | `a19c457091ab093f4d6012db9cc9805d12a8a43a` |
| Reviewed implementation on `feature/v040-beta24-fixes` | `b8194667799e0addce80709e34422c6eb9d937d6` |
| Operator field acceptance register | `a4d5d9cc19a833c8246d4921caab6b8da1a544f0` |
| Mocked companion contract, Wallbox Stationary | `279287c3c1d8863e803144eccf773ab3f10889a5` |

The release-preparation commit changes the manifest version, README and two
release documents, and repairs the link to the renamed nominal NETZ
requirements. The reviewed candidate's runtime, tests, service schema,
dependency pins and HACS metadata supply the package. The
[implementation handoff](beta.24-candidate.md#source-checks) records the
openWB/core and earlier Wallbox Manager source cross-checks.

The published prerelease's **Verification** section records the literal
release-preparation SHA, tree SHA, successful GitHub Actions run, annotated
tag SHA, and peeled commit. Recording those IDs there allows this document
to ship in the tested tree. The tag's Git reference identifies the
annotation object. The commit endpoint resolves the peeled commit and tree.
[Prerelease and verification record](https://github.com/ekkehard-lutz/wallbox-manager/releases/tag/v0.4.0-beta.24) ·
[Tag ref](https://api.github.com/repos/ekkehard-lutz/wallbox-manager/git/ref/tags/v0.4.0-beta.24) ·
[Peeled commit and tree](https://api.github.com/repos/ekkehard-lutz/wallbox-manager/commits/v0.4.0-beta.24)

## Automated validation

| Check | Local result |
| --- | --- |
| `python -m pytest -q` | 1,188 passed, zero skipped, five dependency deprecation warnings, 277.47 s |
| `ruff check .` and `ruff format --check .` | Passed. All 208 Python files formatted. |
| `node --check custom_components/wallbox_manager/www/wallbox-manager-card.js` | Production and both test scripts parsed. |
| `node tests/test_wallbox_card.cjs` | 96 passed. |
| `node tests/test_wallbox_card_layout.cjs` | One headless Chrome test passed. |
| Manifest and HACS | Manifest version-only diff verified. HACS metadata validated. |
| Translations and service schema | 148 matching translation leaves per locale and schema fields for all three services. |
| Documentation | 673 local links and anchors across 133 Markdown files resolved. |
| `git diff --check` | Passed. |

The local environment used Python 3.14.7, Home Assistant Core 2026.8.0,
Node.js 24.22.3 and headless Chrome 157.0.8103.78.

Local Python validation uses a simulated Home Assistant instance, loopback
OCPP peers and mocked Stationary hardware. GitHub's standard runner skips the
optional companion source fixture when that checkout is absent.

## Remaining physical acceptance

1. **F01** — Observe an active and ended charging session. Capture the
   duration state and unit and compare the card against elapsed time.
2. **F02a** — Reconnect Stationary during authorized NETZ charging. Compare
   the current-generation offer and phase readback with the displayed point
   and physical current. Check for extra proposals and missing evidence.
3. **F02b** — Track Fronius reserve ownership during the reconnect and
   restoration on Stop. Verify that an external reserve edit takes precedence.
4. **F03** — Verify that Local blocks NETZ Start and permits draft edits.
   Confirm that explicit takeover alone leaves the manager release retired.
5. **FR-01** — Restart HA in active, delayed and unlimited NETZ windows and
   across a missed expiry. Compare the original deadlines and check that
   station-side Stop while HA is offline prevents positive replay.
6. **FR-02** — Check fixed nearest steps, phase retention within an overlap,
   navigation across a phase boundary and removal of the approximation Select.
7. **PV** — Check one ordinary battery-equipped PV charging session for
   expected charging behavior.

[HACS installation and rollback](release-notes-beta.24.md#hacs-installation-and-rollback) ·
[Historical field acceptance](https://github.com/ekkehard-lutz/wallbox-manager/blob/a4d5d9cc19a833c8246d4921caab6b8da1a544f0/docs/v0.4/field-acceptance-beta.23.md)
