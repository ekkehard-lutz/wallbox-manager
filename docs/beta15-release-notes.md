# v0.3.2-beta.15 — Charging readiness and retained-current accounting

**Experimental GitHub prerelease for controlled Home Assistant and charging-hardware evaluation. Not a stable release; hardware acceptance remains pending.**

## Changes since beta.14

- Correct F1: an old FAST soft preference cannot authorize a higher executable phase/current offer before fresh response/source/interval evidence. Reprice the confirmed offer with current valid voltages while keeping desired-phase planning separate.
- Correct F2: qualified measured battery-discharge deficits can reduce the real solver-selected offer even when its nominal watts fit the ordinary budget. Preserve exact technical-minimum continuation and the existing shared 50 Wh economic STOP.
- Confirm general charging permission independently of transaction-bound preparation. Explicit ON can succeed with no vehicle or transaction where the supported authority/permission capability exists; actual readback remains mandatory. Later legitimate connection does not require another ON.
- Reconstruct manager-side physical reported state with stronger current-scope/source/generation and departure/sequence fencing. Preserve known paused/CP-unverified history; an old transaction departure cannot override a newer charging transaction.
- Begin prospective economic accounting for measured retained-current charging without a preceding new positive manager command, once exact real transaction, authority, actual EV/grid measurements, executable minimum, causal timestamps and durable history are all verified. No pre-identity replay or downtime credit; observation cannot clear STOP/restart provenance or authorize an otherwise forbidden restart.
- Wake a legitimate transaction-evidence startup wait through the existing event path. Preserve hardware backoff, successful FAST 1p/6 A starts across all three PV profiles, response/phase locks, explicit OFF and late-ACK/generation fences.
- Include complete English specifications and the approved Q1/Q2/Q3 decisions. The consolidated V3 remains an overall draft.

## Economic policy retained

Count the entire measured positive import only while it exceeds half the currently verified executable minimum. The threshold remains 50 Wh; each source/acceptance gap is at most 10 seconds and reset requires strictly more than 15 supported seconds at/below threshold. Persist exact transaction-bound energy and STOP reference. Restart additionally requires supported OFF response, all normal eligibility gates and strictly more than 100 W modeled improvement; observation or time alone grants no restart.

## Accepted uncertainty and physical-evidence limits

Before a usable transaction/causal evidence exists, permission may remain ON and electrically legal retained current may operate. Its duration is not guaranteed bounded; no active 50 Wh monitoring/enforcement is claimed for that unattributed interval, and no valid transaction-bound zero can be fabricated. Missing evidence alone does not introduce automatic permission-OFF, mandatory pre-plug zero, a deadline or a new allowance.

Reported OCPP state is not independent proof of fresh GPIO/active CP. OFF-state independent absence/CP evidence and recovery while still connected after a CP-blocked pause remain limited by the current station interface. Use unknown/awaiting or clearly unverified last-known display where evidence is unavailable. A nominal periodic status refresh is not a guaranteed valid physical observation. Station firmware/probe/CP/phase behavior is unchanged and not hardware accepted by this release candidate.

## Software verification

Prior candidate validation passed two complete Python suites of **2667 tests** each, **992 focused regressions**, and **145 frontend tests**, plus Ruff lint/format, JavaScript syntax, JSON/manifest, translation/resource and whitespace checks. A further **17 deterministic asynchronous interleaving scenarios** and the unchanged 49-case startup module passed together (**66 tests**). Focused counts overlap the full suite. Existing Phase B assertions were unchanged by Phase C, which adds 57 retained-current cases. Tests use temporary Home Assistant fixtures and local simulated OCPP peers; they do not establish hardware acceptance.

Publication is gated on fresh complete local validation and GitHub CI for the final release commit. Five existing third-party dependency deprecation warnings are known; a new mandatory test failure stops publication.

## Startup observation retained for hardware monitoring

One earlier complete-suite execution of `tests/test_pv_beta2.py::test_start_timer_fences_queued_command_and_minimum_stop_deadline` expected charging after the configured 10-second startup delay but received `OFF_SELECTED`. That failing execution did not capture enough causal detail to establish why. Subsequent fresh-process runs, hundreds of independent fixture executions, neighboring startup comparisons, two complete suites and all continuous-eligibility deterministic interleavings passed. Deliberately absent or replayed physical evidence correctly selected OFF.

No production defect or fixture defect was demonstrated, no root cause was established, and no code correction was made. The single non-reproducible startup-test observation remains unresolved and must be monitored during controlled hardware testing. Recovery by replugging or repeated ON is not guaranteed. The user accepted this residual uncertainty for the controlled beta test; this acceptance is not a waiver for new failures or a relaxation of the established electrical, permission, physical-response, 50 Wh STOP or restart protections.

## Controlled installation and hardware evaluation

For separately authorized controlled hardware testing after publication, select **v0.3.2-beta.15** through HACS prerelease versions, restart HA as required and verify installed integration version **0.3.2-beta.15**. Do not use the readiness ZIP as an integration asset or copy files directly into HA. HACS download and live hardware behavior remain pending validation.

Focus on ON without vehicle, real Available/Idle evidence, retained legal current and real transaction ordering, profile takeover, all three FAST minimum starts, measured 50 Wh/zero response with permission retained, CP probe/unplug, authority/phase protection, and restart/reload/reconnect without new energy allowance. Preserve source/receipt and command/ACK/readback/actual-response distinctions.

The intended publication targets develop only. Main and the latest stable **v0.3.1** remain unchanged; mark beta.15 as a prerelease and do not mark it Latest.
