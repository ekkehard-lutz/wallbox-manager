# Wallbox Manager – Decision History and Supersession Register (4–16 October 2026)

**Purpose:** Historical traceability for the consolidated requirements baseline V3; this is **INFORMATIVE / NON-NORMATIVE**, rather than a second specification. The original record refers to the 619-page PDF printout of the linked discussion uploaded on 13 October 2026. Page numbers refer to that printout. The sequence follows **later explicit user decisions**, rather than interim proposals or Codex reports alone. The separately sourced 15 October entry records an individually approved decision and does not approve the overall V3 draft.

## Decisions in chronological order

| Date / PDF pages | Subject | Final position reached in the discussion | Superseded / do not adopt |
|---|---|---|---|
| 4 October, pp. 1–19 | Introduction of `PV_OPTIMUM`, three SoC/consumption parameters, capacity | Lower/upper SoC, estimated 24-hour household demand excluding EV, configurable maximum battery discharge; **separately**, nominal storage capacity from a Wh sensor for percentage calculations | SoC percentage alone as a substitute for stored energy; SunSpec nameplate MaxDisChaRte as the actual discharge-power limit |
| 4–5 October, pp. 19–40 | Reuse; FAST regulation | Shared power components; controlled increase, immediate actual reduction; rapid observation (FAST), 3 s import response, planning period 300 s, except where superseded later | Three copied regulators; separate battery Modbus control |
| 5 October, pp. 44–52 | Solcast forecast freshness | Remaining-day forecast as its own 900 s freshness class; ordinary sources remain live; no WBM-owned Solcast API requests | 90 s live freshness for slowly updated forecast |
| 5 October, pp. 67–90 | Premature 0 A pauses and phase lockout | With a temporarily small economic budget, FAST should initially retain the technically possible **positive** point; 0 A causes a real station-owned charging-reenable lockout. Separate desired and actually executable operating points | Zero current solely due to brief startup measurement variation; nonbinding planned phase position instead of confirmed state |
| 6 October, pp. 92–99 | Night operation / SoC | FAST is **not** tied to current PV production; SoC policy decides. FAST starts at night are allowed with sufficient SoC | “Nighttime always requires PV_BALANCE/STOP” |
| 6 October, pp. 98–119 | Day start, end conditions, enable, diagnostics | PV day starts only at **first PV > household consumption excluding EV**; remaining demand until midnight; `FINISHED` at remaining PV <= remaining household demand, daily latch; enable reconciliation; diagnostics 0–3 | 100 W PV for 5 minutes; sunset minus 1 h as final horizon; repeated enable clicks as normal operation |
| 7 October, pp. 132–148 | Shared SoC state machine | `R+H <= T <=100-H`, `L=T-H/2`, `M=T+H/2`, `U=T+H`, state/start matrix; `PV_MAXIMUM` with `T=R+H`; freely configurable `PV_SURPLUS`; dynamic `PV_OPTIMUM`. PV_SURPLUS may also use battery discharge above U | Separate buffer parameter, early STOP at SoC < T, three SoC regulators, special FAST prohibition for PV_SURPLUS |
| 8–9 October, pp. 209–246, 266–320, 401–449 | Oscillation, measurement generations, beta.9 regulators | Different actual sensor acquisition times, ACK ≠ physical EV response; conservative budget; no duplicate use of a generation; ordinary upward ramp with response control, fast reduction; closed-loop simulation | Upward ramp from reused HA reports; using only `requested_w` as baseline; blind trust in PV/EV/grid values reported together |
| 9 October, pp. 320–410 | Measurement acquisition / Wallbox-Stationary | Trial OCPP MeterValues change from 10 s to 2 s; separate analysis of physical Fronius time windows. WBM must not assume asynchronous sources are synchronous merely because EV values arrive faster | Confusing measurement speed with physical simultaneity |
| 9 October, pp. 441–451 | Development/deployment | Codex tasks in English; GitHub **prerelease** and installation through HACS, **no direct installation**; protect `main`/stable | “Simply overwrite files directly in HA”; burdensome mandatory backup ceremony |
| 9 October, pp. 460–476 | beta.9/10 startup problem and headroom | Startup/response checks must not permanently prevent actual minimum startup conditions. Underutilization of the configured 3,500 W during stable charging was explicitly tolerated | Unconditional requirement to use 100% of the economic power limit |
| 10 October, pp. 477–480 | Phase lockout | 3p→1p `phase_switch_lockout` must not itself cause OFF; continue at the currently reachable minimum, retain intended phase separately; 50% grid-import threshold derived dynamically from M | STOP solely due to blocked phase switching; fixed 1p assumption during 3p lockout |
| 11 October, pp. 485–528 | beta.11/12 findings | D1: current/voltage quantization stable; D2: distinguish SoC protection, economic STOP and OFF status; D3: preserve phase-probe deadline across parameter changes; D4: missing authority has status priority | `stopped_battery_soc` as generic cause for every STOP; delayed “measurements missing” as the primary diagnosis under Local |
| 11 October, pp. 529–545 | pv5: load steps, economic STOP | **Retain** fast power reduction. Economic **zero-current shutdown** only after **50 Wh of actual total grid import during periods exceeding M/2**; strictly >15 s below/equal threshold to reset | Instantaneous value alone as economic STOP; fixed 30 s delay; summing only power above M/2; additional parallel rule |
| 11–12 October, pp. 566–575 | Architecture audit, five approvals | Economic minimum-point exception; unified 50 Wh for all PV profiles; measurement gaps at most 10 s; model-based restart; persistent economic STOP history with unambiguous scope | Retaining old `hard_budget_pause`/10 s/90 s economic veto paths; adding 50 Wh only as an afterthought |
| 12 October, pp. 575–583 | Profile availability and equivalence | Battery-free SURPLUS exclusively grid import/export plus EV; battery modes use shared regulator with different T; SURPLUS == MAXIMUM at equal T and otherwise identical configuration | Separate PV/household-load entity as an additional battery-free requirement; “PV_MAXIMUM has entirely different regulation” |
| 12 October, pp. 595–600 | Restart, Codex approval | State comparison using existing H/SoC/battery model; no new mandatory battery sensor; 100 W deadband; only after supported reset and **material** improvement; existing startup/safety fences remain | Restart through elapsed time or budget reset alone; PV-only requirement even with battery; unjustified timer |
| 12 October, pp. 605–613 | Implementation/review | Concrete restart comparison, 50 Wh integration rule, session persistence and late ACK/OFF race; verified implementation details in `docs/pv-grid-import-policy-design.md`; final regressions | Purported safety guarantee from 50 Wh; old UI text and acceptance tests for disabled economic timers |
| 12 October, pp. 615–619 | beta.13 publication | Release `v0.3.2-beta.13` prepared on `develop`, `main`/v0.3.1 unchanged; 2,420 Python tests and 145 frontend tests. Hardware validation **only after release** | Automatically equating passing test suites with real regulation quality |
| 15 October 2026, separate explicit user approval; no PDF page | Charging permission, connection readiness, physical CP observability and recovery | New APPROVED PV-OCPP-04; permission independent of transaction-bound preparation; retained legal startup current permitted; evidence-based physical status and recovery; safeguards retained | General user permission pending until a transaction-bound economic point is prepared; see precise boundary below |
| 15 October 2026, subsequent explicit user approval; no PDF page | Q1–Q3 accounting, uncertainty and OFF display decisions | APPROVED prospective real-scope observational accounting; accepted diagnosed unattributed flow without bounded enforcement; independent physical display under OFF when valid evidence exists | Delayed pre-identity replay is not approved; missing evidence alone authorizes no automatic permission-OFF fallback; OFF alone proves neither absence nor CP observability |

## Main decision chains – where an initially plausible draft was explicitly replaced

### A. PV_OPTIMUM end of day

The discussion began with first PV production (100 W) and sunset. Sunset minus 1 h was then briefly discussed as an alternative to reach upper SoC more reliably. That alternative **did not** become the final rule: the final comparison is remaining Solcast daily energy against linear remaining 24-hour household demand until midnight; `DYNAMIC` ends through a `FINISHED` latch. Evening reserve is protected through the **dynamic target SoC**, rather than additional sunset switching times.

### B. SoC STOP threshold

Initially SoC < T was treated as STOP; in view of brief household-load variations, ending at T−H/2 was discussed. Shared L/M/U thresholds for all battery profiles were adopted. **Reintroducing STOP at SoC < T later without a change decision would be a regression.** The historical T-versus-L conflict remains an explicit review concern wherever older sources still disagree; this translation does not claim those residual contradictions are resolved.

### C. OCPP zero current and lockout

0 A is a real interruption and can trigger a hardware charging-reenable lockout of 600 s; short-term power estimates should therefore not cause it. Continuing at a **technically proven minimum** during phase lockout was explicitly approved. The economic end of that minimum charging was subsequently standardized through the 50 Wh rule. An earlier “50% instantaneous import tolerance with a 10 s STOP” is **SUPERSEDED** as a separate economic STOP condition.

### D. Battery limit: hard or economic?

In the beta.9 context, the conservative EV hard cap was developed to prevent impermissibly high targets. A later audit recognized **economic** immediate STOP at the minimum as unnecessary (e.g. 4,033.94 W compared with a technically possible 4,121.19 W point). The **only explicitly permitted exception** is exactly the current technical minimum; it must not violate actual physical hard limits. Above that minimum, ordinary budget limits remain unchanged.

### E. Economic STOP

Initially there was a 90 s BALANCE/SURPLUS deficit STOP, a separate 10 s import STOP at the minimum and budget-related immediate OFF paths. The discussion deliberately **did not append another 50 Wh gate**; it **removed/replaced** these overlapping economic STOPs. The old `pv_stop_delay` may remain for migration purposes, but no longer controls economic OFF. The precisely defined integration/reset logic is documented in the tagged `docs/pv-grid-import-policy-design.md`.

### F. Restart after 50 Wh

Budget reset does not equal restart permission. The later decision requires >15 s valid below-threshold evidence plus new EV/site evidence, OFF response, profile-dependent eligibility, technical executability and demonstrably **more than 100 W** modeled improvement compared with the previous STOP. Battery participation is explicitly allowed. A pure PV-surplus test would incorrectly restrict PV_MAXIMUM and PV_OPTIMUM.

### G. Temporal regulation and oscillation

Slow increase, fast reduction and response verification were **already agreed**. Beta.9 specified measurement generations and EV response in detail, especially in FAST. Beta.12 also corrected current/voltage quantization. Later beta.13 oscillation in the `PV_BALANCE` path therefore must **not** be “solved” by reinventing every timing/measurement rule. Next step recorded in the original reconciliation: compare the actual path against valid requirements and `pv6`.

## Open issues for which the history provides no final new norm

1. A quantitative acceptance criterion for `PV_BALANCE` oscillation (e.g. tolerated current reversals, settling time after a load step) has **not** been explicitly approved.
2. Whether the hours-long FAST 9 A behavior in `pv6` is an unnecessary block or an intentional/permitted headroom reserve is **OPEN**. Earlier acceptance of conservative reserves conflicts with a blanket “use exactly D” requirement.
3. Exact physical simultaneity of Fronius/BYD/EV source values **cannot be guaranteed** from the visible discussion, repository reports and HA timestamps.
4. The PDF printout contains many references to local Codex reports and historical test logs; these require separate inspection for a subsequent **code/root-cause assessment**, unless available as tagged GitHub documents.

## Previously agreed development procedure

The discussion contains repeated explicit process decisions: **Codex tasks always in English** (p. 405), Codex as an execution tool rather than an autonomous architect (p. 1), GitHub prerelease/HACS rather than direct HA file installation (pp. 448–451), and no disproportionate backup/release ceremony during development (pp. 441–444). These rules must remain visible for later iterations, but organizationally belong outside the mathematical regulator section.

## Recommended use

This history belongs to consolidated V3 as a **NON-NORMATIVE appendix / decision journal**. Keep target specification and history separate so an explicitly rejected old proposal cannot become active again through copy/paste. The original reconciliation made further code work conditional on V3 approval and a documented target/actual comparison against `pv6`. The present documentation decision does not authorize implementation, release, deployment or hardware tests.

## 15 October 2026 – Charging permission, connection readiness and recovery [APPROVED]

**Source:** Explicit user-approved documentation task dated 15 October 2026, subsequent to the 619-page discussion printout. No PDF page attribution is invented. Normative content of this individual decision is recorded as [PV-OCPP-04](pv-regulation.md#pv-ocpp-04--charging-permission-connection-readiness-and-physical-cp-observability-approved). This history remains INFORMATIVE / NON-NORMATIVE; consolidated V3 remains DRAFT pending separate overall sign-off.

**Newly approved:**

- Explicit user ON must establish `ChargingEnabled=true` within existing authority/ownership rules even with no attached vehicle, no active transaction and no prepared transaction-bound profile. General permission is separate from actual economic charging; no repeated ON after plug-in is required.
- A retained electrically legal positive current may temporarily operate immediately after connection. The manager takes over the selected profile promptly once valid transaction identity, authority, usable measurements and current capabilities are available. Missing prerequisites are diagnosed; no unconditional one-cycle correction or fabricated confirmation is promised.
- Physical vehicle absence requires station confirmation, rather than inference from missing transaction data. Observable-CP, connected-OCPP, vehicle-absent standby is a normal live Available/Idle state with no grey styling.
- During connected-vehicle pauses, the applicable verified transaction-bound 0 A point retains general permission; the station owns CP interruption. CP-blocked status may remain greyed last-known physical status and must not falsely claim unplugged.
- Reload/restart/reconnection requires reconstruction using existing supported OCPP readbacks/events. Before physical confirmation, use unknown/pending or last-known-but-unverified presentation. Recovered permission, physical state, transaction and operating point are separate facts.

**Explicitly retained:**

- `wallbox-stationary` ownership of CP, physical connection detection and phase relays; station-side debounce/evidence rules; existing CP probe interval **3600 s** and duration **2 s**, without assuming INI configurability.
- Existing guarded atomic 1p↔3p sequence: target 0 A, CP block, meter-confirmed reduction, relay switch, intended-current validation/restoration, safe CP restoration; no interference from ordinary pause handling.
- Electrical current/phase/device limits; transaction-bound positive/zero `TxProfile` validation; serialized commands, stale fencing, delayed-ACK handling, confirmed operating points and actual physical-response/freshness gates.
- Unified measured **50 Wh** policy, strictly **>15 s** supported reset, transaction-scoped persistence, economic STOP/model-based restart inhibition and positive-to-zero station reenable lockout.
- Explicit OFF intent, ownership/authority, no automatic Local→Remote takeover, session/lifecycle protection and no fictitious transaction/accounting scope.

**Precisely superseded assumption:** General user permission must remain pending until a transaction-bound economic operating point is prepared. Only this ordering dependency is superseded. Valid transaction identity remains mandatory before positive or 0 A `TxProfile` commands. This is not a general removal of safety or recovery checks, nor approval of automatic positive restart from uncertain or exhausted history.

**Unresolved boundaries / future implementation review:** Permission ON creates no accounting scope or reset. Existing command/response-based accounting does not fully specify measured retained-current startup before valid transaction identity or manager-command evidence; causal attribution requires review without backfilled intervals or a newly invented policy. A later source inspection on 15 October 2026 verified existing station-initiated periodic physical refresh on `wallbox-stationary/main` at `2baf0daa8c446dad7570e5daa5f0f1cba486e915`: nominal 60 seconds, fresh hardware evidence only when its preconditions hold, repeated Available/Occupied and, for a valid active transaction, Updated/MeterValuePeriodic Charging/EVConnected reports. See [section 6.4.8](pv-regulation.md#648-state-reconstruction-after-restart-or-reconnection) for source locations and gates. Complete request-driven vehicle/charging/CP readback remains unsupported by the inspected Device Model, and `TriggerMessage` remains unsupported. No-transaction occupancy reports do not establish every charging state. `GetCompositeSchedule` is not assumed valid without an active transaction. The physical wallbox's installed revision and actual periodic-reporting/recovery behavior remain unverified; source inspection is not hardware acceptance. The earlier physical-status investigation is preserved as evidence of its older inspected revision. This clarification adds no requirement or functional behavior. Until supporting evidence exists, recovery and dashboard acceptance remain pending rather than falsely confirmed.

**Subsequent resolution:** The accounting/uncertainty questions described as unresolved in the initial entry above were later resolved by the [Q1–Q3 approvals](#15-october-2026--subsequent-q1-q2-and-q3-decisions-approved). That initial wording remains historical; station-interface evidence limits and hardware validation remain outstanding.

**Scope and acceptance:** Documentation only. The [acceptance matrix](pv-regulation.md#6410-acceptance-matrix-approved-decision-verification-pending) separates requested action, acknowledgement, confirmed state, physical observation and dashboard presentation. No production behavior, F1/F2 candidate, tests, version, release, deployment or hardware acceptance was changed or completed by recording this decision.

## 15 October 2026 – Subsequent Q1, Q2 and Q3 decisions [APPROVED]

**Source and sequence:** The user explicitly approved all three decisions after the PV-OCPP-04 implementation-readiness analysis and after the initial permission/source-clarification entry above. The available analysis was `/tmp/pv-ocpp04-readiness-20261015/analysis-report.md`, especially sections 7–11; it is an analysis artifact, not implementation or historical PDF evidence. These subsequent decisions have no page attribution in the older 619-page discussion printout. This journal remains INFORMATIVE / NON-NORMATIVE; the approved target behavior is integrated into [PV-OCPP-04 section 6.4.4](pv-regulation.md#644-transaction-bound-control-and-economic-accounting) and [section 6.4.9](pv-regulation.md#649-dashboard-presentation-and-physical-evidence). Overall V3 remains **DRAFT**; Q1, Q2 and Q3 are individually **APPROVED**. No implementation, station change or deployment is authorized by recording them.

### Q1 – Prospective real-transaction-scoped observational accounting [APPROVED]

**Decision:** For charging that begins with a retained electrically valid positive station current and no preceding Wallbox Manager positive TxProfile command, use prospective, real-transaction-scoped observational accounting activation.

Eligibility requires a valid real transaction identity/scope, valid ownership/authority, fresh physically measured EV charging activity, verified currently executable minimum `M`, actual valid grid-import measurements, qualifying source/acceptance timestamps, and reconciled applicable persisted economic history. The first counted interval must be fully supported from its start; do not count preceding unsupported intervals or retrospectively attribute earlier charging merely because identity became known later. No general pre-identity buffering/replay mechanism is approved.

Retain exactly the measured **50 Wh** policy: the complete measured positive import counts only for `P_import > M/2`; source and acceptance gaps are each at most **10 seconds**; reset requires **strictly more than 15 seconds** of supported `P_import <= M/2`. No integration/reset credit through unknown gaps or downtime, fabricated scope/intervals, budget/STOP reset or transfer, new energy allowance/timer, or change to the existing restart model is approved.

Observed physical charging, an active economic interval, an authorized positive manager command and an approved economic restart remain distinct. Observational activation must not invoke a restart-confirmation mechanism that clears valid STOP provenance. A genuinely ended transaction followed by a proven new transaction follows existing scope lifecycle rules; a changed ID during uncertain continuous activity cannot silently reset exhaustion.

### Q2 – Accepted temporary unattributed physical charging [APPROVED]

**Decision:** A temporarily unattributed physical charging interval is accepted while transaction or causal accounting evidence is unavailable; confirmed general charging permission may remain ON, and no automatic permission-OFF fallback is authorized solely for that missing evidence.

Do not require mandatory pre-plug zero, invent identity/scope, claim active 50 Wh monitoring during the unattributed interval, or issue automatic positive manager commands when existing economic recovery inhibits them. Diagnose the missing transaction, attribution or measurement evidence. Begin prospective accounting when Q1 is satisfied and selected-profile regulation promptly when actual execution prerequisites hold; retain existing economic STOP/restart restrictions and physical safety/authority guarantees.

**Explicit accepted limitation:** Without a usable OCPP transaction, a physically charging station cannot receive a valid transaction-bound 0 A TxProfile from this manager. Retained charging may continue until valid control prerequisites exist or existing protections intervene. **The duration is not guaranteed to be bounded, and enforcement of the 50 Wh command-trigger threshold cannot be claimed during that interval.** This economic uncertainty must remain visible in diagnostics and later hardware acceptance. It creates no general budget and authorizes no new timeout, allowance, alternative control path, CP command, station intervention or emergency permission-OFF policy.

### Q3 – Independent physical status under explicit permission OFF [APPROVED]

**Decision:** Display physical connection and charging state independently of general charging permission whenever genuinely current, independent physical evidence supports that presentation.

With explicit OFF, operational OCPP, independent fresh confirmation of physical absence and independently supported CP observability, normal **Available / Frei** and **Idle / Bereit** may be displayed while permission remains visibly **OFF**. Do not grey those valid physical observations solely because permission is OFF. Without sufficient independent evidence, show unknown/awaiting observation or clearly identified last-known, currently unverified state. OFF, missing transaction/meter values and an old Available event prove neither absence nor CP observability. Existing dashboard cases A–F remain in force.

**Remaining implementation/interface question:** The reviewed periodic station reader's disabled-permission gates can prevent fresh OFF-state observations. Removing them or adding station-side readback is not approved here; any enhancement needs a demonstrated concrete requirement and separate implementation authorization. Actual deployment and hardware evidence remain unverified.

**Acceptance and retained scope:** The existing [23 scenarios](pv-regulation.md#6410-acceptance-matrix-approved-decision-verification-pending) retain their numbering; rows 3–7, 15, 19 and 22 are clarified for these decisions. Electrical/economic/CP constants, L/M/U SoC behavior, FAST/BALANCE/F1/F2 protections, station CP ownership, guarded phase switching, authority and transaction fencing remain unchanged. This entry records approved policy, not completed software or hardware acceptance.

## 16 October 2026 – Event-fed inputs and clocked regulation [APPROVED]

**Source and scope:** Explicit user decision of 16 October 2026, following observations that WBM-enabled HA Core CPU is materially higher even without control takeover, and that disabling WBM returns it to baseline. The CPU observations implicate WBM but do not identify the root cause. This individually approved architecture decision is **not** taken from the earlier 619-page PDF and **does not** approve all V3. Its normative requirements are [PV-ARCH-05–08](pv-regulation.md#pv-arch-05--two-event-fed-input-interfaces-and-one-observation-snapshot-approved).

**Approved architecture:** Exactly two incoming sources — OCPP wallbox reports and internal HA state entities. Initialize and resynchronize supported state at start/reconnect; maintain current observations from incoming data without starting regulation on each event. A single configurable PV regulation tick (illustratively 5 seconds) reads a consistent snapshot, applies the already approved policy, optional configured smoothing and guarded command submission. Normal sensor, profile and unplugging events wait until the next regulation tick. Inactive/observation-only WBM does not continually run the PV controller; no second per-event PV recalculation path.

**Immediate exceptions and retained rules:** Revoked authority, OCPP disconnect and critical safety/hardware faults invalidate execution promptly. OCPP responses, stale-command fences, physical readback and confirmed operating points remain independent of the PV tick. Ping/Pong proves transport liveness, not measurement freshness. No relaxing of distinct measurement-generation and <=10 s 50 Wh evidence requirements, >15 s reset, SoC thresholds, FAST/BALANCE behavior, 300 s forecast planning, phase switching/lockouts, physical evidence, CP ownership, permission, persistence or restart inhibition. Repeated cached values at successive ticks are not new source readings. An earlier allowance to recompute after individual measurement events does not license a competing ordinary PV regulator.

**Implementation comparison:** The existing working [wallbox-stationary regulator at 6edf3b0a8b46bc1a1f2c34f1e986c179915dd2f0](https://github.com/ekkehard-lutz/wallbox-stationary/blob/6edf3b0a8b46bc1a1f2c34f1e986c179915dd2f0/services/dynamic_amps.py) uses T_TAKT, a current snapshot, short history and conditional target application. Its inverter values are still fetched in the loop and its HA forecast is polled. It is therefore a **timed-regulator reference**, not evidence of event-fed source acquisition and not a replacement for WBM's verified dynamic 1p↔3p phase behavior.

**Process:** Profile first; inspect listeners, timers, publications and background computations; consolidate by removing obsolete code, not adding duplicate layers. Prove behavior with no-authority idle CPU comparisons, one-decision-per-tick checks, exact economic/safety regressions and immediate loss-of-authority/disconnect tests. No code edit, release, merge, deployment or station-firmware action is authorized merely by this documentation decision.
