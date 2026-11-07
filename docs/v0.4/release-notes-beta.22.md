# Wallbox Manager v0.4.0-beta.22

This HACS prerelease integrates two independently reviewed fixes on the
beta.21 baseline.

## NETZ timing and readiness

- Start and Apply require an observed enabled hardware permission. An
  active release can still be stopped while hardware is disabled, including
  with unapproved NETZ drafts.
- A run consumes its approved delay and duration. Retiring the run removes
  those consumed values, while independently edited drafts can carry forward.
- Time input focus leaves the countdown alone. A meaningful edit starts
  from the displayed value, and the schedule changes only on Apply.
- Start, Stop and Apply use green, red and blue. Disabled actions use grey.
  Sparse NETZ diagnostics distinguish the business schedule from electrical
  execution.

See the [NETZ implementation review](../netz-timing-readiness-diagnostics-review.md).

## PV phase-lockout continuation

An atomic Stationary `Rejected / PhaseSwitchLockout` can leave a valid,
independently confirmed offer in place without sending the phase minimum.
The wait restricts phase switching while ordinary same-phase regulation and
mandatory stops remain available. After the deadline, the next eligible
regular PV tick decides again from current inputs and authorization.
Level 2 diagnostics separate proposal, submission, acknowledgement, readback
confirmation and physical phase. FAST/BALANCE policy and the accepted
battery-limited FAST exploration are preserved.

See the [PV implementation review](../pv-phase-lockout-continuation-review.md).

## HACS upgrade and rollback

The release uses GitHub's standard tagged source archive. For a separately
authorized installation, select **v0.4.0-beta.22** through HACS, take a full
HA backup, and verify manifest version **0.4.0-beta.22** after the
operator-managed installation. Retain entity identities, mappings and journals.
Check the browser loads the current card resource.

Manager release starts OFF after integration reload. Beta.22 retires matching
previously approved NETZ times on migration when an older record cannot
distinguish a completed run from a reusable setting. Independent drafts
survive. A package-only downgrade cannot recover consumed values. Preserve
the pre-upgrade backup if the exact older store is needed.

## Verification and physical acceptance

The [release validation](release-validation-beta.22.md) records the combined
local checks. The published release body records the exact candidate, develop,
main and tag CI results. These are automated checks with simulated OCPP and a
real browser. Operator-controlled HACS installation and physical verification
of hardware permission, timing retirement, and both directions of phase lockout
remain separate.
