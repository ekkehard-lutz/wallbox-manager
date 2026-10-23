# NETZ zero-current and lockout implementation review

Review date: 23 October 2026. Scope: `fix/grid-zero-approximation-lockout`.
The workstation was synchronized before code inspection. Remote `develop` was
`063ef61a6d8407fd80913c0bca31b83b3ef1bfab`; the reviewed feature HEAD was
`a34fab37868b0cc3f5c10a3de7ce2fccaf2f2e12`. Both matched the requested refs.
Existing untracked workstation files were preserved, without stashing or reset.

## Requirements and sources

Read the complete `grid-profile.md` specification (WBM-GRID-01–21),
`architecture-responsibility.md` (WBM-ARCH-01–12) and
`active-ocpp-control.md`. The zero-current amendment supersedes the earlier
positive-minimum wording and the initial implementation report's statement that
no substitute is sent. Phase selection still precedes within-mode quantization;
non-monotonic boundaries retain the documented ascending first-crossing rule.

Historical comparison: `v0.3.2-beta.18`, commit
`4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`; inspected `docs/grid-profile.md`,
`custom_components/wallbox_manager/control/runtime.py` and
`custom_components/wallbox_manager/solver/operating_point.py`. Retain the
separation of wire points, confirmed offers and measured draw. Do not restore
its freshness gates, phase optimizer or historical control runtime.

Actual openWB/core source comparison at
`ed5f7038c5c9be525e05429d3a82ec7571680bf7`: inspected
`packages/modules/common/evse.py` and `packages/control/phase_switch.py`.
The EVSE adapter compares the offered register current before writing; physical
switching remains delegated to the chargepoint implementation. Its measured
phase optimizer and cyclic runtime were not imported. This comparison is source
evidence, not confirmation of the installed hardware or a Stationary defect.

## Findings and corrections

The unchanged production code passed 277 Python tests. The 19 existing card
tests also passed when the additional frontend reproduction was introduced.
New regressions exposed these problems before the corresponding production fix:

- An accepted interim 0 A offer left acknowledgement at the earlier 16 A point.
  Record interim acceptance independently of the still-pending desired point.
- Interim rejection retained the original PhaseSwitchLockout reason. Generic
  Busy also perpetuated retries, while recognized ReenableLockout updated the
  delay but concealed its reason. Report the actual interim rejection. A generic
  interim failure ends automatic retry; a recognized lockout supplies its own
  remaining+5 or 60-second retry. New explicit intent can try again normally.
- A timeout during accepted interim confirmation lost the original recognized
  phase retry. Retain that retry and report `interim_readback_failure`; accepted
  zero is not confirmed zero without readback. A failed interim dispatch itself
  remains a communication failure, without inventing retry authority.
- A canceled retry callback could execute the current intent immediately and
  bypass its new trailing-edge debounce. Capture epoch, adapter and transaction
  in retry/debounce callbacks, retaining the existing wire-dispatch guard.
- A rejected ordinary read discarded the held positive phase for planning,
  switching a 4.2 kW request from confirmed 3p to 1p. Reuse the existing confirmed
  point only in the same initialized generation, transaction and physical mode.
- The card combined a missing confirmed phase with a separate physical-phase
  entity to fabricate a complete actual point. Require both applied fields;
  render unknown actual as `—` and preserve the pending requested arrow.

Additional review corrections and regressions:

- Clear retry handles and diagnostics when an attempt runs or confirms. Coalesce
  concurrent reconciliation for the same epoch, including phase notifications
  during the normal acceptance/readback path.
- Keep held observations for display and phase retention, but a sent proposal
  makes a held match insufficient to complete intent until supported readback.
  This prevents a superseded in-flight opposite write plus a failed read from
  being mistaken for confirmation of the old point. The uncertainty marker has
  no age, safety gate, polling task or replay permission.
- Hide applied attributes outside their confirmed generation/transaction/phase
  context and publish phase interpretation changes. Preserve historical internal
  observations; missing reads do not erase them.
- Report `power_above_mode_maximum` on a confirmed clamped point rather than
  implying that an impossible requested bound was satisfied.

The pure Fraction-based planner required no production change. An exhaustive
candidate oracle covers DOWN/UP/NEAREST on 1p/2p/3p, varying voltage, minimum,
step and cap, zero, positive sub-minimum, every exact/midpoint boundary, and
above-maximum clamping. Existing tests cover disabled modes, unequal voltages,
phase retention, dynamic adjacent-mode boundaries and non-monotonic ranges.
No nominal example threshold is used as an implementation constant.

## Validation

Commands run from the repository using its maintained `.venv`:

- `.venv/bin/pytest -q`: 315 passed; five existing dependency deprecation warnings.
- `node tests/test_wallbox_card.cjs`: 20 passed.
- `.venv/bin/ruff check .`: passed.
- `.venv/bin/ruff format --check .`: all discovered Python files formatted.
- `.venv/bin/python -m compileall -q custom_components tests`: passed.
- `node --check custom_components/wallbox_manager/www/wallbox-manager-card.js`: passed.
- Parsed manifest, strings, English/German translations and HACS JSON; verified
  translation key parity, required metadata and unchanged beta.10 dependencies/version.
- `git diff --check`: passed; reviewed diff is limited to Grid control, the
  concise card display, regression tests and this review record.

The added real HA/local schema-valid OCPP tests cover both requested examples:
3p -> DOWN 1.5 kW -> interim 3p/0 A -> subsequent ReenableLockout -> 1p/6 A;
and 3p -> UP 1.4 kW -> interim 3p/6 A -> 1p/7 A. They cover interim acceptance
and generic/recognized rejection, repeated lockout without duplicate interim
commands, confirmation failure, disabled current mode, stale callbacks, OFF,
Local, ended transaction, boot generation, profile change, actual integration
unload, superseded queued interim dispatch and opposite in-flight writes.
Frontend tests cover concise German points, equality collapse and missing/offline
confirmation. Existing reserve, ownership, permission, scheduling, telemetry,
registry, transport and migration regressions remain in the complete suite.

## Limits and delivery boundary

All tests use local simulated stations and HA fixtures. Physical relay/current
behavior, actual re-enable/phase timers and the paired Stationary installation
still require hardware acceptance. Generic 2p planning is tested; the installed
Stationary contract still advertises only 1p/3p. Ordinary measurements remain
last-value-held; silent external changes remain unobservable. A generic interim
failure requires a new explicit intent or a normal eligibility event; it does
not create an automatic retry loop.

Stationary was unchanged. No version change, merge, tag, release, deployment,
service restart, actual Home Assistant or hardware interaction was performed.
Only the existing fix branch is committed and pushed. Pre-existing untracked
workstation files remain; tracked changes are clean after the commit.
