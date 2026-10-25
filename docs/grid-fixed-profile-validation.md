# NETZ implementation validation — 23 October 2026

Branch: `feature/grid-fixed-profile`. Approved base:
`f1e24d69cf350cd93b2627521591d46360fccc2b` (verified exact documentation-branch head).
A clean isolated checkout was created because the original WBM develop checkout
contains pre-existing untracked files; those files were preserved untouched.
The paired Stationary implementation is
`e9f7e22db70f54c7ca86bef778326f6d0f8680df` on the same feature branch.

| Check | Result |
| --- | --- |
| Full maintained pytest | **244 passed**, 30.75 s; 5 dependency deprecation warnings |
| Ruff check | Passed |
| Ruff format --check | Passed, 100 Python files |
| JavaScript syntax | Passed |
| Node card regression suite | **18 passed** |
| Manifest JSON and English/German translation-key parity | Passed |
| Git diff whitespace and approved-base ancestry | Passed |

Tests use real disposable HA fixtures, schema-valid local OCPP peers, fake clocks,
and hardware stubs. They do not change an installed Home Assistant instance and
are not physical hardware acceptance. No version change, tag, merge, release,
deployment, service restart or charging-hardware operation was performed.

## Requested, acknowledged, confirmed and measured

For the simulated 230 V / (1,3) / 6–27 A case, an 11,000 W request with NEAREST
selects 3p × 16 A, confirmed offered power **11,040 W**. Metered EV draw remains
an independent observed value (the reserve test uses **10,000 W**). A 6,000 W
edit while that 3p offer is running retains 3p and resolves to 9 A / **6,210 W**.
These quantization differences are deliberate. Rejected targets remain unresolved;
Accepted without matching readback remains accepted_unconfirmed. During lockout,
the existing confirmed point is retained and is not mislabeled as desired success.
No substitute profile is dispatched.

## Remaining acceptance and review concerns

Stationary can correlate an observed DIN read outage followed by configured boot
current and restore its Remote desired current, including zero. There is no
positive reboot counter, so resets missed between samples cannot be reliably
identified; arbitrary deviations are not automatically overwritten. This is the
explicitly allowed evidence limitation, not a manager resend workaround.

Physical paired inventory/readback, EVSE boot/fallback behavior, phase switching,
300/600-second lockouts, CP/automatic wake-up and the installed reserve entity
remain unvalidated. Cross-repository assumptions and inspected openWB/historical
revisions are recorded in [the implementation/source assessment](grid-fixed-profile-implementation.md).
Independent review should inspect generation/late-response and schedule ownership,
legacy-cap startup restoration and HA number-service races. Same-value external
reserve writes cannot establish writer identity; HA lacks atomic compare-and-set.
The new Grid policy uses event-triggered tasks and one-shot business/retry timers,
without restoring the historical regulator/phase optimizer or duplicate metering.
The optional start-delay pause remains subject to Stationary's re-enable lockout;
finite schedule windows do not bypass that hardware policy.

## Changed files

- `README.md`
- `custom_components/wallbox_manager/__init__.py`
- `custom_components/wallbox_manager/control.py`
- `custom_components/wallbox_manager/entity.py`
- `custom_components/wallbox_manager/migration.py`
- `custom_components/wallbox_manager/number.py`
- `custom_components/wallbox_manager/ocpp.py`
- `custom_components/wallbox_manager/select.py`
- `custom_components/wallbox_manager/settings.py`
- `custom_components/wallbox_manager/strings.json`
- `custom_components/wallbox_manager/translations/de.json`
- `custom_components/wallbox_manager/translations/en.json`
- `custom_components/wallbox_manager/www/wallbox-manager-card.js`
- `tests/test_identity_migration.py`
- `tests/test_observations.py`
- `tests/test_passive_benchmark.py`
- `tests/test_wallbox_card.cjs`
- `custom_components/wallbox_manager/battery.py`
- `custom_components/wallbox_manager/grid.py`
- `custom_components/wallbox_manager/grid_battery.py`
- `custom_components/wallbox_manager/grid_point.py`
- `docs/grid-fixed-profile-implementation.md`
- `docs/grid-fixed-profile-validation.md`
- `tests/test_grid_battery.py`
- `tests/test_grid_profile.py`
