# v0.3.2-beta.13 — Unified measured PV grid import policy

**Experimental prerelease requiring real Home Assistant hardware validation.**

## Unified PV regulation and economic STOP

- Apply one session-bound 50 Wh grid-import budget to all available PV profiles,
  including PV_SURPLUS without a home battery. The threshold is half the smallest
  currently executable positive charging point, using actual voltages, currents,
  phase capabilities and restrictions. Above that threshold, count the entire
  measured positive site import; at or below it, suspend accumulation.
- Reset only after strictly more than 15 seconds of supported below-threshold
  measurements. Use distinct, fresh source generations and monotonic acceptance
  intervals of at most 10 seconds; duplicates, gaps and HA downtime grant no
  energy or reset credit. Preserve proved energy across phase and threshold changes.
- Replace competing H-minimum vetoes and economic deficit/import STOP timers.
  Ordinary FAST/BALANCE power reduction still uses the existing battery-aware
  economic allocation. An eligible continuation may exceed that allocation only
  at the exact technical minimum; arbitrary higher points receive no exception.

## Restart, persistence and profile semantics

- Budget reset alone cannot restart charging. Retain normal SoC, startup-response,
  freshness, authority and technical gates, and require meaningful model-based
  improvement relative to the failed episode's minimum operating point. Reuse the
  existing 100 W deadband to reject unchanged conditions and measurement noise;
  elapsed time or a timed phase probe alone cannot buy a cheaper retry.
- Preserve normal battery-assisted starts without a new mandatory availability
  sensor or a PV-only restart requirement. Persist verified energy, economic pause
  provenance and restart references against the wallbox and external transaction.
  Unknown or mismatched recovery cannot create a fresh automatic allowance; anchors
  and partial reset time are not restored. Explicit OFF also fences a delayed
  initial checkpoint so stale ON preparation cannot revive its retired scope.
- Share regulation across battery-equipped PV profiles. PV_MAXIMUM retains its
  reserve-plus-hysteresis target, PV_SURPLUS its bounded manual target, and
  PV_OPTIMUM its existing dynamic forecast target. Battery-free PV_SURPLUS needs
  measured grid import/export and actual EV power, without separate PV-generation,
  consumption or battery mappings. Configuration help describes those inputs and
  the retained stop-delay setting, which no longer controls economic STOP.
- Preserve genuine electrical and SoC protections, physical-response stabilization,
  explicit ownership, OCPP command fences, ACK/BUSY handling and station-side
  lockouts. The beta.10 ledger/evidence correction remains intact. Physical
  lockout durations and wallbox firmware are unchanged.

## Validation and hardware limitations

All 2,420 Python tests passed (five upstream HA/backoff deprecation warnings).
Ruff lint and formatting, all 145 JavaScript card tests, the JavaScript syntax
check, Git whitespace checks and manifest/HACS metadata checks passed on the
release candidate. Tests used temporary HA instances and simulated OCPP peers.

The model cannot guarantee actual future battery support. Further economic stops
remain possible if the battery does not respond as expected. The 50 Wh limit
requests STOP; sampling, acknowledgement and BUSY delays can allow additional
import before charging physically stops. Hardware validation must confirm trusted
meter cadence/source metadata, battery response, phase handling and station limits.

Select `v0.3.2-beta.13` as a prerelease in HACS and verify integration version
`0.3.2-beta.13` after installation. Installation and hardware testing remain
operator-controlled. No Home Assistant deployment, firmware change or hardware
charging test was performed. The latest stable release remains `v0.3.1`.
