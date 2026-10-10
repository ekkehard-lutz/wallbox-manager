# v0.3.2-beta.11 — Phase-switch lockout continuation

**Experimental prerelease for real Home Assistant hardware validation.**

- Continue an existing charging session at the minimum valid current on the
  confirmed physical phases when the station temporarily rejects a phase switch.
  The reproduced sequence `1p/19A → 3p/7A → rejected 1p/10A` now continues at
  `3p/6A`, subject to the actual supported minimum and safety limits.
- Enforce bounded total-site grid import of up to 50% of the minimum charging
  power, calculated from measured phase voltages and current limits. Sustained
  excess import still stops charging after the existing confirmation grace.
- During lockout continuation, configured battery SoC equal to its existing
  target permits charging; SoC below target stops it.
- Use the existing physical-response settling window to prevent immediate
  reverse phase switching. Reconsider blocked transitions from fresh measurements
  at the existing retry cadence.
- Preserve independent battery/electrical ceilings, measurement freshness,
  authority and command fences, physical-response validation, station-side phase
  and restart lockouts, and normal PV policies outside the temporary fallback.
  The beta.10 EV measurement ledger consistency correction is retained.
- Cover PV_SURPLUS, PV_OPTIMUM, and PV_MAXIMUM with 68 continuation regression
  cases, including the transient zero-power measurements from the hardware log.

Validation: 68 focused tests and all 2040 Python tests passed. Ruff lint and
formatting, frontend tests, and manifest/HACS metadata checks passed.

Hardware confirmation remains outstanding. Fallback requires the specific
`PhaseSwitchLockout` response and fresh authoritative measurements; generic BUSY
or restart rejection does not grant continuation. A genuine hard limit can still
require a safety STOP. No firmware changes, deployment, or hardware charging test
were performed for this release.

Select `v0.3.2-beta.11` as a prerelease in HACS and verify integration version
`0.3.2-beta.11` after installation. Installation and hardware testing remain
operator-controlled. The latest stable release remains `v0.3.1`.
