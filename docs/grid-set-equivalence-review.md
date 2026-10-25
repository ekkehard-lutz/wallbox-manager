# NETZ SET: reevaluate pending equivalent drafts

Reviewed starting point: `251d594b779038148f1a052d88e6ba65c7062448` on
`fix/grid-zero-approximation-lockout`. The workstation checkout was fetched and
safely fast-forwarded (already current); all eight existing untracked files were
preserved.

## Reproduced finding

Before changing production code, a real HA integration test with a schema-valid
OCPP 2.1 WebSocket peer failed at the expected assertion. At nominal 230 V and
minimum 6 A, approved 1.4 kW NEAREST resolved to 1p/6 A. Draft 1.6 kW NEAREST
required 1p/7 A and correctly remained pending. Changing approximation to DOWN
made both requests resolve to 1p/6 A, but approved power remained 1.4 kW and
`grid_set_required` remained true. The original draft-write-only equivalence
check did not run again.

## Focused correction

Reevaluate after an existing Grid reconciliation task finishes successfully and
after physical-phase context notifications. Approximation and current-cap edits,
capability changes, and current-generation initialization/confirmation already
drive these paths. No timer, telemetry regulator, transport request or retry
policy is added. Repeated notifications can check the draft but do not write or
execute an already adopted request.

Settings reuses one equivalence predicate for draft writes and these events.
Under the existing persistence lock it reads the latest draft and context,
requires unchanged timing drafts, validates numeric power, and delegates to the
existing canonical desired-point comparison. Current-generation confirmation,
physical phase consistency, uncertainty and in-flight-command guards remain in
force; a closed controller cannot establish equivalence. Missing capabilities or
confirmation retain explicit SET. Equivalence is to approved desired intent,
including a pending phase switch, rather than a temporary lockout actual point.

Only the numeric approval/revision is adopted. The existing atomic/fsync profile
write and disk verification finish before runtime values or approval
notifications change. Failures publish the approval error and retain the old
approval; they do not schedule a retry. The existing equivalent-approval event
preserves active timing, retry deadlines and execution guards. Immediate
approximation/capability decisions continue to use approved power. A different
draft cannot execute merely because approximation changes. Reserve behavior and
permission/authority handling are unchanged.

## Validation and reference checks

- Complete Python suite: **363 passed**, five existing dependency deprecation
  warnings. Sixteen new regression cases cover the exact reproduction, a draft
  remaining different, user caps, station maxima/minima and phase capabilities,
  missing/restored capabilities, pending timing, both lockouts, uncertain and
  later-confirmed readback, concurrent successful/failed explicit SET,
  restart/current-generation initialization, repeated notifications, and
  successful/failed automatic durable approval followed by restart.
- Frontend/card suite: **27 passed**; backend metadata remains authoritative.
- Ruff lint/format, Python compilation, JavaScript syntax, manifest metadata and
  unchanged `0.4.0-beta.10` version, HACS JSON, English/German translation key
  parity and SET labels, and Git whitespace checks: passed.
- The reproduction and repeated-notification regressions compare the complete
  received OCPP operation list as well as charging profiles. The immediate
  approximation decision retains its existing single schedule read; adoption
  adds no further OCPP requests. Lockout coverage preserves the retry deadline.
- Applicable specifications: `docs/specifications/grid-profile.md` (including
  WBM-GRID-22–30), `architecture-responsibility.md` (WBM-ARCH-01–12), and
  `active-ocpp-control.md`. Existing SET persistence and execution paths were
  inspected before adding the event hooks.
- Historical comparison: `v0.3.2-beta.18`,
  `4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`,
  `custom_components/wallbox_manager/solver/operating_point.py`; no historical
  runtime was restored.
- Actual openWB/core source rechecked at
  `ed5f7038c5c9be525e05429d3a82ec7571680bf7`,
  `packages/modules/common/evse.py` (`set_current`, lines 114–126). Its
  offered-current comparison supports retaining unchanged-offer suppression;
  no openWB control loop or physical switching logic was imported.

All peers are local simulations. Physical acceptance remains outstanding, and
incomplete planning context intentionally keeps SET required. No Stationary
change, version change, merge, release, deployment, service restart, or actual
Home Assistant/hardware interaction is part of this correction.
