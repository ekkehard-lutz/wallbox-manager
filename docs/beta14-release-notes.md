# v0.3.2-beta.14 — PV charging feedback stability

**Experimental prerelease for controlled Home Assistant and charging-hardware
validation. Hardware stability has not yet been established.**

## PV charging feedback stability

- Fix FAST becoming blocked when an EV genuinely responds while consuming less
  than the offered charging power. Retain dispatch-time EV/source evidence and
  accept meaningful fresh movement in the command's direction, preserving source
  freshness, command-response gates and phase-settling safeguards.
- Stabilize PV_BALANCE against asynchronous EV/site measurements and independently
  smoothed PV/total-consumption readings. Cap upward preference with conservative
  raw PV/flow capacity and require fresh sources and the previous physical response
  before increasing.
  Real raw deficits and measured import still reduce promptly; smoothing lag
  alone does not manufacture surplus or force a reduction.

## Economic accounting

- Preserve supported measured grid-import intervals, causal threshold/activity
  events and below-threshold reset progress across internal regulator task
  relaunches in the same control scope.
- Retain economic STOP provenance and restart inhibition. Command authorization
  markers are still revoked, and genuine lifecycle changes or invalid measurement
  evidence still break coverage. Duplicate or unsupported intervals grant no
  energy or reset credit.
- Keep the 50 Wh threshold, dynamically verified M/2 comparison, strict
  >15-second reset and existing economic restart policy unchanged.

## Verification

The beta.14 cycle adds **64 deterministic regression cases**: 47 feedback cases
and 17 economic relaunch cases. The exact versioned release candidate passed
**2,484 Python tests** (five dependency deprecation warnings), **145 frontend
tests**, Ruff lint/format, JavaScript syntax, Git whitespace and manifest/HACS
metadata checks. Tests used temporary HA instances and simulated OCPP peers.

No intentional change to the SoC state machine, forecasting, electrical/phase
limits, OCPP transactions/command fences or authority policy.

Implementation and verification details:

- [PV feedback stability review](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.14/docs/pv-regulation-feedback-stability.md)
- [Economic relaunch verification](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.14/docs/pv-economic-relaunch-verification.md)

## Hardware validation pending

Use this prerelease for controlled tests of EV under-offer response and tapering,
raw versus smoothed household/cloud changes, actual meter acquisition/cadence,
battery response, phase transitions, station limits and reload/input gaps.
Asynchronous reports do not guarantee simultaneous physical acquisition. A cold
adopted under-offer point without a causal dispatch baseline remains conservatively
gated; the existing near-offer response proof remains available. The 50 Wh value
triggers STOP; measurement and OCPP delays can permit
additional import before the physical stop.

Select `v0.3.2-beta.14` using HACS prerelease versions and verify integration
version `0.3.2-beta.14` after installation. HACS discovery and installation have
not been verified here. No Home Assistant deployment, wallbox/firmware change
or hardware charging test was performed. The latest stable release remains
`v0.3.1`.
