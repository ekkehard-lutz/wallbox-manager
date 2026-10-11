# v0.3.2-beta.12 — PV_OPTIMUM stability and diagnostics

**Experimental prerelease for Home Assistant hardware validation.**

## PV_OPTIMUM stability and diagnostics

- Stabilize confirmed FAST current setpoints near soft-power quantization
  boundaries to prevent rapid 3p/6A ↔ 3p/7A command oscillation. Retention uses
  fresh physical-phase and voltage evidence and remains subject to all hard limits.
- Preserve economic PV pause reasons independently from restart eligibility
  and genuine battery-SoC protection. Rejected, canceled and superseded OFF
  commands retain valid continuation where the command outcome permits it.
- Preserve valid phase-switch probe backoff across ordinary PV parameter changes;
  discard stale technical restrictions when the relevant control context changes.
- Give unavailable control authority priority in the PV status display while
  keeping genuine telemetry problems independently visible.
- Add 62 Python regression cases covering operating-point stability, pause
  lifecycle, phase lockout, runtime recovery and authority diagnostics, plus
  16 additional card checks for the German and English messages.

## Compatibility and safety

- Existing PV_OPTIMUM energy policies and the 90-second BALANCE PV-deficit pause
  remain unchanged. Normal DOWN selection, SoC hysteresis, battery discharge
  ceilings, the 100 W uncertainty margin and electrical limits remain binding.
- Retain beta.11 minimum-current continuation during a specific phase-switch
  lockout, its bounded 50% grid-import allowance and existing grace period,
  strict target-SoC stopping and phase-response stabilization.
- Preserve authority protections, explicit takeover, station-side lockouts,
  command fences and charging-session behavior. No wallbox firmware changes
  are required.

## Validation and hardware testing

All 2102 Python tests passed. Ruff lint and formatting, all 145 JavaScript card
checks, the JavaScript syntax check, Git whitespace checks and manifest/HACS
metadata checks passed on the release candidate.

Hardware confirmation remains outstanding. The original delayed telemetry-warning
observation still lacks a precise timeline and identified input channel; the
reproduced authority/status-priority defect is fixed. Fresh voltage alone does
not prove that all required PV inputs are fresh. The offer-versus-measured-power
discrepancy remains outside this release's scope.

Select `v0.3.2-beta.12` as a prerelease in HACS and verify integration version
`0.3.2-beta.12` after installation. Installation and hardware testing remain
operator-controlled. No Home Assistant deployment, restart, firmware change or
hardware charging test was performed. The latest stable release remains `v0.3.1`.
