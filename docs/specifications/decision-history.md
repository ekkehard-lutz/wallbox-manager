**24 October 2026 — approved supersession:** Automatic wake-up configuration moves entirely to Stationary local INI, with no WBM AutoWakeUpEnabled OCPP variable or HA switch. Hardware ChargingEnabled remains an independent whole-wallbox switch accessible from the HA device page without Remote current/phase authority. A new manager-owned `charge_release` gates current/phase dispatch and becomes the single Lovelace action. NETZ's standalone SET is replaced by the three-state action; PV profiles eventually use Start/Stop even when computing 0 A. See [normative specification](charge-release.md). This is a documentation-only decision.

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

## 16 October 2026 – Smoothing units and approximate 50 Wh trigger [APPROVED]

**Source:** Explicit user follow-up of 16 October 2026, referencing the actual WBM options screenshot: regulation interval 5 seconds, power smoothing window 5 seconds. The user proposed smoothing as a **number of regulation cycles**, and accepted a nominal 50 Wh economic intervention being physically detected only after e.g. 60, 80 or 100 Wh. These changes are separately approved target requirements, not a claim about the current implementation or complete V3 approval.

**Smoothing decision:** [PV-BAL-03](pv-regulation.md#pv-bal-03--freshness-and-smoothing-approved-revised-16-october-2026) now specifies **N recent regulation ticks**; N=1 is unsmoothed, N=3 with T=5 s gives approximately 15 s of history. The configured regulation interval remains in seconds and separate. Keep smoothing limited to PV and household/consumer power. Existing seconds-based saved options require a version-aware migration (never silently map 5 seconds to 5 ticks).

**50 Wh decision:** [PV-ECO-09](pv-regulation.md#pv-eco-09--approximate-50-wh-economic-intervention-not-exact-metering-approved-16-october-2026) replaces exact numeric Wh/Ws accounting as a requirement with a simple approximate estimate on regulation ticks. The **nominal 50 Wh** threshold still initiates economic OFF promptly when detected; it is not a guaranteed hard cap on physical grid energy. Latency or discretization may result in higher actual consumption, which is accepted. Preserve M/2 comparison, transaction/authority-specific applicability, scope/history, >15 s reset, safety/OCPP confirmations and economic restart conditions.

**Unresolved separate validity question:** The prior **<=10 s distinct-source and acceptance-time gap** contract in PV-ECO-05 remains approved and is not silently removed by acceptance of approximate math. It may be incompatible with event-only, last-value-held sources, even when their reported values reliably represent changes. Clarify this in a separate explicit requirements decision before changing the corresponding validation logic. No extrapolation through unknown/downtime gaps is authorized.

**Scope:** Documentation only. No code, version, release, merge, deployment or wallbox firmware changes.

## 16 October 2026 – Remove 10-second metering gap rule and separate profile availability [APPROVED]

**Source:** Explicit later user decision of 16 October 2026. The previously approved **10-second source/acceptance report-gap rule is removed** because sensor inputs are assumed to reliably announce value changes: 30 seconds or longer without a message means **unchanged**, not unavailable. The user also confirmed that dependent PV profiles require their relevant Fronius/smart-meter/BYD inputs, while **NETZ remains independently possible** with a working wallbox connection. Normative requirements: revised [PV-ECO-05](pv-regulation.md#pv-eco-05--last-value-held-validity-and-causality-approved-revised-16-october-2026), clarified [PV-ARCH-03](pv-regulation.md#pv-arch-03--profiles-with-battery-and-forecast-approved).

**Supersession:** The old distinct-report-at-most-10s source/acceptance rule and the earlier 16 October open compatibility question are now **HISTORICAL / SUPERSEDED**. The earlier 15 October Q1 reference is preserved as history, not an active parallel requirement. Valid unsmoothed grid-import values are held and used on successive ticks for the approximate nominal 50 Wh budget and strictly >15 s supported reset, even when unchanged for 30 s or longer. No fictitious fresh measurement is asserted and no event-triggered second PV regulator is introduced.

**Real failure/unknown:** HA may mark values `unknown` or `unavailable` because of device, Modbus, network or integration issues, not only permanent hardware failure. During **known** invalid values and WBM/HA downtime, do not invent measurement values, backfill energy or reset budget; retain transaction/authority-bound provenance and economic recovery constraints. This availability rule concerns ordinary metering, not proof of commanded physical transitions or verified control permission/authority.

**Profile independence:** Unavailable mandatory measurements prevent the **dependent PV profile** from making new PV control decisions. A battery failure does not in itself prohibit separately configured battery-free PV_SURPLUS when grid import/export and EV input remain available. **NETZ does not require Fronius/BYD HA sensor values** but still requires applicable OCPP connection, permission, authority, verified capabilities and explicit profile selection. No implicit PV-to-NETZ fallback or newly invented emergency permission-OFF is authorized.

**Status:** Documentation decision only; no implementation, tests, version, release, deploy or wallbox firmware changes. Reliable change-report semantics are an explicit project assumption to be verified for the installed sources.

## 16 October 2026 – Wallbox status through CP pause and full reconnect bootstrap [APPROVED]

**Source:** Subsequent explicit user decision of 16 October 2026, recorded as individually approved requirements [PV-OCPP-05](pv-regulation.md#pv-ocpp-05--retain-wallbox-reported-connection-and-charging-status-during-cp-pause-approved-16-october-2026) and [PV-OCPP-06](pv-regulation.md#pv-ocpp-06--initial-synchronization-of-all-supported-state-on-every-ocpp-reconnect-approved-16-october-2026).

**CP pause/reporting:** The WBM uses the **most recent wallbox-reported connector and charging status** instead of layering a CP observability timeout/revalidation algorithm over it. During a CP-blocked pause, an unplugged car may continue to appear connected until the station's existing nominal **3600 s / 2 s** CP probe detects disconnection and sends a new OCPP report. This delay is explicitly accepted. The prior PV-OCPP-04 case-C grey/stale display requirement is **superseded**, while station-owned physical CP safety, verified command responses, transaction and authority rules are unchanged.

**New connection baseline:** After `wallbox-stationary` restart, wallbox reboot, OCPP reconnect or HA/WBM reload/restart, a **complete new-generation initial snapshot** of supported known measurements and states must be transmitted/read, **even if values are not changing** (including L2/L3 continuously at 0 A). A new connection cannot silently inherit old current values from the former session. Prefer station-initiated immediate OCPP reports plus supported explicit readback, with unknown/unsupported fields marked accordingly. During CP block the station may only know its own last reported connector status; this is a reported state, not newly confirmed physical sensing. Existing transaction, authority, phase and applied-point confirmations still gate relevant commands.

**Current interface gap:** `wallbox-stationary/main` at [`2baf0daa`](https://github.com/ekkehard-lutz/wallbox-stationary/blob/2baf0daa8c446dad7570e5daa5f0f1cba486e915/ocpp_runtime/station.py) resets and sends connector status after registration but delays its first meter report and can skip physical-status refresh under CP block. A complete immediate replay is **not proven** for all desired fields and needs separately approved interface work or supported readback. OCPP reconnect is not by itself a guarantee of full telemetry replay.

**Supersession and scope:** Later CP-display requirements replace the conflicting 15 October stale/grey status interpretation only; no implicit Local-to-Remote takeover, unsafe positive restart, invented transaction, economic STOP reset or physical phase/current proof. Documentation only; no implementation, release, merge or deployment.

## 16 October 2026 – WBM initiates the OCPP connection bootstrap [APPROVED]

**Source:** Explicit user follow-up asking whether WBM detects connect/reconnect and deciding that WBM itself should initiate initial synchronization, including after WBM restart. This clarifies, rather than removes, the full supported-state baseline requirement of [PV-OCPP-06](pv-regulation.md#pv-ocpp-06--initial-synchronization-of-all-supported-state-on-every-ocpp-reconnect-approved-16-october-2026).

**Connection ownership:** `wallbox-stationary` connects as the WebSocket/OCPP client to the WBM's configured CSMS listener. Every new accepted socket reaches `CentralSystem.on_connect()` in WBM and creates a fresh connection token; `BootNotification` establishes its own boot generation. After the boot response, WBM already initiates read-only `GetBaseReport` discovery and can extend that lifecycle with a **single, generation-scoped complete initialization coordinator**. On WBM restart the server listener is recreated and the station's own reconnect loop establishes a **new connection**, so the same bootstrap path applies. Retries depend on normal connection availability and the station's backoff/rejection behavior, not on WBM reaching out to the station's URL.

**How information arrives:** WBM initiates supported requests and tracks completeness. `GetBaseReport` is capability inventory, **not meter telemetry**. For push-only states/metering, WBM must coordinate reliable initial reports from the station, using a verified supported request/trigger **only if available**, otherwise a station post-registration initial publication. The existing `wallbox-stationary` implementation does not establish a complete immediate meter/charging-state snapshot or `TriggerMessage` support. A separate station interface modification may be necessary and needs distinct authorization. This is a functional contract, not a claim of existing implementation.

**Fencing:** Never treat previous OCPP connection values as current new-generation data; no duplicate bootstraps, no old-session ACK acceptance, no implicit authority takeover or economic positive restart. Each field is pending/ready/unsupported independently, and normal changed-only operation begins after its current-generation initial observation. No periodic reinitialization during normal operation; request readback only where the interface actually supports it.

**Status:** Documentation change only; no WBM/station code, deployment, merge, tag or release.

## 17 October 2026 – Clocked PV target across OCPP disconnect [APPROVED]

**Decision:** While an already authorized automatic PV profile remains selected through a temporary OCPP communication loss, the configured regulator cadence can continue producing a **provisional energetic target** from still-valid HA inputs; this is not an executable wallbox operating point and does not manufacture absent EV/meter, transaction, phase or other physical evidence. During the outage, **no OCPP control commands are sent**, and there is **no command/setpoint queue**. WBM uses its existing connection/BootNotification initialization (PV-OCPP-06) to re-establish current-generation status and technical prerequisites. Only the **next ordinary regulation tick after readiness** determines a fresh executable point and may send it through existing checks. Do not replay a stored pre-disconnect command or add a reconnect-triggered policy loop, extra timer, or implicit authority takeover. Explicit user OFF/Local takeover and safety revocation remain authoritative; persisted 50 Wh STOP/restart history is unchanged, and outage intervals earn no economic credit.

**Requirement:** [PV-ARCH-09](pv-regulation.md#pv-arch-09--clocked-pv-target-during-temporary-ocpp-disconnect-no-deferred-command-queue-approved-17-october-2026). This clarification permits provisional computation across **temporary communication loss** only and does not supersede the ordinary rule of no cyclic PV policy in passive Local/unauthorized operation. It also does not claim that a complete station baseline is currently supported. The consolidated V3 document as a whole remains **DRAFT**.

**Scope:** Documentation only. Current priority remains eliminating avoidable passive Home Assistant CPU load (repeated profile-setting/SoC calculations, broad runtime/entity publications, redundant expiry timers and activity/logbook pressure). No production implementation, test, merge to `develop`, release, deployment, or station modification is authorized by this entry.


## 17 October 2026 – Minimal passive v0.4 restart [APPROVED]

Explicit user decision: architectural restart from minimal production code and a
new focused suite, rather than another refactor. Normative milestone decisions
are [V04-01–12](passive-v0.4.md). One independent entry/device/connector, preserved
public identities/storage/statistics, safely detached legacy subentries and a
recognizable single-instance card. Local settings remain functional; all physical
control is unavailable. Future PV/OCPP/economic/safety decisions remain preserved,
including PV-ARCH-09, without implementing a regulator now. Coordinated multiple
wallbox management belongs to a separate future Energy Manager. No release,
deployment, main/develop change or station modification in this assignment.


## 18 October 2026 – Station safety ownership and no-expiry observations [APPROVED]

**Source:** Explicit user task “Architectural Responsibility Boundary and No-Expiry
Observations”, based on develop / v0.4.0-beta.3. The independent normative
[WBM-ARCH-01–12 decision](architecture-responsibility.md) takes precedence over
conflicting passive/PV requirements and historical implementation descriptions.
V3 as a whole remains DRAFT. Earlier decisions above are preserved as history.

**Decision:** The station alone enforces physical/electrical safety, current and
phase limits, CP and safe switching. WBM owns optimization, intent, economic and
profile policy, authority and truthful outcomes/display. Its proposals may be
suboptimal with incomplete/old observations; station rejection/delay/adjustment
is normal. No WBM safety proof, age gate or mandatory pre-command physical readback.

All ordinary observed values remain held without expiry. Explicit invalid reports,
connection/boot boundaries and failed reads are different events: failed reads do
not change an observed value. Preserve beta.3 one-invalid/two-invalid phase
robustness, with one coherent canonical value instead of separate display and
safety-proof semantics. Identical permission reads update internal acquisition
metadata without repeated HA publication or public timestamp churn. Five-second
permission polling can detect external changes, never maintain a TTL.

**Explicit supersession:** The normative register names V04-08/10, PV-ARCH-04–09,
PV-PLAN-04 (including 900 s forecast expiry), PV-FAST-03/04, PV-BAL-02/03,
PV-PHASE-01/02, PV-OCPP-01/02/04–06 and section 6.4 acceptance rows, affected
PV-ECO-01/02/05/06/08/09 and PV-QA-01/02 safety/freshness clauses. The beta.3
5 s phase proof, 15 s permission confirmation and expiry callback are removed.
Authority, transaction/intent fencing, economic/SoC policy, transport/response
limits, command sequencing and session/business timers remain independently valid.

**Authorized scope:** Small passive WBM runtime and specification change; full
maintained tests, feature-branch commit/push. No active regulation, station change,
main change, merge, tag, release or deployment. No hardware acceptance is claimed.

## 21 October 2026 – Explicit active OCPP foundation [USER-REQUESTED SCOPE]

The implementation task authorizes the narrow [WBM-CTRL-01–12 successor](active-ocpp-control.md):
explicit station authority acquisition and independent charging permission through
verified writable OCPP 2.1 endpoints. It supersedes passive V04-03/06 only for these
two actions; the passive milestone's historical approval remains intact.
WBM-ARCH-01–12 remain overarching, profile settings remain local, and PV V3 remains
a draft. No operating-point control, wake-up, current/phase preparation, automatic
takeover/retry/replay or deployment is authorized. Commit/push is separate from
independent review, merge, release and user-controlled hardware acceptance.


## 25 October 2026 – Shared PV regulator simplification and Level-2 diagnostic scope [AGREED DESIGN DIRECTION]

**Source:** The user's explicit agreement to the analysis comparing `wallbox-manager` `v0.3.2-beta.18` (`pv_soc.py`, `pv_optimum.py`, `pv_regulators.py`), `wallbox-stationary` (`services/dynamic_amps.py`, `control/amps.py`) and publicly documented openWB grid/battery priority approaches. The user additionally required Level-2 (“relevante Zustandsänderungen”) diagnostics to record **only enough relevant measurements to reconstruct regulation choices**, without a growing raw or per-tick log stream. This history is descriptive; normative changes live in [PV V3](pv-regulation.md#4-fast_discharge--grid-point-led-utilization-25-october-direction) and [PV-DIAG-01–10](pv-decision-diagnostics.md).

**Agreed concept:** Shared SoC state machine with all three battery PV profiles distinguished only by T. The former v0.3.2 target calculations remain a reuse candidate; simplify FAST to primarily **grid-point feedback plus bounded active upward exploration even at 0 W grid**, and BALANCE to **signed battery charge/discharge feedback, SoC recovery near T and an independent grid economic fallback**. Stationary exclusively enforces physical hardware safety. Temporary deviations are tolerable and subsequently corrected. Remove the old user-configurable maximum storage-discharge power `D` from **future** regulator decisions; retain the HA Number identity/persistence until a separately reviewed entity migration. No change to current beta.16 control code.

**Dependencies / OPEN:** The new BALANCE rule requires a *bidirectional* storage power mapping, not just the old positive discharge reading. The historical PV-ECO-08 restart capacity formula includes D and cannot be reused; preserve historical STOP history, strict improvement intent and 50 Wh accounting, but decide a valid non-D restart test before programming active PV regulation. Controller gains/bands and SoC recovery response also need replay/test-driven decisions. Do not silently substitute a grid-only formula that cannot actively explore battery headroom at zero grid exchange.

**Diagnostics decision:** Level 2 logs only significant profile/T/SoC-mode/feasible-point/economic/failed-and-recovered decision transitions and the **subset of measured inputs that caused each decision**, with stable reasons, compact units and no raw OCPP/HA sample stream. Hundreds of unchanged ticks yield zero new PV decision logs. The exact semantic deduplication thresholds and severity policy require implementation review. Level 0–3 existing OCPP diagnostic semantics remain valid.

**Authorization boundary:** Documentation and design direction only; overall V3 remains DRAFT. No Codex implementation, merge, release, HA install, wallbox-stationary edit or hardware execution follows automatically.


## 25 October 2026 – Common FAST/BALANCE ramp and EV settling [AGREED DESIGN]

The user requires one **legal ampere step upwards every three eligible regulation cycles**, preserving the currently confirmed phase series until its endpoint in both directions, even where another phase series offers a closer nominal power point. In 3p/6 A (4,140 W) the next increase is 3p/7 A (4,830 W), skipping overlapping 1p/19–20 A. A real desired reduction below 4,140 W to 3,910 W or less may switch to 1p/17 A. Use nominal 230 V × phases × offered A to evaluate vehicle consumption against **50%** of the offered point. Upward regulation is blocked below this threshold in startup, temporary vehicle unlock (~30 s), balancing or other underconsumption. After an actual 0 A→positive transition the user explicitly wants **ten complete PV regulation cycles** without ordinary changes in either direction; afterward ordinary downward correction may proceed even if measured EV power stays under 50%, while further upward adjustment still requires the 50% and three-cycle tests. During already active positive charging, underconsumption blocks increases but does not prevent required reductions. Explicit user/SoC/authority STOP remains independent. No fixed extra grid deadband was approved; quantized steps and time/response gates should be evaluated first. Preserve the separately established 50 Wh economic STOP unless an explicit overriding rule is agreed. Normative specifications: [PV-STEP/RESP](pv-operating-point-response.md). Documentation only; no code changes.


## 25 October 2026 – Configurable PV tuning and Wh budget [AGREED]

The user requested five HA integration regulation parameters: upward interval default **3 regulation cycles**, 0 A startup hold default **10 regulation cycles**, signed grid correction deadband default **±100 W**, ordinary downward correction delay default **0 regulation cycles** (no extra delay), and shared economic grid-import STOP budget default **50 Wh**. The two obsolete seconds-based PV start/stop delay entities and settings are to be removed **without compatibility retention**, since no user automation depends on them. Never silently migrate seconds to cycles; the new options have distinct identifiers. The economic Wh STOP takes precedence over startup and downward holds, and the configured Wh budget replaces the old fixed 50 Wh constant without weakening M/2 energy eligibility, reset, transaction ownership or restart inhibition. Normative details and migration acceptance criteria: [PV-TUNE-01–10](pv-operating-point-response.md). No executable implementation, merge or release authorized by this documentation.


## 25 October 2026 – BALANCE target deadband and STOP direction [AGREED; matrix OPEN]

User clarified that BALANCE uses Fronius two nonnegative entities, one charge and one discharge, combined as charge minus discharge. Correct EV sign: one legal operating step **below ideal** when SoC < T-H/2 to charge battery; at ideal inside target band; one step **above ideal** when SoC > T+H/2 to discharge gently. Only switch phases upon exhausting current phase series, as for NETZ. BALANCE STOP is requested if (A) positive battery charging is unattainable even at currently feasible minimum positive EV operating point, or (B) SoC strictly drops below **T-H**. At constant T case A should normally prevail; moving PV_OPTIMUM T on Solcast forecast can lead directly to B. This **changes** historical PV-SOC-01/02 STOP boundary `SoC <= T-H/2`, requiring explicit full SoC transition/re-entry matrix revision; do not implement both in parallel. Minimum-point response evidence and restart when criterion A clears remain OPEN. The EV uptake fraction now becomes configurable default 50%, shared by FAST/BALANCE (PV-TUNE-11). Normative contract in [PV-BAL-06](pv-regulation.md). Documentation only.


## 25 October 2026 – Existing SoC STOP restart rule confirmed [AGREED]

After either BALANCE STOP cause (insufficient positive battery recharge at the feasible minimum EV point or SoC strictly below T-H), use the **existing common STOP state**; no additional A-specific restart logic. From STOP, a battery SoC **strictly above T+H/2** permits BALANCE **when proven PV surplus and all common eligibility conditions exist**; at **SoC >= T+H**, FAST may start regardless of PV surplus. The previous SoC matrix's `SoC <= T-H/2` STOP is replaced with strict `SoC < T-H`, and the previous inclusive restart at M is changed to strict `SoC > M`. Dynamic Solcast-driven increases in T recalculate the thresholds. Separate economic import-Wh STOP restart inhibition still applies. The updated normative matrix is [PV-SOC-02](pv-regulation.md). Documentation only.


## 25 October 2026 – BALANCE minimum-current STOP A accepted [APPROVED]

No extra STOP timer or repeated-sample counter: on the next ordinary PV tick, if SoC is **strictly below T-H/2**, the truly applied/physically valid operating point is **1p/6 A**, initial 0 A→positive settling has finished, and measured battery charge minus discharge power is **strictly negative**, BALANCE requests 0 A. Battery power at 0 or positive is not cause A. A mere requested/OCPP-accepted or phase-locked point is not valid proof. The shared SoC STOP→BALANCE/FAST thresholds in PV-SOC-02 apply; independent SoC STOP at SoC<T-H and economic Wh STOP are not weakened. See PV-BAL-06. Documentation only.


## 25 October 2026 – Configurable household-load threshold for FAST economic restart [AGREED]

A battery charging with **any measured positive power (>0 W, even 1 W)** is sufficient economic recovery evidence, subject to existing import budget reset and all SoC/OCPP/operator gates. For battery-backed FAST (including night operation), user added a second alternative: valid household demand **excluding EV** strictly **below** an **integration-configurable threshold, default 500 W**. Typical local night house load is about 200–300 W; this justifies the local default but is not a universal proof of inverter/battery available power. The historical 100 W relative modeled recovery margin and storage-discharge budget D formula are superseded for this proposed controller. Stop provenance and physical OFF still matter, and unchanged evidence should not cause unbounded retry. See [PV-ECO-08](pv-regulation.md) and [PV-TUNE-12](pv-operating-point-response.md). Only documentation branch modified.


## 25 October 2026 – No extra restart-loop prevention yet [APPROVED]

The user elected to **observe** whether economic Wh STOP followed by valid retry actually produces a problematic immediate retry loop. Do not make an additional WBM backoff, new sensor-value edge, once-per-event retry latch or fixed rearm wait a normative precondition. Existing genuine physical station re-enable lockouts and command/response fences remain respected. With valid import-Wh reset, actual OFF, SoC/authority permission and either positive battery charging or FAST low-household-load evidence, restart is permitted even if the qualifying measurement has not changed since the previous STOP. Add extra loop control only if on-site tests show a real issue and the user approves a new rule. See PV-ECO-08. Documentation only.
