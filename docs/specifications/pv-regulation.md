**25 October 2026 BALANCE STOP amendment (latest user decision):** The corrected EV step direction is **below** the ideal point to recharge the battery when SoC is below its target deadband, and **above** to discharge when SoC is above. BALANCE has two STOP causes: (1) the desired *positive* battery charging cannot be attained even at the currently feasible minimum positive EV charging point; (2) `SoC < T-H` (strict). **Both use the shared STOP→BALANCE restart only above `T+H/2` (with proven PV surplus), or STOP→FAST at/above `T+H`.** This supersedes the historical `SoC <= T-H/2` STOP and the old restart equality at M; the revised matrix below is authoritative for the new design. An updated Solcast forecast can jump the dynamic T across the new threshold immediately. This documentation is not deployed control behavior.

**25 October 2026 configurable PV economics:** The formerly fixed 50 Wh economic import STOP is now the shared integration parameter `pv_grid_import_budget_wh` (**default 50 Wh**), per [PV-TUNE-01–12](pv-operating-point-response.md). References below to “50 Wh” mean the configured budget, default 50 Wh, except historical quotes. The accumulated Wh, M/2 comparison, >15 s reset, STOP provenance and economic restart inhibition remain unchanged. Explicit economic STOP overrides ordinary startup and reduction timing. Configuration changes do not themselves restart economic charging.

**25 October 2026 shared point and EV-settling requirements:** [PV-STEP-01–06 / PV-RESP-01–08](pv-operating-point-response.md) specify NETZ-like phase-series navigation, one upward legal point every third eligible regulation cycle, a nominal-230-V measured-consumption 50% gate, and a mandatory ten-cycle ordinary optimization hold after 0 A → positive. These apply **identically to FAST and BALANCE**; independent user/SoC stop and economic 50 Wh priority are not silently suspended. FAST deadband is configurable, default ±100 W; the two legacy PV delay settings are removed, replaced by shared cycle-based timing parameters. See PV-TUNE-01–12.

**25 October 2026 agreed redesign direction — not yet an implementation authorization:** the operator accepted a simpler, shared three-profile PV regulator: preserved profile-specific target SoC, grid-point-led FAST with bounded active exploration, battery-power-led BALANCE with SoC recovery and a grid economic fallback, and withdrawal of the former configurable maximum storage-discharge power as an ordinary controller budget. Sections PV-FAST-01–04, PV-BAL-01–05, PV-ECO-06/08, PV-QA-01–03 and the cross-cutting review below are updated accordingly. Ramp, settling, FAST deadband, EV uptake threshold, ordinary downward delay, import-Wh budget and FAST low-house-load restart threshold are specified under PV-TUNE-01–12. BALANCE battery bias, STOP/restart, and no smoothing are resolved under PV-BAL-03/06/07 and PV-SOC-02. The non-D economic restart signals are specified under PV-ECO-08; only explicitly noted measurement mapping, replay validation and execution details remain OPEN. The related [Level-2 decision diagnostics contract](pv-decision-diagnostics.md) is a documentation target, not deployed behavior. This is an individually approved architectural direction within an overall **DRAFT** V3; no release or implementation is authorized by this document.

**Implemented feature-branch supersession (24 October 2026):** [charge-release.md](charge-release.md) and [implementation report](../coordinated-control-redesign.md) govern manager release, unified NETZ actions, authority-independent hardware enable, and removal of WBM automatic wake-up control. Conflicting older clauses below are historical. PV regulation algorithms remain deferred.

**Narrow approval (24 October 2026):** [WBM-PV-RELEASE-01–07](charge-release.md) introduce a separate operator-controlled manager `charge_release` gate for the future PV profiles: manual Start/Stop, a computed 0 A while release is true is not an operator stop, and each profile change resets release to false. These requirements supersede only conflicting charging-permission/start-stop UI wording below. The broader PV V3 regulator and economics remain DRAFT pending separate approval.

# Wallbox Manager – PV Regulation: Consolidated Requirements Baseline V3

**Status:** DRAFT for overall subject-matter approval; **no new implementation instruction without review**

**18 October 2026 overarching override [APPROVED independently]:**
[WBM-ARCH-01–12](architecture-responsibility.md) governs this entire draft and its
acceptance matrices. Its explicit supersession register replaces manager-side
physical safety gates, observation-age expiration and mandatory fresh physical
proof/pre-command readback. Remaining “fresh”, “safety”, “proof”, “verified now”
and readback language below cannot reinstate those obligations. Actual/requested
state, source/response causality for optimization, economic policy and authority,
intent, transaction and generation fencing remain distinct. **V3 stays DRAFT.**

**Updated:** 25 October 2026 (FAST/BALANCE redesign direction, logging and open issues); architecture precedence: 18 October 2026; original source reconciliation: 13 October 2026

**Reference:** `wallbox-manager` v0.3.2-beta.13 (release commit `aaef76d41295cf0b0255933f72b6d65448f6864a`), GitHub branch `develop` / release tag `v0.3.2-beta.13`

**Work type:** Source reconciliation, English migration, approved independent decisions and 25 October 2026 shared-regulator redesign review; **documentation only**, with no control-software, HA or station change.

**Primary sources:** User-supplied PDF printout *Wallbox Modbus-Abendauswertung* (619 pages, discussion of 4–12 October 2026), particularly pp. 1–19, 67–83, 92–119, 132–148, 209–246, 266–320, 401–449, 460–480, 501–528, 529–545 and 566–619; repository documents, **especially the latest** `docs/pv-grid-import-policy-design.md` from `v0.3.2-beta.13` (see source list below). PV-OCPP-04 is sourced separately to the explicit user approval of 15 October 2026, recorded in the [decision history](decision-history.md#15-october-2026--charging-permission-connection-readiness-and-recovery-approved).

**Evidence boundary:** The PDF printout contains the visible discussion text, but not automatically the full contents of every referenced local log, Codex artifact or hidden attachment. Requirements identified here as APPROVED were supported by the visible discussion or the specification released with beta.13. Whether beta.13 meets them at runtime is a separate assessment. The migration preserves this original evidence statement; it does not claim a new inspection of the external PDF or hardware logs.

## 0. Validity, precedence and change rules

- **APPROVED:** Explicit agreement in the discussion, most recently authorized by the user, or a technical clarification subsequently established and approved under that agreement. Remains valid until superseded by a later explicit decision.
- **TECHNICALLY SPECIFIED:** Concrete execution semantics from the final implementation documentation released with beta.13. This is not automatically a universal product requirement for future redesigns; changes must be justified.
- **HISTORICAL / SUPERSEDED:** Retained for traceability only; **do not** apply as a parallel requirement.
- **OBSERVED:** Finding from a hardware log or discussion. Without comparison against the target specification, this does not authorize a change.
- **OPEN:** No binding threshold or proven cause exists. Do not introduce an arbitrary new rule.

**Precedence in a conflict:** (1) Latest explicitly confirmed decision, including the 25 October 2026 FAST/BALANCE direction → (2) its detailed, approved realization in the beta.13 design/review report → (3) older current specifications that have not been superseded → (4) older draft/implementation reports as history. This precedence governs the **target behavior contract**; actual behavior and code require separate assessment. The 15 October 2026 decision supersedes only the permission-preparation dependency specified in PV-OCPP-04. The 16 October 2026 decisions specify event-fed input and one clocked PV regulator (PV-ARCH-05–08), historical smoothing configuration in regulation cycles (later PV-BAL-03 expressly disables extra BALANCE smoothing), approximate 50 Wh detection (PV-ECO-09), and **explicitly replace the former 10-second measurement-report gap rule** with last-value-held validity (PV-ECO-05). Ordinary unchanged values remain valid until explicitly invalid, not merely until a report deadline. This does not remove the independent OCPP safety, command-acknowledgement, permission, transaction, physical evidence or economic recovery requirements. Neither decision approves the overall V3 draft.

**25 October precedence:** The newly revised PV-FAST/BAL clauses supersede older D-limited power-regulation and all-source-generation increase gates **only for future PV regulator implementation**. The updated PV-SOC, unchanged PV-PLAN, and preserved PV-ECO-01–05/07/09, WBM-ARCH and WBM-PV-RELEASE provisions retain their own scope. PV-ECO-08 now contains the agreed energetic restart tests without a further anti-repeat rule; signed battery measurement mapping and operating-point acceptance remain to be implemented and verified before active PV control.

**For every future change:** Identify the requirement ID, unchanged/superseded target behavior, observed actual behavior, deviation, effects on other IDs, concrete test cases, expected diagnostic evidence and, where applicable, *explicit* approval of a target-behavior change. No iteration may silently discard an approved requirement merely because a new log exists.

---

## 1. Functional architecture and profile availability

### PV-ARCH-01 – Shared regulator with a home battery [APPROVED]

All three PV profiles with a home battery use **one shared** SoC state machine, FAST/BALANCE power regulation, technical operating-point selection, economic 50 Wh logic and OCPP execution. The profiles differ **only in selecting the profile-dependent target SoC T** for this common regulation path; they must not implement separate FAST/BALANCE policies, tuning or divergent OCPP behavior. **PV_MAXIMUM and PV_SURPLUS must produce identical charging decisions and OCPP operating points when T and all other parameters are identical.**

Evidence: PDF pp. 19, 24, 134–145, 579–583; `docs/pv-soc-policy.md`, `docs/pv-grid-import-policy-design.md`.

### PV-ARCH-02 – PV_SURPLUS without a home battery [APPROVED]

`PV_SURPLUS` is **the only PV profile required to work without a home battery**. The current mandatory measurement contract for battery-free operation consists of **measured grid import, measured grid export and measured power of the selected wallbox**. The surplus target is calculated as

`P_ziel = P_EV_ist + P_Netzeinspeisung - P_Netzbezug`.

The original formula identifiers are retained: target power = actual EV power + grid export − grid import.

Neither a separate PV power sensor, household-consumption entity, battery nor PV forecast may become an **additional mandatory input** for this special case. Valid measurements and actual technical operating limits remain prerequisites. Incomplete grid mapping must not falsely indicate that PV_SURPLUS is executable.

Evidence: PDF pp. 575–580, 583, 608–609, 611–612; `docs/pv-grid-import-policy-design.md`, final review.

### PV-ARCH-03 – Profiles with battery and forecast [APPROVED]

- With a battery and the required battery measurements: `PV_SURPLUS` and `PV_MAXIMUM` are available.
- `PV_OPTIMUM` additionally requires a mapped, valid remaining-day PV energy forecast (energy in Wh/kWh/MWh, rather than instantaneous power alone).
- Missing mandatory entities or explicitly `unknown`/`unavailable` states must not produce invented zero values or accidental permission. Temporary unavailability must be distinguished from permanently missing profile mappings. Mere silence without a changed value does **not** make a valid input stale.
- **16 October approved clarification:** A PV profile lacking any mandatory currently valid input cannot perform new PV regulation. Only profiles using the missing input are affected. An independently configured battery-free `PV_SURPLUS` can remain eligible when valid grid import/export and selected EV power remain available, even without BYD SoC. **NETZ remains operational without Fronius, Smart Meter, PV or BYD HA measurements** when its own OCPP connection, verified authority, permission, capability and user prerequisites are met. Never automatically change from PV to NETZ or invent a missing measurement. Handling an already charging PV session when mandatory input becomes unavailable follows the existing safe execution/STOP rules; no new unconditional emergency permission-OFF or automatic NETZ fallback is approved.

Evidence: PDF pp. 575–580, 609; `docs/pv-optimum-design.md`, `docs/pv-grid-import-policy-design.md`.
**25 October measurement-contract change (direction approved; mapping design pending):** FAST uses the measured **signed grid exchange** (positive import, negative export) and selected EV actual power as its primary feedback; ordinary SoC and common eligibility remain required. BALANCE needs **bidirectional home-battery power** (charge positive, discharge negative) and valid SoC, plus measured grid exchange for the existing economic import policy and corrective fallback. The existing `storage_discharge_power` mapping is discharge-only and cannot by itself represent battery charge. **25 October agreement and verified FPM `develop` source:** map the **second, separate Fronius Home Assistant storage charging-power entity**, alongside existing `storage_discharge_power`, as `storage_charge_power` (proposed reference key). They come from Model 160 `StCha` / `StDisCha` `DCW` channels read under the same FAST model, but are separately published HA states; the PV Manager does not explicitly enforce that the two separately reported values can never both be positive. Apply exactly the WBM-specific normalization and both-positive-zero rule in PV-BAL-07, without changing the source integration. Do **not** infer charge from zero discharge or silently add a mandatory PV/household sensor. Final HA entity-ID mapping is selected during config flow; preserve existing discharge mapping. Battery-free PV_SURPLUS retains its exact special-case contract PV-ARCH-02. NETZ remains entirely independent.

### PV-ARCH-04 – Ownership boundary [APPROVED]

**18 October precedence:** WBM-ARCH-01–04/09 now makes the station solely responsible for all physical/electrical safety, interlocks and switching; WBM requests are optimization/policy proposals.

`wallbox-manager` owns profile policy, the charging operating point and OCPP control. The Fronius/BYD integration supplies measurements; Wallbox Manager does **not** control the home battery through a parallel Modbus path. The wallbox hardware owns CP, vehicle state and physical phase relays. A future energy manager must not create a second competing regulator. Reuse means **shared functions**, rather than duplicated algorithms.

Evidence: PDF pp. 1–19, 209–246, 579–583.

### PV-ARCH-05 – Two event-fed input interfaces and one observation snapshot [APPROVED]

Wallbox Manager has exactly two incoming observation interfaces: **(1) OCPP to the wallbox** for telemetry, station/transaction state and command responses, and **(2) internal Home Assistant state entities** for PV, household/grid, battery and forecast information. Both are treated as reliable event-fed sources for the information they actually deliver. For ordinary measurements, **no change event means no value change**, even after 30 seconds or longer, **within one successfully initialized connection generation**; a currently valid value has no fixed age expiry. On each new OCPP connection, PV-OCPP-06 requires all supported current values/states to be established again, including unchanged values. It becomes invalid on an explicit unavailable/unknown indication or applicable source/connection-generation invalidation. Initialize known HA entity state and obtain supported wallbox discovery/readback once on setup, boot, reconnect or required recovery; thereafter maintain one canonical observation store. Subscribe only to relevant configured HA entities. Ordinary incoming events **update observations without running PV regulation**. Unchanged value reports may update timestamps/provenance without repeated business-state publication or control computation.

Keep value, identity, source time, receipt time, validity and OCPP connection/boot generation distinguishable. Reliable unchanged-value retention for ordinary measurements does **not** prove a requested *new physical operating point* or invalidate the need for source-specific OCPP readbacks. OCPP does not universally guarantee push events for all device variables; retain specifically justified queries for authority, permission, physical phases and command application where notifications cannot confirm them. A HA dashboard may display meaningful new observations promptly without triggering a PV-control step.

### PV-ARCH-06 – One periodic PV regulation step with a current snapshot [APPROVED]

**25 October 2026 release-independent planning amendment [EXPLICITLY APPROVED]:**
A selected battery-equipped PV profile maintains its canonical target SoC and, for
PV_OPTIMUM, the daily planning phase independently of `charge_release`, OCPP connection,
transaction, Remote authority, hardware enable or actual charging. Use the **same single
PV scheduler**, with an initial read-only setup calculation and subsequent configured
cycles. With release OFF it performs only observational planning, meaningful publication
and durable day/phase updates, then exits before FAST/BALANCE, economic accounting,
operating-point selection or OCPP execution. No additional acquisition, forecast API,
regulation loop, execution worker or planning entity is introduced. Invalid calculation
inputs make T unavailable; valid retained HA inputs and target-formula independence from
actual SoC remain governed by WBM-ARCH and WBM-HS-UI. Release/authority/transaction guards
still apply unchanged to every charging request. NETZ performs no PV planning;
battery-free SURPLUS gains no battery-target dependency.

An active authorized **automatic PV profile** runs **one configurable regulation cycle**, for example every **5 seconds**; this is an illustration, not a new fixed timing parameter. Each cycle reads a consistent snapshot, applies the existing shared profile, SoC, FAST/BALANCE, electrical and 50 Wh policies, and submits a new target only when required. Ordinary changes in PV, grid, battery, vehicle connection or profile selection are reflected in the **next** cycle, not in an additional event-triggered control pass. An unchanged target must not cause redundant OCPP commands; queued and confirmed states remain separate. No competing duplicate PV loop or per-event power recalculation is permitted.

Smoothing, where justified, is owned by the regulator and samples configured inputs once per control cycle. For example three consecutive 5-second cycle samples form a three-element sliding average; they do **not** establish three independent physical OCPP/HA measurement generations. **PV-BAL-03** continues to define independent regulation-interval/averaging settings and smoothing scope (PV and total consumer power, not SoC, EV response, voltage or safety evidence). **Revised PV-ECO-05** permits energy counting and the strictly >15 s reset condition across control ticks with the same still-valid grid-import value, without pretending those ticks create new sensor reports. Actual unavailable values, missing accounting eligibility and HA/WBM downtime remain excluded.

When WBM only observes, when authority is absent, or when no automatic PV control is active, there is **no cyclic FAST/BALANCE operating-point or economic policy evaluation** merely because battery SoC or other inputs are configured. The explicitly approved observational target/day planner above continues for a selected battery PV profile, including release OFF; that calculation grants no charging authority. Fixed/manual control does not need a continuously running PV regulator. Genuine time-based obligations (e.g. roughly 300-second forecast planning, local-midnight transition, permitted dwell/lockouts) remain scheduled as needed. The older **PV-PLAN-04** allowance for immediate recalculation on defined measurement events no longer authorizes a parallel ordinary PV power-regulation path; immediate exceptional processing is limited by PV-ARCH-07.

### PV-ARCH-07 – Immediate safety and asynchronous OCPP execution [APPROVED]

**18 October precedence:** WBM-ARCH-01/09/11 supersedes manager hardware safety checks and mandatory physical proof below. Prompt authority/connection/intent fencing and truthful command outcomes remain; no mandatory fresh readback before requests.

Loss of control authority, OCPP disconnect and critical hardware/safety faults must invalidate control readiness promptly, **independent of the PV regulation tick**. The station remains responsible for physical CP and local protections. A normal unplugging/status event can be incorporated into the next PV decision, but queued/new commands still have to pass the existing live pre-dispatch authority, permission, connection, transaction, intent and safety checks. OCPP CALL/CALLRESULT/ERROR handling, command serialization, delayed acknowledgements, readback and confirmation of the **actual physical operating point** are asynchronous and separate from the regulator; protocol acceptance never substitutes for physical proof.

WebSocket Ping/Pong already supplies transport liveness; choosing to ping only after inactivity is an optional optimization, **not** a new correctness precondition. Transport liveness is distinct from the assumed last-value-held ordinary metering contract and never proves a new physical phase/current change or an applied command. Boot, disconnect and reconnect invalidate or resynchronize affected execution evidence. Preserve PV-OCPP-01–04, verified phase switching, CP lockouts, response/measurement causality, existing SoC/FAST/BALANCE safeguards and 50 Wh accounting/recovery restrictions; no new automatic authority takeover or unsafe restart.

### PV-ARCH-08 – Remove redundant work and validate performance [APPROVED architectural approach]

Implement the above architecture by **removing or replacing** redundant event listeners, periodic tasks, publications and duplicated regulation paths, not by adding another coordination layer. Separate OCPP input/recovery, HA observation intake, one PV decision engine and guarded asynchronous OCPP execution. Every implementation review must identify which old path becomes obsolete, preserve invariants with tests, and report simplifications/removals as well as additions. Diagnostics and HA state updates must not trigger fresh full-profile calculations when only presentation changes.

Use the proven clocked design of **wallbox-stationary** as a **conceptual reference, not drop-in code**: its [dynamic regulator](https://github.com/ekkehard-lutz/wallbox-stationary/blob/6edf3b0a8b46bc1a1f2c34f1e986c179915dd2f0/services/dynamic_amps.py) runs one T_TAKT loop, reads a measurement snapshot and buffered grid history, and conditionally applies a current target. That code still reads inverter values inside the tick and polls the HA forecast; its input acquisition is **not yet the event-fed interface required here**. Its PV loop also does not supply the required dynamic 1p↔3p selection, verified switching and phase lockouts of WBM.

Validate observation-only CPU cost with WBM disabled/enabled without takeover under comparable conditions, profile active-cycle computations, redundant entity publications, safety invalidation between ticks, and preservation of all earlier PV-OCPP/PV-ECO requirements. A numeric CPU pass threshold remains OPEN until measurement context is established; observed high CPU is not proof of a specific root cause. Source: [16 October decision](decision-history.md#16-october-2026--event-fed-inputs-and-clocked-regulation-approved).

---

### PV-ARCH-09 – Clocked PV target during temporary OCPP disconnect; no deferred command queue [APPROVED, 17 October 2026]

During an already authorized automatic PV charging intent, **temporary loss of the OCPP connection does not by itself stop the configured PV calculation cadence**. The regulator can continue determining its **provisional energetic target** from available Home Assistant PV/grid/household/battery input at the existing configured interval. This does **not** authorize control actions or pretend that missing station readings are current: physical phases/voltages, actual EV power, transaction and charging response evidence required by individual policies or by executable-point selection remain unavailable until independently re-established. Invalid or missing inputs do not become zero or grant economic credit. Explicit user OFF, explicit Local authority/revocation and other actual safety/intent changes continue to stop or invalidate the applicable control activity; this exception is for a **temporary connection outage**, not permanent passive observation.

From disconnect until connection-scoped initialization and the necessary live permission, authority, transaction, capability, actual phase and physical-response evidence are re-established, **no OCPP charging command is dispatched**. There is **no pending setpoint queue, buffered command replay, resend of the last pre-disconnect setpoint, or special reconnect-driven regulation cycle**. After successful current-generation initialization under PV-OCPP-06, the **first ordinary configured regulation tick** takes the latest available measurements, makes a fresh policy/executable-point decision and submits a command **only if the existing live safety/authority/permission/transaction/phase/command guards permit it** and an application is needed. Preserve original configured cadence; do not invent another polling or wake timer to accelerate reconnect. Stale old-generation ACKs and accepted/applied operating points cannot satisfy new-generation proof.

Connection loss does not create economic accounting eligibility, permit downtime energy integration, erase persisted 50 Wh STOP/restart history, grant a fresh restart or imply a zero-current physical response. Keep the existing distinction between a provisional energetic target and a verified executable OCPP operating point. This is a **future implementation requirement only**, not approval to change the running code or station during passive CPU optimization.

Evidence: Explicit user decision of 17 October 2026; [decision history](decision-history.md#17-october-2026--clocked-pv-target-across-ocpp-disconnect-approved).

---

## 2. Shared battery SoC state machine

### PV-SOC-01 – One target SoC T, one hysteresis parameter H [APPROVED]

`R` = reported minimum home-battery reserve, `H` = shared parameter `soc_hysterese`, default **2 percentage points**, `T` = profile-dependent battery target SoC. Valid settings satisfy:

`R + H <= T <= 100 - H`.

**Revised thresholds:**

- `S = T-H` — lower BALANCE / common SoC STOP boundary, with **strict** `SoC < S` as the new requested criterion.
- `L = T-H/2` — **lower target deadband boundary**, no longer the common STOP threshold.
- `M = T+H/2` — **upper target deadband boundary**; STOP→BALANCE requires strictly `SoC > M` with established PV-start evidence under PV-SOC-02.
- `U = T+H` — FAST entry threshold.

**Compatibility:** Historical PV-SOC-01/02 and `pv_soc.py` used `SoC <= L` as STOP; the new SoC matrix PV-SOC-02 replaces it with strict `SoC < S`, preserves established start-evidence and FAST eligibility, and uses strict `SoC > M` for STOP→BALANCE. Never enforce the historical and new STOP thresholds in parallel.

Do **not** introduce separate configuration parameters for “hysteresis” and “buffer”.

Evidence: PDF pp. 133–145, 148; `docs/pv-soc-policy.md`.

### PV-SOC-02 – Shared SoC mode and STOP restart matrix [REVISED 25 OCTOBER; APPROVED DIRECTION]

**Latest operator clarification (25 October):** The **existing SoC recovery hysteresis** already defines STOP restart. Both `BALANCE` STOP causes (minimum-positive-point battery charging not achieved, or strict `SoC < T-H`) join the **same STOP state**; no new SoC-based restart policy or dedicated retry gate is added for cause A. This does **not** remove the separately approved economic import-Wh STOP/restart inhibition. The previous matrix's `SoC <= T-H/2` mandatory STOP is **HISTORICAL / SUPERSEDED**, and the previous STOP→BALANCE equality at `M` is superseded by the explicitly requested **strictly above M** threshold.

Let `S = T-H`, `L = T-H/2`, `M = T+H/2`, `U = T+H`:

| Observed SoC | Previous STOP / paused / new session | Previous BALANCE | Previous FAST |
| --- | --- | --- | --- |
| `SoC < S` | STOP | STOP | STOP |
| `S <= SoC < M` | Remain STOP | Continue BALANCE **unless** minimum-point condition A applies while below `L` | Switch to BALANCE |
| `SoC = M` | Remain STOP (**strict >M** to restart) | Continue BALANCE | Continue FAST |
| `M < SoC < U` | Start BALANCE **only with proven PV surplus** (`PV > household consumption excluding selected EV`); otherwise remain STOP | Continue BALANCE | Continue FAST |
| `SoC >= U` | Start FAST (no PV-surplus prerequisite) | Switch to FAST | Continue FAST |

**BALANCE condition A** is the explicitly approved no-additional-delay STOP when **`SoC < L`**, real applied **1p/6 A** (after required initial settling) and valid signed **battery power < 0 W** are observed on the scheduled PV regulation tick. It enters the same STOP column thereafter. Mere request/ACK/current-phase lockout or a still-settling EV are not sufficient. No separate timer or repeated-negative-measurement gate is added.

**Restart semantics:** Once stopped for either cause A or cause B, continue measuring the battery while the car is paused at 0 A. As the battery recovers, start BALANCE **strictly when SoC > M**, subject to the already approved raw PV-surplus start evidence, current operator manager release, authority/transaction, technical feasibility and any independently applicable economic STOP history. If the observed SoC has reached **`U` or higher**, start FAST instead, **even without PV surplus**, provided normal eligibility applies. Do not add a distinct timer, new SoC threshold, remembered cause-A restart formula or competing sub-state machine. Startup point changes continue to use the approved 0 A→positive settling hold and one-point-per-configured-cycles ramp.

**Important:** Hysteresis can deliberately produce a trajectory around `T-H/2` and `T+H/2` rather than exact SoC lock at T. The SoC deadband and discrete EV bias are for BALANCE regulation only, not an extra STOP state. When T moves dynamically due to a Solcast update, recompute `S,L,M,U` from that new canonical T on the next eligible regulation tick; if `SoC < S`, trigger the mandatory SoC STOP even if the battery itself has not discharged since the previous tick.

The former approved matrix (with STOP at `SoC <= L` and STOP→BALANCE when `SoC >= M` plus PV-surplus evidence) remains recorded in [v0.3.2 policy history](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.18/docs/pv-soc-policy.md). Those two threshold differences are deliberately superseded here; other surrounding eligibility and ownership rules are retained.

### PV-SOC-03 – Target by profile [APPROVED]

- **PV_MAXIMUM:** `T = R + H` (no separate target-SoC setting).
- **PV_SURPLUS with battery:** User-defined `T` within the permitted interval. **Intentional battery discharge for the vehicle is also allowed above U in this profile**; PV_SURPLUS can reproduce PV_MAXIMUM at the same T.
- **PV_OPTIMUM:** Dynamic T, bounded by its own ordered lower/upper user limits; both also lie within the globally permitted interval.
- **PV_SURPLUS without battery:** No SoC state machine or mandatory battery SoC; its own PV/grid-surplus policy, with shared technical and economic STOP logic.

Evidence: PDF pp. 140–148, 580–583.

---

## 3. PV_OPTIMUM and the daily forecast

### PV-PLAN-01 – Purpose [APPROVED]

At the beginning of the PV day, the battery may discharge down to the permitted dynamic target to free capacity for buffering. Towards the end of the day's potential yield, T rises so that the upper target is restored for household use in the evening/night where possible. WBM **does not force nighttime battery charging**. Brief new BALANCE charging periods are permitted while the shared SoC/start rules are met.

Evidence: PDF pp. 1–19, 92–114; confirmation in the discussion current at the original source reconciliation.

### PV-PLAN-02 – Three daily phases [APPROVED; later decision supersedes earlier behavior]

1. `BEFORE_SURPLUS`: From the start of the local day until the **first proven strict PV surplus**, `PV > household consumption excluding the selected EV`; target = upper SoC limit.
2. `DYNAMIC`: From that first event, the target SoC is calculated regularly. A cloud or temporarily negative instantaneous balance does not revoke the start.
3. `FINISHED`: As soon as `remaining_PV_Wh <= remaining_house_Wh`, immediately set the target to the upper limit and retain this daily state **until midnight**, even if a later forecast improves.

The local date change resets the daily latch. Daily state is persisted with versioning; a reload must not fabricate a first PV surplus. The daily planner observes these transitions while release is OFF as well as ON; release transitions never reset its day/phase. A valid total-consumption measurement includes the selected EV, so `PV > total_consumption` already proves strict surplus without inventing EV power. Otherwise, subtract only a genuinely valid selected-EV observation to establish household demand; release OFF is never evidence of EV=0. Missing surplus evidence retains BEFORE_SURPLUS without inventing a past event. Day reset remains independent of source availability; a missing forecast/capacity/reserve keeps T unavailable.

**Superseded:** “First PV >=100 W”, “100 W for five minutes”, sunrise, fixed sunset or sunset minus 1 h as the planning start/end. See the supersession register.

Evidence: PDF pp. 1, 98–114, 115–119; `docs/pv-optimum-design.md`.

### PV-PLAN-03 – Calculation and time basis [APPROVED]

The forecast is **remaining PV energy for the remainder of the local calendar day**. `estimated_daily_house_consumption_kwh` denotes assumed **24-hour daily consumption excluding EVs**. Only the remainder of that day is included linearly, using the correct duration until the next local midnight (including DST days):

```
remaining_house_Wh = daily_house_kWh * 1000 * remaining_day_seconds / 86400
remaining_surplus_Wh = remaining_PV_Wh - remaining_house_Wh
T = clamp(upper_soc - 100 * remaining_surplus_Wh / nominal_storage_capacity_Wh,
          lower_soc, upper_soc)
```

For `remaining_surplus_Wh <= 0`, `FINISHED` applies instead. Nominal battery capacity comes from a mapped real Wh entity, **not** SoC alone. The configured battery-discharge power limit is **not** the SunSpec nameplate value `MaxDisChaRte`. Battery capacity and discharge power limit are different quantities.

Evidence: PDF pp. 12–17, 28–30, 44, 108–115; `docs/pv-optimum-design.md`.

### PV-PLAN-04 – Planning and freshness [APPROVED]

Regular replanning occurs approximately every **300 s**; defined events may trigger immediate recalculation. Solcast updates may affect T between calculations and change it in **either direction** while `DYNAMIC` remains active. The external integration owns API requests and internal forecast updates; WBM makes **no** Solcast requests of its own (do not circumvent free API limits). **18 October supersession (WBM-ARCH-05/06):** The former 900 s forecast freshness class and stricter live-input age limits are removed. Forecast and ordinary inputs remain last-value-held until explicit new information or applicable lifecycle invalidation; age is diagnostic only. Planning cadence and the forecast's calendar-day meaning remain separate from observation validity.

Evidence: PDF pp. 37–52, 101–119; `docs/pv-optimum-design.md`.

---

## 4. FAST_DISCHARGE – Grid-point-led utilization (25 October direction)

### PV-FAST-01 – Purpose and bounded active exploration [REVISED AGREED DIRECTION]

Once the shared SoC state machine admits FAST (including at night above its threshold), FAST should **actively seek more useful EV charging power**, even when net grid exchange is already approximately 0 W and the battery is presently supporting household or EV demand. Do not wait for positive export as the sole reason to increase. Grid exchange is the **primary regulating feedback**: import is positive, export negative. Evaluate signed grid exchange and actual selected EV power from the canonical snapshot once per configured PV tick. Use the existing feasible 1p/3p operating-point solver and station proposals, not independently calculated electrical safety limits. No FAST sub-profile.

An **active exploration step** is allowed only while SoC/profile/manager release, common economic restart, authority/transaction eligibility and ordinary OCPP command/response state permit it. Do not stack unacknowledged setpoint changes or assume an offered current equals measured charging. Once a candidate has been sent, allow asynchronous physical response to settle for optimization; keep progressing at subsequent eligible ticks even if unrelated *unchanged valid* HA sources produce no new report. Do not require all source generation counters to advance to make any new upward progress. A limit/rejection or repeated unsatisfied response is not permission to blindly increase.

### PV-FAST-02 – No configurable battery-discharge budget [REVISED AGREED DIRECTION; replaces old economic D]

The former `optimum_max_discharge_w` / `D` is **not used as a positive EV-power ceiling, FAST ramp authorization, BALANCE cap or economic STOP exception**. WBM does not guarantee a maximum instantaneous physical battery discharge; installed battery/inverter controls and Stationary retain their respective physical limits. FAST instead follows valid SoC thresholds and the measured grid-point correction plus the common measured-import 50 Wh economic policy. Brief grid/SoC deviations are optimization effects to correct in subsequent ticks, **not** a new WBM hardware-safety violation.

**Compatibility/migration:** Preserve the existing HA Number entity identity, service contract and persisted value initially for compatibility, mark it deprecated and unused by the **new** PV control policy; remove/retire it only in a separately reviewed migration decision. This supersedes the 24 October UI-only removal as to future **functional use**, not as to the currently shipped beta.16 behavior. Never interpret the old saved value as a new zero-current safety constraint.

### PV-FAST-03 – Asymmetric response and grid correction [REVISED AGREED DIRECTION]

**Shared mandatory phase/ramp/startup behavior:** [PV-STEP-01–06 and PV-RESP-01–08](pv-operating-point-response.md) take precedence over unspecific `slow increase` wording here and also apply to PV_BALANCE.

- FAST grid-error deadband is configurable at `±pv_grid_deadband_w` (default **±100 W**), without suppressing bounded exploration near zero grid exchange. Increase by one feasible current step no faster than every `pv_increase_interval_cycles` (default **3** ticks); ordinary decreases obey `pv_decrease_delay_cycles` (default **0**). EV uptake must meet `pv_ev_uptake_threshold_pct` (default **50%**) for an increase. No independent per-profile tuning parameters.
- If observed grid import rises materially, reduce quickly, potentially by multiple feasible current steps relative to the actual observed EV power, rather than blindly subtracting 1 A per tick. A short inverter/battery response transient is tolerable, but continuing economic grid import is corrected and is still accounted toward the nominal 50 Wh rule. No separate instantaneous grid-import STOP timer.
- Use one canonical per-tick snapshot. Respect asynchronous request/accepted/confirmed/measured distinctions and the current-generation command fences; *confirmation* is not measured load response. A prompt downward correction need not wait for the upward-ramp timer or every unrelated measurement to refresh.
- An unchanged valid observation may be reused on successive regulation ticks; no fake new sensor report is created. Prevent upward runaway by at most one outstanding intended change, observed response/settling evidence and a bounded exploration cadence rather than historical all-source generation locks.
- Do not add a second feedback loop, parallel grid-correction task or manager-side hardware safety state machine.

### PV-FAST-04 – Technical points, limits and economics [REVISED AGREED DIRECTION]

Shared feasible electrical operating points (including actual offered/applied distinctions), phase-change handling, physical response observations and Stationary protection remain intact. Existing PV-ECO-01–07/09 accounting with **configurable Wh budget (default 50 Wh)** is unaffected by removing `D`. The old “only the technical minimum may exceed D” rule is obsolete. When FAST has a positive ideal point below the currently feasible positive minimum, request/retain that feasible minimum while SoC and economic conditions permit, letting the Wh budget manage material grid import. A computed desired **0 W** alone must not initiate a positive session. The non-D recovery signals are specified in PV-ECO-08, not OPEN.

**Design basis, not imported code:** WBM v0.3.2-beta.18 `pv_regulators.py` provided overly tight generation/response and discharge-budget coupling; `wallbox-stationary` `services/dynamic_amps.py` + `control/amps.py` demonstrate a simpler tick/feedback structure but do not fully solve active grid-zero exploration or phase selection. openWB software2 PV charging documents the EVU/grid exchange as a central control input with storage priorities, **not** a universal storage-only or grid-only controller; see the source review below.

## 5. PV_BALANCE – Battery-flow-led target maintenance (25 October direction)

### PV-BAL-01 – Common battery-flow feedback [REVISED AGREED DIRECTION]

All battery-equipped PV profiles share **one** BALANCE law and entry/exit SoC matrix. BALANCE responds primarily to the **signed measured battery power**: positive means battery charging and permits more EV demand; negative means battery discharging and calls for less EV demand. As a conceptual **unsaturated** starting point:

`P_EV_ideal = P_EV_actual + P_storage_signed`

This is the **unquantized ideal EV power**, not a separate proportional battery-reserve controller. When SoC is **below `T-H/2`**, select **one feasible EV current step below** the ideal; inside the SoC band use the ideal feasible point; above `T+H/2` select **one feasible step above**. The precise phase-series navigation and upward/downward timing are PV-BAL-06 and PV-STEP/RESP. Do not invent a battery reserve target in watts, a SoC-to-power gain or a second feedback loop. Use actual EV power, not offered power, to avoid double counting; no extra BALANCE smoothing (PV-BAL-03).

Battery-flow control must have a valid **bidirectional** HA battery power source and SoC (see PV-ARCH-03). Charging and discharging sign/units must be verified. A discharge-only source reporting 0 W is **not evidence of positive battery charging**. No new mandatory PV-generation or total-household-consumption mapping is justified merely to populate the battery-first controller. If the required battery source is explicitly unavailable, stop issuing fresh BALANCE proposals; preserve manager release, OCPP ownership, and truthful state (PV-ARCH-03/07). Battery-free PV_SURPLUS continues via PV-ARCH-02, not this law.

### PV-BAL-06 – Target deadband, discrete EV bias and STOP [AGREED; acceptance tests pending]

Let `B = P_storage_charge - P_storage_discharge`, with separate valid Fronius entities where one of the two nonnegative readings is zero. Positive B means battery charging; negative B discharging. Derive the instantaneous unquantized ideal EV point from actual EV power and B, **without assuming absent sensor data equals zero**. Then compare observed SoC against the **SoC deadband** `[T-H/2, T+H/2]`:

- **Below** `T-H/2`: choose one feasible EV current step **below the ideal** (not above), leaving a small positive charging surplus for the home battery.
- **Inside** the band, including endpoints: choose the ideal feasible EV point without a deliberate additional battery charge/discharge bias.
- **Above** `T+H/2`: choose one feasible EV current step **above the ideal**, deliberately allowing slow battery discharge toward T. This is BALANCE behavior in the hysteretic state range; FAST takeover still occurs at U under the common state machine.

Apply the existing 230 V nominal phase/current lattice and **phase-series retention** from PV-STEP-01–06; no cross-phase nearest-point search just to achieve a nominally finer bias. The ideal point is calculated at each regulation tick, but actual upward request changes still obey the common cycle interval and EV consumption threshold; ordinary reductions obey the configured decrease delay and startup hold. STOP/economic/authority overrides remain effective. A discrete phase step (230 W 1p or 690 W 3p at 1 A) is not an independently controlled exact battery charging power; battery/inverter loads can respond nonlinearly.

**BALANCE STOP criterion A (minimum-point storage discharge) [APPROVED 25 OCTOBER]:** If **`SoC < T-H/2`**, the manager has already reached the **real, confirmed/applied 1p/6 A operating point**, the agreed 0 A→positive **startup/settling hold has completed**, and the valid measured **signed battery power is still negative** (`P_charge - P_discharge < 0 W`), request **0 A on that regulation tick**. No additional consecutive-negative-sample condition, STOP timer, power smoothing or extra persistence threshold is imposed. A battery flow of **exactly 0 W or positive** is not criterion A. Do not trigger this STOP merely because 1p/6 A was calculated, requested, OCPP-accepted, temporarily phase-locked, or not yet shown as actually applied; nor on absent/invalid storage readings or a stale power/phase observation. A brief household transient can therefore trigger economic BALANCE pause *once those concrete conditions hold*, as accepted. If 1p/6 A is not genuinely reachable in the current station context, this exact criterion is not falsely asserted; other already-approved technical, SoC and economic policies continue to apply, and no imaginary available minimum is inferred. The existing **shared SoC hysteretic STOP restart** applies (PV-SOC-02), with no separate A-specific re-entry test. This is WBM economic behavior, not an additional wallbox hardware safety mechanism.

**BALANCE STOP criterion B (SoC lower bound):** If **`SoC < T-H`**, request SoC-policy STOP independently of whether minimum charging could restore the battery. Equality at `SoC = T-H` does not itself trip this strict test. This may be crossed unexpectedly if PV_OPTIMUM raises T sharply on Solcast forecast changes or raises it faster than SoC can follow. At fixed T, criterion A should normally stop earlier, but this is an intended operating hypothesis, not proof that criterion B is unreachable under all source/measurement disturbances.

**Shared restart confirmed:** Both STOP causes A and B follow PV-SOC-02: no restart in `S <= SoC <= M`; restart to BALANCE only at `SoC > M` with established PV-surplus evidence, or FAST at `SoC >= U` without that evidence. No new restart test specific to minimum-point STOP A. The separate economic Wh STOP may still inhibit a start. Avoid any extra per-cycle STOP event or new sub-state machine. The evidence for A is now defined: valid negative battery power at a truly applied 1p/6 A with completed startup settling and SoC below L. The details require acceptance tests, not a further operator threshold.

### PV-BAL-02 – Stable adjustment and economic grid fallback [REVISED AGREED DIRECTION]

**Shared mandatory operating points and settling:** [PV-STEP-01–06 and PV-RESP-01–08](pv-operating-point-response.md) apply without a BALANCE-specific ramp/phase policy.

No independent battery-power deadband or temporal smoothing is added to BALANCE at this stage (PV-BAL-03/07). Positive corrections are controlled; meaningful negative battery flow or material grid import reduces demand sufficiently promptly. **Grid exchange remains an independent economic guard and corrective fallback, not a competing per-tick second controller**: a battery at its own discharge limit or holding at 0 W can coincide with grid import; BALANCE must not claim that 0 W battery flow alone proves an economically acceptable operating point. The grid import observation feeds the shared 50 Wh estimate; any grid-based correction must be composed into the **same** target decision with a single reason and single request. Do not react to every tiny sign change, substitute raw PV minus household load as the new primary law, or copy historical FAST response/generation gates into BALANCE.

Below `T-H/2` the battery recovers through the **one-lower-EV-step** rule in PV-BAL-06; inside the band no extra battery bias is added, and above `T+H/2` one higher EV step is preferred while BALANCE is authorized. STOP/FAST thresholds and entry are settled in PV-SOC-02. Do not add a separate battery target in watts, new SoC STOP threshold or extra timing filter.

### PV-BAL-03 – No additional BALANCE measurement smoothing [APPROVED 25 OCTOBER]

**Decision:** BALANCE uses the **current valid storage charge and discharge values without any new temporal averaging, median, smoothing window, low-pass filter or additional number of sample cycles**. At each existing PV regulation tick compute the signed battery value and target anew from the currently retained HA states. The ordinary power-point quantization, shared SoC hysteresis, upward tick interval, EV uptake gate and ordinary downward setting already provide the intended damping. Revisit smoothing **only after hardware/replay evidence shows a real regulation problem** and a new explicit operator decision; do not preemptively hide fast storage changes.

Previously approved configurable cycle-based smoothing `N` (legacy `power_smoothing_window` migration) is historical or may remain available for **other independently specified paths** where actually used. Do not silently apply it to the new BALANCE storage regulation or reinterpret legacy seconds as cycles. The shared configured **regulation interval in seconds** remains unchanged. Unsmoothed measured grid-import power remains mandatory for economic Wh accounting; SoC, command state, authority and urgent STOP are never smoothed.

### PV-BAL-07 – Fronius charge/discharge mapping and mutually-positive transition [APPROVED 25 OCTOBER]

**Input mappings:** Use existing `storage_discharge_power` (positive/zero) and add `storage_charge_power` (positive/zero) for the Fronius PV Manager's distinct **Model 160 storage-discharging DCW** and **storage-charging DCW** Home Assistant entities, with watts as units. These are separately readable entities with their own independent Home Assistant reporting, even though Model 160 is fetched in a common FAST model read. Require both sources to be configured and currently valid for battery-equipped BALANCE; no new source is mandated for battery-free PV_SURPLUS or NETZ.

**Normalization on each regular PV tick:**

```text
C = measured battery charging power in W
D = measured battery discharging power in W

if C > 0 and D > 0:
    C_effective = 0 W
    D_effective = 0 W
else:
    C_effective = C
    D_effective = D

P_storage_signed = C_effective - D_effective
# positive = battery charging; negative = battery discharging
```

Both positive inputs indicate a **transitional/inconsistent pair for WBM purposes**. Zero both **only within the WBM regulation calculation and derived optimization snapshot**. Do **not** write 0 to the Fronius source entities, change their HA Recorder history, reinterpret both positive as actual net battery charging/discharging, or delay the rule until more samples arrive. A positive reading paired with 0 is accepted normally, as are two valid zeros. If either source is explicitly unavailable, unknown, nonfinite, missing or outside its supported nonnegative domain, do **not** fabricate a valid zero pair or charge/discharge value: follow existing missing-mandatory-input behavior.

**Code verification / limits:** `fronius-pv-manager/develop` `semantics.py` classifies `StCha` and `StDisCha` separately, `sensor.py` publishes their raw `DCW` values with measurement rounding (0.1 W) and `coordinator.py` polls the Model 160 source in FAST cadence. It does **not** provide an explicit cross-entity atomicity/mutual-exclusion guarantee. Thus both-positive must be handled even though normal inverter physical direction is single. This rule is intended to be simple, stateless and have **no temporal smoothing/settling timer**.

**Effect on STOP A and Level-2 logging:** During a both-positive pair `P_storage_signed=0 W`; **BALANCE STOP A based on actual discharge is therefore not triggered** by the ambiguous pair. The ordinary independent strict SoC STOP, grid-import economic budget and other authority requirements still apply. At diagnostics Level 2, only an actual change of decision or meaningful transition into/out of an invalid/ambiguous-input condition may be recorded with minimal context; do not log every tick of unchanged pairs.

**Acceptance:** exercise `(C,D)=(1200,0),(0,800),(0,0),(1,1),(1000,800)` watts; resulting signed powers `+1200,-800,0,0,0` watts. Test sequential HA publication producing a temporary both-positive pair, eventual switch to a valid single direction, absent/invalid one-channel input, 0.1 W readings, STOP A at applied 1p/6 A (not triggered by the both-positive zero), no new timer/polling/smoothing and no Level-2 log flooding.

### PV-BAL-04 – One common 50 Wh economic STOP policy [UNCHANGED APPROVED]

The historical 90-second deficit STOP, separate 10-second import STOP and instantaneous discharge-limit economic stop remain superseded. BALANCE and FAST use **only** the common measured-import 50 Wh rule for economic OFF, in addition to independent SoC/intent/OCPP conditions. `pv_stop_delay` is **obsolete** and is removed from future settings, persisted keys and HA entities (PV-TUNE-10); it is **not** a second economic timer.

### PV-BAL-05 – Quantitative hardware acceptance [MEASURE/VERIFY]

Quantify acceptable current reversals after simulated PV/household steps, load-step settling, battery power integral/SoC recovery toward T, allowed grid import before correction and behavior when storage is charging, holding, constrained or discharging. Test zero-ampere/start transitions, active response and phase lockouts. The initial replay must demonstrate a **non-oscillating response to the wallbox's own load change**, including source lag, both-positive transitions and last-value-held inputs, and no sustained grid import simply because the battery is holding at 0 W. Any future need for additional smoothing is decided only after these measurements.

## 6. Technical minimum, phase restrictions and OCPP

### PV-PHASE-01 – Provable technically executable minimum [APPROVED]

Minimum charging power `M` is derived from phases, currents, voltage, device/EVSE limits, proven physical position and active phase-switch lockout that are **actually executable now**. `M` is not always 1p/6 A. Under a confirmed 3p lockout, 3p/6 A may be the relevant minimum. A merely planned or time-assumed switch to 1p is **not** currently executable. Unknown physical evidence does not justify an invented 1,380 W minimum.

Evidence: PDF pp. 72–83, 477–480, 569–571; `docs/phase-lockout-continuation-report.md`, `docs/pv-grid-import-policy-design.md`.

### PV-PHASE-02 – Continue charging under phase-switch lockout [APPROVED]

A rejected 3p→1p switch due to an actual phase lockout **does not automatically cause STOP**. The confirmed executable phase mode remains active; current is reduced to its exact positive minimum if necessary. The energetically desired mode is retained; subsequent bounded probes/retries use **fresh** inputs. Existing settling logic must prevent immediate reversal after a confirmed phase change. Generic OCPP BUSY is not proof of a phase lockout.

Evidence: PDF pp. 77–90, 477–480; `docs/phase-lockout-continuation-report.md`.

### PV-OCPP-01 – Commanded and actual state [APPROVED]

A confirmed current/phase setpoint proves **no** measured power. Commands are serialized and bound to proven control authority (`Remote`), permitted charging permission, transaction and current device data; existing live fences apply. An economic **0 A pause** uses the verified zero-current OCPP profile and retains `ChargingEnabled`; station-side charging-reenable/phase lockouts and retry conditions must not be bypassed. User OFF and actual technical/SoC STOP reasons retain their own priority.

**15 October clarification:** Transaction binding applies to positive and zero-current `TxProfile` control. General charging permission has the separate contract in PV-OCPP-04 and does not require a transaction or prior operating-point preparation. Ordinary pauses retain permission while the station owns physical CP interruption. All other command and physical-response protections above remain in force.

Evidence: PDF pp. 67–83, 209–246, 477–480; `docs/pv-surplus-profile.md`, `docs/pv-grid-import-policy-design.md`; PV-OCPP-04 for the explicit later clarification.

### PV-OCPP-02 – Command snapshot and measurement changes [APPROVED]

For a command **not yet sent**, recheck the defined pre-dispatch evidence. Ordinary changes to PV, household, EV, voltage and SoC values after successful dispatch do not retroactively turn the sent point into a stale error. Actual authority, permission, connection, transaction, capability, intent and safety fences remain active after dispatch. The next regulation cycle processes the latest valid measurements. An aborted or unconfirmed OFF attempt must not produce a falsely final stopped state; a late-confirmed positive command must not revoke a **newer** proven 50 Wh STOP.

General permission recovery under PV-OCPP-04 must preserve these lifecycle and late-ACK protections. Reconstruct permission, transaction identity and operating point independently; accepted permission is not proof of an applied profile or physical response.

Evidence: PDF pp. 103–119, 524–527, 605; `docs/pv-surplus-profile.md`, `docs/pv-grid-import-policy-design.md`; PV-OCPP-04 for recovery clarification.

### PV-OCPP-03 – No silent authority takeover [APPROVED]

Only explicit user action acquires authority. With `Local`/missing authority, no positive control commands are sent. Status gives **missing authority priority**, rather than misleadingly reporting missing measurements later; actual sensor diagnostics remain separately visible. Following an authority change or reconnect, the display must update promptly.

PV-OCPP-04 does not authorize automatic Local→Remote takeover or override ownership. An explicit permission ON is handled within the existing explicit authority acquisition contract; reconstruction alone grants no authority.

Evidence: PDF pp. 511–528; `docs/beta12-implementation-report.md`; PV-OCPP-04 for the retained boundary.

### PV-OCPP-04 – Charging Permission, Connection Readiness and Physical CP Observability [APPROVED]

**Decision:** 15 October 2026; individually approved, while the consolidated V3 remains DRAFT. See the [decision record](decision-history.md#15-october-2026--charging-permission-connection-readiness-and-recovery-approved). This is a new decision, rather than a translation correction or a claim of implemented behavior.

#### 6.4.1 General charging permission without a connected vehicle

When the user explicitly enables charging permission in Home Assistant, Wallbox Manager must establish `ChargingEnabled=true` within the existing authority/ownership contract. This must be possible with no connected vehicle, no active OCPP transaction and no transaction-bound charging profile already applied. The user must not have to repeat ON after plugging in a vehicle.

General charging permission is independent of actual charging power. Missing transaction-bound economic preparation alone must not indefinitely defer permission. There is no automatic Local→Remote takeover; explicit user OFF remains distinct from an economic pause. Verify the actual enabled state through supported OCPP readback, rather than treating an accepted command as success.

**Precisely superseded assumption:** A general user ON must remain pending until a transaction-bound economic operating point (positive or 0 A) has been prepared. This dependency alone is replaced. Positive and zero-current `TxProfile` commands still require a valid transaction and all existing execution checks. Electrical safety, phase safety, authority, valid economic measurements, response verification, accounting persistence and economic STOP/restart restrictions remain in force.

#### 6.4.2 Normal vehicle-absent standby

When permission is ON, no vehicle is physically connected, CP is observable, OCPP communication is operational and the station has confirmed physical vehicle absence, the expected state is:

```
ChargingEnabled: true
Connector status: Available
Charging state: Idle
Physical CP: active
Dashboard: normal, not greyed out
```

This is normal standby, rather than a charging pause. No active charging transaction is required merely to remain ready for a vehicle connection. Transaction presence or absence is never a substitute for physical connection evidence. If the available interface has not supplied sufficient physical confirmation, retain an explicit unknown/awaiting-observation state under section 6.4.8.

#### 6.4.3 Temporarily retained current after connection

A previously stored, electrically valid positive current limit may temporarily become effective when a vehicle is plugged in. Neither a transaction-bound 0 A profile nor a new positive `TxProfile` is a prerequisite for general permission. The station may initially use its retained current setting; hardware-enforced electrical limits and capabilities remain mandatory.

Wallbox Manager must take over the selected profile's economic/profile regulation promptly once a valid transaction, required control authority, usable measurements and current capability information are available. Normal regulation starts as soon as these prerequisites are met. Do not promise unconditional correction within one regulation cycle if communication, transaction identity or required measurements are missing. Diagnose missing prerequisites, retain existing safety protections and never invent confirmation that a command succeeded. This temporary permission does not authorize bypassing electrical safety limits or clearing a valid economic STOP/restart inhibition.

#### 6.4.4 Transaction-bound control and economic accounting

Positive and zero-current `TxProfile` commands remain bound to the appropriate valid OCPP transaction identity. Retain authority checks, serialization, current/phase validation, stale-request fencing, delayed-ACK handling, confirmed operating-point tracking and observation of real physical response. General permission must not create a fictitious transaction.

The unified measured 50 Wh policy and transaction-scoped persistence remain unchanged. `ChargingEnabled=true` without a vehicle or transaction does not start a fictitious charging session, create an artificial accounting scope, reset persisted budget state, clear valid STOP history or bypass economic restart inhibition. Reconcile economic regulation with the correct real transaction when it becomes available; never reuse another transaction's budget.

**Q1 — Prospective retained-current accounting [APPROVED, 15 October 2026].** For charging that begins with a retained electrically valid positive station current and no preceding Wallbox Manager positive TxProfile command, use prospective, real-transaction-scoped observational accounting activation. This subsequent decision resolves the former OPEN attribution-policy question; see the [Q1–Q3 decision record](decision-history.md#15-october-2026--subsequent-q1-q2-and-q3-decisions-approved). It extends the earlier command/response-based activation contract described in `docs/pv-grid-import-policy-design.md`; it does not claim that the extension is implemented.

Accounting becomes eligible only when **all** required evidence is available:

- Valid real OCPP transaction identity and scope.
- Valid ownership and control authority.
- Fresh, physically measured EV charging activity.
- Verified currently executable minimum charging point `M`.
- Valid actual grid-import measurements.
- A valid current source value according to revised PV-ECO-05; elapsed time without a value change is not itself a gap.
- Reconciliation of applicable persisted economic history.

The **first counted interval must be fully supported by actual qualifying measurement evidence**. Do not count a preceding interval whose start lacked the required evidence. If transaction identity arrives after physical charging or measurements, begin only valid prospective accounting once the conditions hold; later knowledge of identity must not retrospectively attribute earlier charging. No general pre-identity buffering/replay mechanism is approved. Unknown gaps and downtime produce no integration or reset credit; do not fabricate intervals, transactions or accounting scopes.

Preserve the unified **nominal 50 Wh** economic command threshold: approximate accumulation uses the complete **valid unsmoothed** grid-import power only while `P_import > M/2`; a held valid value may count across any number of regulation ticks, **without a 10-second maximum gap between source reports**. Reset still requires **strictly more than 15 seconds** of valid `P_import <= M/2`. Revised [PV-ECO-05](#pv-eco-05--last-value-held-validity-and-causality-approved-revised-16-october-2026) and retained PV-ECO-07/08/09 govern availability, provenance, persistence, and restart. No persisted budget/STOP history is reset or transferred by observational activation; no new energy allowance, timer or change to the economic restart model is approved.

Keep these four facts distinct: **observed physical charging**, **an active economic accounting interval**, **an authorized manager positive command**, and **an approved restart following economic STOP**. Observational activity activation is **not an authorized positive restart**. It must not call a restart-confirmation mechanism that clears valid STOP provenance merely because charging was observed. A genuinely ended transaction followed by a proven new transaction follows the existing lifecycle rules for a new scope; a changed transaction ID alone during uncertain continuous activity must not reset exhaustion or reuse another transaction's energy. HA restart/reload requires reconciliation of persisted history and new valid evidence, without downtime integration or a fabricated fresh allowance.

**Q2 — Temporary unattributed charging [APPROVED, 15 October 2026].** A temporarily unattributed physical charging interval is accepted while transaction or causal accounting evidence is unavailable; confirmed general charging permission may remain ON, and no automatic permission-OFF fallback is authorized solely for that missing evidence. No mandatory pre-plug 0 A TxProfile is required. Diagnose the missing transaction, attribution or measurement prerequisites explicitly, and distinguish observed physical activity from economic monitoring that is not yet eligible. **Do not claim that the 50 Wh economic monitoring is active during an unattributed interval.** Begin valid prospective accounting when Q1 is satisfied and proceed with selected-profile regulation promptly when its actual execution prerequisites hold. Existing economic STOP/restart restrictions remain mandatory; do not issue automatic positive manager commands when the existing economic recovery contract inhibits them.

**Accepted enforcement limitation:** If the station is physically charging without a usable OCPP transaction, the manager cannot send a valid transaction-bound 0 A TxProfile. Retained-current charging may continue until valid control prerequisites become available or existing protections intervene. **This duration is not guaranteed to be bounded, and the manager cannot claim enforcement of the 50 Wh command-trigger threshold during that interval.** The accepted uncertainty must be visible in diagnostics and in later hardware acceptance. It is not a new general budget or permission to bypass electrical safety. No new timeout, charging allowance, alternative control path, CP command, station-side intervention or emergency permission-OFF policy is approved. Independent physical hardware safety mechanisms and normal authority constraints remain in force.

#### 6.4.5 Connected-vehicle pauses and CP ownership

For a legitimate economic, profile or protective interruption, request the applicable verified 0 A operating point through existing transaction-bound OCPP control. General `ChargingEnabled` remains ON for an ordinary charging pause. `wallbox-stationary` performs physical CP interruption and remains responsible for physical vehicle-connection detection. Do not implement CP control in the Home Assistant integration or introduce a second CP state machine.

The existing economic STOP, exact minimum-current exception and restart restrictions remain valid. A confirmed positive-to-zero OCPP transition can invoke the station's charging-reenable lockout; do not bypass it. Explicit user OFF is a separate permission action and retains its priority. If a required transaction-bound pause cannot be sent, diagnose that limitation and retain existing protective behavior; do not fabricate a transaction or a successful OFF.

#### 6.4.6 Existing station-side CP probing [RETAINED]

**16 October clarification:** During a CP-blocked pause, the station may not detect that a vehicle has been unplugged until the next nominal hourly probe. Under PV-OCPP-05, WBM deliberately continues showing the last wallbox-reported connected state until the station sends the new state. This accepted delay does not authorize WBM to operate CP or introduce a competing detection algorithm.

During a CP-interrupted session with a connected vehicle, the accepted station-side behavior remains:

```
CP probe interval: 3600 seconds
CP probe duration: 2 seconds
```

The station periodically releases CP to determine whether the vehicle remains connected. Once physical disconnection is confirmed, CP returns to normal continuously active operation so a new connection can be detected. Existing station-side connection debouncing and evidence rules remain authoritative. CP interruption alone does not establish immediate vehicle disconnection. These intervals are unchanged; do not assume they are INI-configurable without proof from the actual deployed station implementation. Recording this accepted behavior does not claim a new deployment inspection or hardware test.

#### 6.4.7 Physical phase switching [RETAINED]

**18 October precedence:** WBM-ARCH-01/09: the following sequence and all physical interlocks belong exclusively to the station. WBM does not implement, prove or revalidate its safety steps.

The existing atomic, guarded 1p↔3p sequence remains unchanged:

1. Set EVSE target current to 0 A.
2. Block CP.
3. Verify current reduction through the meter.
4. Switch the physical phase relay.
5. Validate/restore the intended current.
6. Restore CP when safe.

Retain locks, electrical confirmation, failure handling and safety fences. Ordinary pause CP handling and phase-switch CP handling must not interfere. The station retains physical ownership of CP and phase relays in any future implementation.

#### 6.4.8 State reconstruction after restart or reconnection

**Later individually approved update (16 October; PV-OCPP-05/06):** On any new OCPP connection, old-generation observations must not be treated as the new initial state. Wallbox Manager requires a full **new supported-state baseline** of measurements and reported state, including unchanged per-phase zeros, connection/charging state, transaction identity, permission, authority and supported phase/operating-point evidence. Prefer an unsolicited station report burst immediately after registration; supplement using supported explicit readback. This is a new interface obligation, **not** behavior guaranteed by generic OCPP. Within the synchronized generation, retain the wallbox's last reported connection/charging status without a separate WBM CP-observability expiry; CP blockage may delay unplug detection until the next station probe. Missing/unsupported bootstrap fields remain pending/unknown, not silently restored as current facts. Details and tests: PV-OCPP-05 and PV-OCPP-06 below.

Cover HA integration reload, HA restart, Wallbox Manager restart, OCPP reconnection and wallbox restart, including startup with no vehicle, with a connected vehicle and during CP-interrupted charging. Reconstruct current state from valid existing OCPP events and supported read-only queries. Track observation timestamp, source and freshness for each independent fact:

| Fact | Existing evidence / query | Confirmation boundary |
|---|---|---|
| Actual `ChargingEnabled` | Supported `GetVariables` Actual readback | A `SetVariables` acknowledgement alone is insufficient |
| Control authority and ownership | Existing authority observations and supported Device Model variables | Reconnection does not grant ownership or Remote authority |
| OCPP connection/registration | Current transport and registration lifecycle | Heartbeat/registration is not physical vehicle evidence |
| Connector status | Valid scoped `StatusNotification` | Missing transaction is not Available; preserve source time and CP validity |
| Charging state | Valid scoped `TransactionEvent`, or a supported derivation justified by physical absence | No invented Charging/Idle state from permission alone |
| Physical vehicle connection and CP observability | Valid station physical observations through existing supported events/interfaces | CP blocked means unobservable, rather than disconnected; absence must be physically confirmed |
| Current transaction identity | Valid real transaction lifecycle evidence | Do not manufacture a session or reuse an old transaction |
| Current operating point | Supported valid readback, including `GetCompositeSchedule` where an active transaction makes it valid; supported physical phase readback | Never assume `GetCompositeSchedule` works without a transaction; schedule is not measured power or physical phase proof |

Keep explicit user OFF intent, control ownership/authority, session identity, economic scope, late-ACK protection and all existing safety/restart conditions. Reconstructing a persisted desired ON is not authority to override OFF or dispatch an automatic positive point. Unknown, missing, stale or contradictory evidence remains explicit unknown/awaiting observation; retained last-known state must be labeled unverified. Do not claim confirmed current or phase without actual supporting evidence.

**Implemented in source — periodic station-side reporting (informative clarification):** On 15 October 2026, GitHub `wallbox-stationary/main` resolved to [`2baf0daa8c446dad7570e5daa5f0f1cba486e915`](https://github.com/ekkehard-lutz/wallbox-stationary/commit/2baf0daa8c446dad7570e5daa5f0f1cba486e915). Its [`ChargingStation._physical_status_notifications()`](https://github.com/ekkehard-lutz/wallbox-stationary/blob/2baf0daa8c446dad7570e5daa5f0f1cba486e915/ocpp_runtime/station.py#L168) attempts a refresh immediately after accepted registration and on a nominal **60-second** schedule, using the hardcoded `PHYSICAL_STATUS_INTERVAL = 60.0`. Monotonic scheduling skips missed periods without catch-up bursts; this is independent of heartbeat, metering and phase reporting. It is not a guarantee of a valid observation every interval.

The publisher skips a refresh when its physical reader is unavailable, the transaction lock is busy, a remote result is pending or a transaction start is pending. [`WallboxDomain.read_physical_status()`](https://github.com/ekkehard-lutz/wallbox-stationary/blob/2baf0daa8c446dad7570e5daa5f0f1cba486e915/domain/wallbox.py#L253) obtains new GPIO CP feedback and EVSE Modbus permission/current/vehicle/CP-generation readings, with a UTC acquisition timestamp. It requires an available state lock, no in-flight command or phase switch, startup readiness, enabled permission, active CP before and after the register reads, and valid permission, current, recognized non-fault vehicle state and CP-generation values. Failed reads or invalid evidence suppress the refresh. A vehicle READY reading is reportable as absence only if the service already confirms disconnection; one read cannot bypass the existing vehicle-connection debounce in `services/evse_service.py`. The reader does not mutate cached connection state or operate CP/phase relays. CP-blocked states cannot be made newly observable by this timer.

For a valid Available/Occupied sample, the publisher sends `StatusNotification` for EVSE 1 / connector 1 using the sample timestamp, **including unchanged status**. For an existing active, non-ending transaction with enabled permission, no phase switch/fault, confirmed connection and known charging activity, [`TransactionObserver.physical_refresh()`](https://github.com/ekkehard-lutz/wallbox-stationary/blob/2baf0daa8c446dad7570e5daa5f0f1cba486e915/ocpp_runtime/transactions.py#L182) additionally produces `TransactionEvent(Updated, MeterValuePeriodic)` with the same real transaction identity, explicit EVSE/connector, `offline=false` and observed Charging or EVConnected, even if unchanged. The existing serialized sender reserves the sequence number and attaches available regular meter values. The periodic path creates no transaction and emits no Started/Ended lifecycle event; genuine departure remains the existing observer's responsibility.

Without an active transaction, periodic `StatusNotification` can refresh connector occupancy or physically confirmed Available. Valid absence may support the existing derived Idle presentation where supported by the manager's evidence model; the periodic path sends no transaction charging-state snapshot in this case. Occupied alone does not distinguish Charging from EVConnected. Do not fabricate a transaction to obtain that distinction.

**Unsupported by the current interface — explicit request-driven snapshot:** The inspected source still exposes no complete manager-requested vehicle/charging/CP snapshot through its Device Model; `TriggerMessage` remains unsupported. Station-initiated periodic reports are distinct from manager-initiated readback. Heartbeat, MeterValues, `GetVariables(ChargingEnabled)` and phase readback are not substitutes for physical presence/charging-state evidence. `GetCompositeSchedule` describes the supported operating point only where an active transaction makes it valid; do not assume availability without one. Full no-transaction charging snapshots and explicit CP-observability provenance remain OPEN interface questions where existing reports are insufficient.

**Unverified on hardware — deployment and acceptance:** This source inspection does not establish the installed revision of the physical wallbox, receipt of periodic reports from it, or successful physical-state reconstruction. **New interface gap (PV-OCPP-06):** WBM must coordinate initial readback/reporting after every accepted OCPP connection. The inspected station resends connector status after registration, but its metering loop initially sleeps until the first configured interval and station-side physical refresh can be suppressed by CP/other gates. A guaranteed complete immediate initial snapshot is not yet demonstrated; it requires separate station-side implementation or a supported trigger/readback where available. Those remain subject to real OCPP/hardware verification. Until valid evidence arrives, retain unknown/awaiting-observation or last-known-but-unverified presentation; do not claim live Available/Idle recovery. The [earlier physical-status investigation](../physical-status-resync.md) remains historical evidence for its explicitly inspected older revision; its proposed periodic reporting is now implemented in the later source above, while its request-driven alternative remains unsupported. No new station-side protocol requirement or accepted charging behavior is introduced by this clarification.

#### 6.4.9 Dashboard presentation and physical evidence

**Later approved requirement (16 October 2026; PV-OCPP-05) supersedes the CP-freshness display policy here:** The connector and charging-state display represents the **latest report from the wallbox in the current synchronized OCPP generation**. Do **not** expire, grey or downgrade a reported connection/charging status solely because CP is interrupted, the last status notification is old, or no new message was received. The station owns physical CP handling, probe and reporting; WBM has no parallel CP freshness/observability display state machine.

| Case | Meaning | Required display |
|---|---|---|
| A – Wallbox reports Available / Idle | OCPP connected and current-generation report received | Normal **Frei / Bereit**, regardless of charging permission or missing transaction |
| B – Wallbox reports connected/charging | Current-generation station report | Show reported state normally |
| C – Pause, CP interrupted | Last report remains connected/paused; physical unplug might be unknown until station's next probe | **Keep the last station-reported status**, normally displayed; no CP-based ageing or grey overlay |
| D – OCPP offline | No current communications | Last known with explicit offline indication, or unknown; no claim of new live evidence |
| E – Hourly CP probe discovers disconnection | Station subsequently reports Available / Idle | Immediately replace last report with new status |
| F – New OCPP session, initial snapshot incomplete | Some required fields not yet received/read | Pending/unknown for those fields; **never reuse old-generation state as current** |

**Accepted delayed disconnect:** A car unplugged during a CP-blocked pause can remain displayed connected until the wallbox's next nominal **3600 s** test (with **2 s** CP release) successfully detects and reports the disconnection. This is deliberate, not a WBM fault. The displayed state is the **last wallbox report**, not a promise of current physical connection.

**Supersession of the 15 October Q3/case-C display wording:** The previous requirement to grey CP-blocked reported statuses and insist on independent recent CP proof before showing an unchanged status is **superseded**. The retained Q3 intent remains: charging permission OFF by itself does not alter the reported connection/charging display. **Independent proof is still required for actual commanded physical current, phase switching and operating-point confirmation**. No missing transaction, zero-current command or CP pause may be treated as proof of vehicle unplugging, and no fictitious physical state may be invented.

#### 6.4.10 Acceptance matrix [APPROVED decision; verification pending]

**16 October PV-OCPP-05/06 override:** Historical scenario rows 2, 9–13 and 19 below retain the original 15 October CP-freshness/grey-display wording. **Replace that display expectation with section 6.4.9 and PV-OCPP-05**: keep the most recent wallbox-reported status during CP interruption, even until an hourly probe reports unplugging. Rows 14–18 and 23 use **PV-OCPP-06**: a reconnect/restart requires current-generation initial reports for *all* supported relevant state and measurements, even if values are unchanged; missing fields remain pending/unknown. All other safety, authority, transaction and economic obligations remain as originally approved. Dedicated new acceptance scenarios are given below.

These are acceptance obligations for future implementation and separate hardware acceptance, not completed tests. “ACK” below is only protocol acknowledgement; “confirmed” requires supported state readback/evidence, and “physical” requires a genuine station/meter observation. All rows retain authority, electrical, phase, lifecycle and economic safeguards. Apply the approved Q1/Q2 accounting and uncertainty boundaries in section 6.4.4 and the Q3 OFF presentation rule in section 6.4.9. Physical readback/observability limitations in section 6.4.8 remain implementation/interface questions; none of these decisions constitutes hardware acceptance.

| # | Scenario / requested action | OCPP acknowledgement and confirmed state | Physical observation / dashboard acceptance |
|---|---|---|---|
| 1 | Explicit user ON, no vehicle | Authorized permission command may be ACKed without transaction/profile preparation; readback must confirm `ChargingEnabled=true` | No second ON after connection; pending physical evidence is explicit |
| 2 | Enabled vehicle-absent standby | Permission readback and valid scoped status establish permission and Available/Idle separately | Physically confirmed absent, CP active, OCPP connected: normal Available/Idle, no grey styling (A) |
| 3 | Plug in with retained legal positive current; real transaction known before the first measured EV response | No mandatory preliminary 0 A or new positive profile; no fictitious transaction; Q1 eligibility awaits all required evidence, including actual measured activity and reconciled history | Retained current may temporarily operate within electrical limits; show only observed connection/activity; count only a fully supported prospective interval, never infer activity from identity alone |
| 4 | Observe a new real active transaction, including identity arriving after measured charging began | Accept valid transaction identity and scope; lifecycle observation is not a profile ACK; late identity creates no retrospective attribution or pre-identity replay | Associate correct vehicle/session evidence; never infer physical presence from identity alone; begin Q1 accounting only when all prerequisites hold and the first interval is fully supported |
| 5 | Required prerequisites become available | Regulate promptly using selected profile; serialize valid transaction-bound commands, distinguish ACK from confirmed operating point; start Q1 accounting when eligible, without requiring a preceding positive manager command | Verify actual EV response before further increases; display confirmed physical states; observed charging/accounting activation must not authorize restart or clear valid STOP provenance |
| 6 | Transaction evidence missing, including for an extended period during retained-current charging | General permission can be confirmed and remain ON; defer `TxProfile` control and diagnose missing identity; no mandatory pre-plug zero or automatic permission-OFF fallback solely for missing evidence | No invented transaction, current or live physical state; unattributed flow is explicitly diagnosed, monitoring is not claimed active, and continued charging/enforcement delay is not guaranteed to be bounded |
| 7 | Measurements or causal accounting evidence missing/unusable | Diagnose missing inputs and attribution prerequisites; no invented regulatory success or unconditional one-cycle correction; no automatic permission-OFF fallback solely for missing evidence | Existing safety remains operative; show independent valid physical states and separate input/unattributed-flow diagnostics; do not claim active 50 Wh monitoring until Q1 eligibility |
| 8 | Economic pause required | Request valid transaction-bound 0 A; ACK alone does not prove zero; retain permission ON | Observe actual reduction; retain economic STOP provenance and response gates |
| 9 | Station blocks CP during pause | Integration sends no CP relay command and runs no second CP state machine | Station owns interruption; label physical observation limitation and last-known state (C) |
| 10 | Vehicle remains connected while CP blocked | No transaction absence or missing event is treated as unplug evidence | Never falsely show disconnected; retain grey last-known physical status |
| 11 | Existing CP probe becomes due | No new manager CP command or invented observation | Station probes every 3600 s for 2 s; retain debounce/evidence rules |
| 12 | Vehicle physically unplugged, detected by probe | Valid station disconnection/lifecycle evidence reconciles the real scope | Only physical confirmation establishes unplug; CP returns continuously active |
| 13 | Ordinary observation resumes after unplug | Valid Available/Idle evidence replaces last-known state | Restore live normal Available/Idle automatically (E); permission remains independently confirmed |
| 14 | HA restart with no vehicle | Reconstruct permission/authority/registration with supported queries/events; no transaction needed | Until physical absence/CP validity confirmed: F; then A; missing interface evidence remains OPEN |
| 15 | HA restart with active charging vehicle, including retained-current activity | Reconcile actual transaction, operating-point readback where supported, and persisted economic state before applicable control/accounting activation; retained activity cannot clear exhausted budget or STOP/restart history | Fresh physical connection/activity and EV response required; cached desired point is not confirmation; prospective accounting uses only fully supported new intervals, with no downtime or earlier-interval backfill |
| 16 | HA restart during CP-blocked pause | Preserve STOP/restart conditions and real scope; do not fabricate positive recovery or disconnection | Last-known/unknown with CP limitation; transition from F to C only with supporting evidence |
| 17 | Wallbox restart | Re-establish connection, authority, permission, transaction identity and capabilities; old ACKs fenced | No fabricated current/phase or live status; retain unknown until new valid observation |
| 18 | OCPP drops and reconnects | Preserve intent/scope, invalidate unsuitable connection evidence, reconcile via supported readbacks/events | D while disconnected; F while unverified, then appropriate live/CP-limited case |
| 19 | Explicit user OFF versus economic pause; OFF with and without valid independent absence/CP evidence | OFF permission command requires supported confirmation; pause uses transaction-bound 0 A with permission ON; OFF alone is no proof of absence or CP observability | OFF intent wins over recovery/late ACK; never label an economic pause as user OFF; with operational OCPP and valid independent fresh absence/CP evidence show normal Available/Idle and permission OFF; otherwise unknown/awaiting observation or clearly unverified last-known state |
| 20 | Positive-to-zero transition invokes restart lockout | Confirm applicable zero transition; no positive command bypasses station reenable restriction or economic restart tests | Retain actual stopped/paused evidence; no time-only economic restart |
| 21 | Guarded 1p↔3p switching during pause/operation | Existing atomic station sequence and all validation/locks retained | Meter-confirm reduction before relay switch; safe CP restoration; no interference from pause handling |
| 22 | Reload, profile change or different real transaction with persisted 50 Wh state; genuine end versus uncertain identity handover | Same verified scope retains energy/history; no reset from permission ON or observed charging; different scopes never reuse state; uncertain recovery inhibits automatic positives; new scope only under existing proven lifecycle rules | No downtime/backfilled integration or pre-identity replay; Q1 prospective activation preserves exhausted budget/STOP provenance and cannot authorize restart; changed ID alone is no fresh allowance; late ACK cannot erase newer exhaustion |
| 23 | Integration reload or Wallbox Manager restart | Apply the same reconstruction/fencing contract as HA restart, independently of a full HA reboot | Initial F, then evidence-supported A/B/C; no synthetic observation timestamp |

---

### PV-OCPP-05 – Retain wallbox-reported connection and charging status during CP pause [APPROVED; 16 October 2026]

The wallbox is the **only authority for physical connection detection and CP management**. WBM directly adopts its last valid current-generation **OCPP connector status and charging state**, with no manager-side CP-timeout, observability-state machine, inferred unplugging from missing power/transactions or status expiry during CP blocking.

A CP-interrupted charging pause can prevent the station from seeing a vehicle being unplugged. **The WBM may display the car as connected until the next successful station-owned hourly CP probe (nominal 3600-second interval, 2-second CP release) and resulting explicit status report.** This delay is accepted. A new `StatusNotification` or applicable `TransactionEvent` updates the corresponding state when received; no user interaction or secondary WBM probe is required. The station's hardware safety and connection detector remain authoritative; this decision **does not turn a last-known reported status into a new physical observation**.

This simplification affects *reported connector/charging presentation* and ordinary PV tick inputs. It does **not** replace the current transaction scope, authority, permission and post-command physical phase/current evidence necessary for valid OCPP execution. If OCPP disconnects, show offline or unknown; after reconnect, PV-OCPP-06 requires a **new-generation** baseline before claiming that old reported status is current.

**Acceptance:** Preserve reported connected/paused state across prolonged CP block, including when the vehicle is physically unplugged between probes; switch only when the station reports departure. No CP-based grey/stale styling, invented unplugging, duplicate regulation loops or WBM-side CP commands. Verify command/authority/phase safety separately.

### PV-OCPP-06 – Initial synchronization of all supported state on every OCPP reconnect [APPROVED; 16 October 2026]

**Every new OCPP connection/registration is a new observation generation**, whether caused by `wallbox-stationary` service restart, wallbox reboot, communication drop/reconnect or HA/WBM restart/reload. State may have changed while disconnected, then become constant again. **The Wallbox Manager is responsible for initiating and coordinating initial synchronization on every new connection.** It detects WebSocket establishment (and a later `BootNotification` on that connection), invalidates previous-generation OCPP observations and marks the new generation as **initializing**. Once the station's `BootNotification` is accepted and OCPP request/response exchange is ready, the WBM starts the one-time inventory and runtime-state synchronization. A duplicate boot on the same socket must replace/cancel any obsolete in-flight initialization instead of running two concurrent bootstrap workflows.

**Before adopting last-value-held change-only operation for the new generation, obtain a full current baseline of all supported and currently known relevant state and measurements, including unchanged values.** The WBM tracks receipt/completeness by field and generation, fences previous-generation replies, and does not mark a required input ready merely because the WebSocket is alive. It is not sufficient to wait for the next *change*. Each input becomes eligible individually after its current-generation initial report/readback is available; an unrelated missing optional value must not block NETZ.

**Initiator versus delivery:** Connection establishment always originates at `wallbox-stationary` using its configured CSMS URL. **Synchronization initiation and orchestration belong to WBM**. For information already queryable through supported standard OCPP operations, WBM actively requests it (such as `GetBaseReport` for capabilities, `GetVariables` for supported Device Model actual values, or `GetCompositeSchedule` only where its transaction prerequisites are satisfied). The response to `GetBaseReport` is **inventory, not a complete live-metering snapshot**. For station-originated messages such as `MeterValues`, `StatusNotification` and `TransactionEvent`, WBM must explicitly coordinate a **guaranteed initial publication** by the station: a verified supported request/trigger if one exists, otherwise a station-side bootstrap emission after registration. The station's data delivery may be push even though WBM owns the lifecycle trigger. **Do not presume that `TriggerMessage` or an arbitrary meter-value read request is supported** without a separately verified OCPP interface.

The initial contract covers **all available per-phase current/voltage/power and energy readings** (including unchanged L2/L3 = 0 A), connector and reported charging state, known real transaction identity/lifecycle, and supported authority/charging permission, physical phases and operating-point evidence as available. Unsupported or currently unobservable fields must be explicitly unknown/pending, not invented or carried over as live values. The interface must be documented field by field, with a bounded initialization result (ready / missing / unsupported) rather than indefinite silent waiting. Once complete for relevant fields, the normal path consumes subsequent changed reports without recurring snapshot polling.

A CP-blocked wallbox may initially report its **own last known connector status**, which is acceptable as a *reported station state* under PV-OCPP-05 but not proof of a fresh physical unplug measurement. Values from the previous OCPP connection cannot be automatically treated as current-generation measurements merely because no change was reported. Each profile or command can proceed once **its own mandatory initial measurements and established authority/transaction/electrical response requirements** have been obtained; a missing unrelated PV input does not prevent independent NETZ operation.

**Existing implementation and interface gap:** In `wallbox-manager/develop`, [`CentralSystem.on_connect()`](https://github.com/ekkehard-lutz/wallbox-manager/blob/develop/custom_components/wallbox_manager/protocols/ocpp/common/transport.py) already runs for every accepted WebSocket connection and `Runtime.connect()` creates a connection generation. [`DiscoveryAdapter.register_boot()`](https://github.com/ekkehard-lutz/wallbox-manager/blob/develop/custom_components/wallbox_manager/protocols/ocpp/common/adapter.py) handles `BootNotification` and starts `GetBaseReport` **after the boot response**; known reconnects also request discovery. **This is not yet full runtime-data initial synchronization.**

In [`wallbox-stationary/develop`](https://github.com/ekkehard-lutz/wallbox-stationary/blob/develop/ocpp_runtime/runtime.py), the configured CSMS connection is established by the station and automatically retried following a dropped/failed session (subject to its registration-rejected terminal behavior). On service restart a new WebSocket session is required. Its [station](https://github.com/ekkehard-lutz/wallbox-stationary/blob/develop/ocpp_runtime/station.py) already sends `BootNotification` on registration and resets the remembered connector status, but `_meter_values()` first waits for its configured interval and `_refresh_physical_status()` may be gated (including during CP interruption). A complete immediate runtime snapshot or supported on-demand `TriggerMessage` has **not** been demonstrated. Implementing guaranteed initial reporting on the station or a supported request interface requires separate authorization; **do not claim it already works**.

**Acceptance:** Instrument the WBM's connect/boot/initialization lifecycle and prove **one active initialization per connection/boot generation**, not repeated forever or triggered by normal unchanged telemetry. Test a `wallbox-stationary` service restart, wallbox reboot, WBM/HA restart, and a transient network disconnect; after reconnect, WBM must **initiate synchronization itself** once the station has registered. Change values and connection state while OCPP is down, then leave every affected value **unchanged** afterward. WBM must receive/refresh the full supported baseline, including sustained 0 A on L2/L3, reported vehicle/charging status, transaction, permission, authority and verified phase/operating-point information. Missing or unsupported fields remain unknown/pending and no command uses previous-generation evidence. Verify that a deliberately late old-generation response is ignored. Retained 50 Wh/STOP provenance and authority are not reset/acquired by bootstrap; no automatic positive restart is implied.

This is an **inter-project interface requirement**, not authorization to modify/redeploy `wallbox-stationary` or to change the wallbox firmware.

---

## 7. Unified economic 50 Wh grid-import policy

**This section replaces historically different economic STOP rules.** It follows the five explicit architecture approvals (PDF pp. 570–575), the subsequently specified profile/restart rule (pp. 575–600), and `docs/pv-grid-import-policy-design.md` from beta.13.

### PV-ECO-01 – Scope and priority [APPROVED]

Unified economic STOP for **PV_MAXIMUM, PV_OPTIMUM, PV_SURPLUS with battery and PV_SURPLUS without battery**, provided the required **valid measured grid-import evidence** exists. It **replaces** the previous economic minimum-budget immediate STOP, separate 10 s minimum-import STOP and BALANCE/SURPLUS deficit timers. No second parallel economic STOP. SoC protection, technical protection limits, authority, permission, OCPP lifecycle, startup eligibility and existing technical retries are **not** replaced by 50 Wh.

### PV-ECO-02 – Dynamic comparison threshold [APPROVED]

`T_import = M / 2`, where `M` is the **currently actually executable, verified positive minimum charging point in watts**. At 230 V and 1p/6 A, approximately 690 W; at 3p/6 A, approximately 2,070 W. A changed actual phase capability or voltage changes the threshold from its causal observation time, **not retroactively**. The 50% value is not a fixed power setting.

### PV-ECO-03 – Exactly which energy counts [APPROVED; configurable budget amendment]

**25 October amendment:** The fixed nominal threshold of 50 Wh is replaced by `pv_grid_import_budget_wh` (default 50 Wh), shared across all PV profiles. Its value is strictly positive Wh; the energy integration gate and counted power do not change. See PV-TUNE-05/06/09.

When **actual measured positive grid-import power `P_import > T_import`**, the **entire** grid-import power over the supported duration counts towards the budget, rather than just the portion above T_import. For `P_import <= T_import`, do not integrate; already accumulated energy initially remains. Budget exhaustion at the **nominal 50 Wh** threshold triggers the economic OFF request. It is a **command-trigger threshold**, not a guaranteed physical energy ceiling. Later **PV-ECO-09** approves approximate rather than exact Wh detection, without intentionally increasing the nominal budget.

### PV-ECO-04 – Reset / dynamic interval [APPROVED]

Only **strictly more than 15 seconds** of uninterrupted **supported** grid import `P_import <= T_import` resets the budget. Exactly 15 seconds is insufficient. Brief periods below the threshold pause accumulation without deleting the budget. A phase/voltage change changes the threshold prospectively and does not arbitrarily reset accumulated energy.

### PV-ECO-05 – Last-value-held validity and causality [APPROVED; revised 16 October 2026]

**The prior 10-second source-time and acceptance-time report-gap limits are explicitly REMOVED.** We assume reliable change-oriented measurement delivery: a **valid existing value remains unchanged and valid until a change, explicit `unknown`/`unavailable` indication, or applicable source/connection invalidation**. Not receiving a new report for 30 seconds, 30 minutes, or longer is **not** a measurement gap and must not interrupt automatic energy estimation or trigger additional polling.

During valid, actively eligible PV charging, the regulator uses current **unsmoothed grid-import power** to estimate import energy once per configured regulation tick and may count elapsed eligible time even when the last input value has not changed. Likewise, **strictly more than 15 seconds** of continuously valid `P_import <= M/2` may reset the budget without further measurement changes. No new physical source report is fabricated by sampling an unchanged value; repeated identical messages must not cause double integration or duplicate regulation.

**Known invalidity remains meaningful:** an explicitly unavailable/unknown required entity, missing mapping, unusable value, invalid transaction/authority/permission or other required execution evidence interrupts eligibility. During these periods and during Home Assistant/WBM downtime or before valid initial/resynchronized state, **do not** integrate energy, advance the reset, backfill previous intervals or grant a new budget. Keep persisted scope, accumulated Wh, STOP/restart provenance and qualified economic conditions intact. An OCPP reconnect requires its existing command/physical evidence reconstruction, not a guess based on old physical status.

**Causality:** changes or explicit invalidations take effect prospectively from their accepted/current observation context. Count supported elapsed time at most once; the exact per-source sample-generation pairing, <=10 s acceptance/source gaps and exact Watt-second arithmetic of beta.13 are no longer current *economic accounting* requirements. This does not weaken the independently required verification of requested physical current/phases, delayed ACK fencing, CP authority/transaction rules or electrical limits.

**Supersession:** Later explicit [16 October decision](decision-history.md#16-october-2026--remove-10-second-metering-gap-rule-and-separate-profile-availability-approved) replaces the formerly APPROVED and TECHNICALLY SPECIFIED 10-second accounting contract. Earlier tagged sources and decision-history quotations remain historical evidence, not parallel active policy.

### PV-ECO-06 – Economic minimum continuation without D [AGREED; execution tests pending]

**18 October WBM-ARCH-01/09/11 precedence remains binding:** Stationary owns electrical safety; WBM owns economic policy. With the former discharge budget `D` withdrawn, there is no longer an “above D only at exactly M” test. A technically feasible positive minimum is still relevant when balancing a brief import against a costly zero-current pause/restart; continuation at a lawful requested minimum is governed by valid SoC/manager-release/authority/transaction eligibility and the **same** 50 Wh grid-import policy. Do not react to grid import with a separate immediate STOP merely because storage power momentarily exceeds an arbitrary old D. This does not expand hardware current or phase limits, or bypass confirmed command/response distinctions.

**25 October choice:** For **FAST**, when the economic ideal is positive but below the currently feasible positive minimum, continue/offer that minimum while SoC/eligibility permits; the common Wh budget handles import. **BALANCE** initially does likewise, but below `T-H/2`, at truly applied **1p/6 A** and valid battery discharge following startup settling, STOP A requests 0 A (PV-BAL-06). Independently, `SoC < T-H` causes SoC STOP. A desired **0 W** does not by itself initiate a new positive charge. An actual phase-switch lockout may make the 3p minimum the currently executable one (PV-PHASE-01/02); never infer an available 1p point or immediately STOP solely due to phase lockout. Preserve WBM-PV-RELEASE-02/03 and verify actual transition behavior; no third economic STOP mechanism.

### PV-ECO-07 – STOP provenance and transaction binding [APPROVED + TECHNICALLY SPECIFIED]

Grid-import energy, activity/pending/in-flight/paused state, STOP cause and restart reference are **session- and authority-bound**. Persist them in Home Assistant Store where the same entry, wallbox identity, authority, connector, external OCPP transaction and fixed policy-context data can be proven. Neither reload nor profile change/reselection of a PV profile may blindly reset a consumed budget. During downtime, **do not** integrate energy or advance the reset interval. Uncertain recovery permits no automatic positive point. Explicit OFF, NETZ scope exit and proven genuine transaction end close the scope under implemented rules. Late storage operations or delayed ACKs must not undo a newer OFF.

Under PV-OCPP-04, general permission without a real transaction creates no accounting scope and clears no history. The approved Q1/Q2 decisions in [section 6.4.4](#644-transaction-bound-control-and-economic-accounting) require prospective real-transaction-scoped observational accounting, reconciled persisted history and explicit diagnosis of accepted unattributed flow; no retrospective pre-identity replay or reset is authorized. Observational activation cannot authorize restart or clear STOP provenance. Recovery inhibition of automatic positive points does not mean general permission must await transaction preparation.

### PV-ECO-08 – Economic restart after configured import-Wh STOP [25 OCTOBER OPERATOR DIRECTION]

**Confirmed, non-D restart evidence for the battery-equipped profiles:** After an economic OFF/STOP from exhaustion of configured `pv_grid_import_budget_wh`, the common economic restart guard may admit a new positive attempt when **either**:

1. Valid measured home-battery **charge power is strictly positive** (`P_storage_charge > 0 W`), even 1 W. The paired charge and discharge readings must be valid and consistently interpreted; a negative or missing signed battery flow is not positive charge evidence.
2. **FAST only**, with battery and active SoC authorization, valid measured **household demand excluding selected EV** is **strictly less than the new integration setting `pv_fast_restart_household_load_limit_w`**, default **500 W** (not 500 W inclusive). This alternative permits FAST battery-backed restart at night even if battery charging is 0 W or storage is still discharging. The 500 W default is the operator's site-specific assumption, **not** proof of an available physical discharge rate, new battery power cap or guarantee.

The second condition is **not** a new mandatory household sensor for battery-free PV_SURPLUS or NETZ. For FAST with battery, use an already mapped valid household-consumption signal only after excluding actual selected EV power when the mapped source includes charging. Verify units and mapping semantics. If a household signal is missing/invalid, the low-load branch cannot qualify, but the positive battery-charge branch remains usable when valid. Do not invent household load from a zero grid reading, nor add a new polling loop.

**Still mandatory for both triggers:** Existing PV-SoC eligibility (including STOP→BALANCE with PV start evidence and STOP→FAST at/above U), current operator charging release, authority/transaction/OCPP command and physically confirmed economic OFF prerequisites, and the **strictly >15 s continuously supported** below/equal-`M/2` import reset remain independent. Neither the budget reset by itself nor an unchanged prior condition grants immediate restart. Saved STOP provenance and transaction binding must survive reload and config edits. A new attempt uses the shared start-settling/response gate, normal economic Wh accumulation and normal hardware station constraints. Do not repeat the attempt every idle PV tick or bypass a failed/busy phase transition.

**Superseded:** The `D`-dependent capacity formula and the historical modeled-improvement **>100 W** requirement are not carried forward into this simpler restart policy. No new stored minimum positive watt improvement is required when either specified restart evidence is present. Source-value changes or forecast changes alone, with neither valid branch true, do not authorize a post-economic STOP restart. In particular, battery-supported FAST at night must not require current PV production.

**25 October operator decision — observe retries before adding restrictions:** Do **not** introduce an additional WBM anti-restart hold, mandatory new post-STOP sensor-value transition, one-shot latch or retry backoff merely to pre-empt a hypothetical repeated-start loop. Once the existing import-energy reset, confirmed previous OFF, SoC/authority/transaction eligibility and one of the two stated energetic restart conditions are valid, a new eligible attempt may proceed even if the positive battery charge or low household load was already present before the preceding STOP and remains unchanged. Preserve all **existing station-imposed actual re-enable/phase-switch lockouts**, guarded OCPP command fences and shared startup behavior; do not evade them. Observe and diagnose real start/STOP cycles in hardware testing. Only if a practical repeated restart problem occurs should a new anti-oscillation rule be discussed and separately approved. Test that STOP still takes effect and station lockout is respected; do not make an extra retry-limiting rule a prerequisite for implementation.

---

### PV-ECO-09 – Approximate configurable economic import-energy intervention [APPROVED; 25 October parameter amendment]

**Current rule:** Estimate valid actual grid-import energy and trigger economic STOP at the configured `pv_grid_import_budget_wh` (default **50 Wh**) on the first tick observing its exhaustion. The old fixed numeral “50 Wh” below describes the approved default, not an unchangeable runtime constant. Preserve the already-approved timing tolerance, eligibility, M/2 threshold, >15 s reset and persistence.

The **nominal 50 Wh** total measured grid-import threshold above the applicable `M/2` comparison stays in force as an **economic OFF-command trigger**. Product behavior does **not** require exact rational arithmetic, bit-exact Watt-second accounting or sub-cycle reconstruction. A computationally simple estimate from the latest **valid, unsmoothed grid-import power** and control-loop time is acceptable, for example `estimated_wh += grid_import_w * elapsed_seconds / 3600` on eligible cycles. Start the guarded OFF command process **on the first cycle that detects the nominal threshold**, rather than delaying deliberately or substituting a new 100 Wh setting. Physical energy may in practice have reached **60, 80 or 100 Wh** due to measurement, cycle, command or station delays; no hard maximum or precise physical 50 Wh guarantee is asserted. Preserve observability of the estimated amount and the requested, accepted and physically confirmed stop.

**Scope of supersession:** This changes the **required arithmetic precision** and accepts delayed real-world detection; it does not abolish the actual charging/transaction/authority scope, the strict **>15 s** supported reset condition, the actual `M/2` comparison, recorded STOP/provenance, persistence, model-based restart inhibition, source availability or OCPP safety/readback gates. The earlier statement in PV-ECO-05 that the existing implementation uses **exact watt-seconds** remains a historical implementation detail, not a future product requirement.

**Later 16 October decision resolves this compatibility question:** revised PV-ECO-05 removes the former 10-second report-gap and distinct-generation prerequisites for economic integration. Successive ticks can count elapsed time at one unchanged **still-valid** input. Do not integrate during known unavailability or downtime, or bypass context/transaction/authority/physical evidence requirements.

Source: [16 October 2026 user decision](decision-history.md#16-october-2026--smoothing-units-and-approximate-50-wh-trigger-approved).

---

## 8. Technical validation, diagnostics and quality contract

### PV-QA-01 – Do not confuse diagnostics with effectiveness [APPROVED]

**New 25 October diagnostics amendment:** [PV-DIAG-01–10](pv-decision-diagnostics.md) defines minimal **Level 2: meaningful regulation state/decision changes**, with the small subset of measurements actually consumed by that decision, and no per-tick or per-source log stream. Existing OCPP Level 0/1/2/3 classifications remain compatible.

Logs distinguish planned, sent, accepted, confirmed and **physically measured effective** operating points. OCPP `Accepted` does not establish an appropriate grid/battery balance. Diagnostics include source generation, time basis, measurement age, economic `M`, `T_import`, energy in Wh, reset progress, previous STOP reference, state transition, request/applied point and reason. Diagnostic levels 0–3 remain distinct; level 3 must not trigger a second sensor query or cause unnecessary load.

For PV-OCPP-04, also distinguish permission intent/ACK/readback, transaction identity, physical connection/charging state, CP observability, OCPP connectivity, observation source/time/freshness and missing reconstruction prerequisites. An accepted permission command is neither profile confirmation nor physical vehicle evidence.

Evidence: PDF pp. 99–119, 235–252, 529–535; `docs/pv-surplus-profile.md`, `docs/pv-grid-import-policy-design.md`; PV-OCPP-04 for the new diagnostic distinctions.

### PV-QA-02 – Mandatory regressions [APPROVED INVARIANTS + PROPOSED TEST CASES]

1. Shared SoC matrix with exact S/L/M/U boundaries, strict SoC<S STOP and strict SoC>M BALANCE re-entry, nighttime FAST, START versus continuation, identical PV_MAXIMUM/PV_SURPLUS results for the same T.
2. DYNAMIC/FINISHED latch, local midnight/DST, forecast steps in either direction, 300 s replanning, stale forecast without unnecessary Solcast requests.
3. FAST: bounded active exploration while grid flow is near zero (also night/high SoC), no requirement that every upstream source report a new generation each tick, at most one outstanding positive adjustment, observed-response-aware increase, quick proportionate grid-import reduction, no use of former D, no indefinite response lock or 9 A plateau absent a real policy explanation.
4. BALANCE: test criterion A with `SoC < T-H/2`, actual confirmed/applied 1p/6 A, completed startup hold and negative `P_charge-P_discharge` resulting in 0 A at that tick; check battery=0 W, battery charging, unavailable entity, 1p/6A only requested/ACKed, phase lockout, startup still settling, and independent strict `SoC<T-H` all **do not wrongly enter/hold criterion A**. Also test: signed charge/discharge power and verified sign/units, SoC recovery when below T, battery hold and discharge-limit conditions, small reversals and large PV/household steps, asynchronous EV/battery observations, no additional battery smoothing (historical cycle-based smoothing compatibility retained where independently needed), both-positive source zeroing, no self-induced 5 s 6 A/14 A oscillation, and economic grid fallback without a competing loop.
5. 1p↔3p phase switching with lockout; retain 3p minimum during confirmed 3p restriction; no immediate reversal or fabricated lockout-expiry time.
6. Dynamic technically **reachable** minimum and 50% threshold, including changed voltages/phase evidence; no minimum exception above M.
7. Configurable grid-import Wh budget (default 50): complete valid raw grid-import power only for P>T; strictly >15 s reset; 30 s or 30 min **without changes** still permits counting a last-known valid value. No 10 s report-gap limit. Test explicit unavailable/unknown, downtime, duplicate messages, startup and OFF/late-ACK behavior.
8. Economic STOP priority: no old 10 s, 90 s, H-minimum or other independent economic OFF path; genuine SoC/safety STOPs remain.
9. Persistence: HA restart/reload, active scope, genuine transaction end, profile change, authority/connection change, late ACK/checkpoints and failure cases.
10. Restart: exercise previous STOP reference, >15 s economic reset without unconditional restart, valid actual-OFF evidence, and the newly agreed battery charge >0 W (including 1 W) OR FAST-only valid household load below configured default 500 W (strict) even at night. The historic D model / >100 W improvement rule is superseded; **do not invent a new anti-retry latch, new timed backoff or compulsory changed-sensor event**. Verify station-imposed reenable lockouts.
11. Battery-free SURPLUS using **only** grid import/export + EV mapping; incomplete configuration remains blocked.
12. No automatic Local→Remote takeover, no unauthorized commands, authority takes status priority.
13. PV-OCPP-04 acceptance matrix: permission without transaction/preparation; retained legal current; normal live vehicle-absent standby; CP-blocked/probe/disconnection evidence; all restart/reconnect cases; explicit OFF, transaction-scoped persistence and unresolved interface limitations. Keep command request, ACK, confirmed state, physical observation and dashboard presentation separate in verification.
14. PV-ARCH-05/06/08: replay repeated/equal and rapidly changing OCPP and HA measurements with WBM observing only or without authority; no background PV regulation, redundant OCPP targets or expensive all-entity reactions. Compare HA Core CPU WBM enabled/disabled and record profiler evidence, not an invented numeric threshold.
15. PV-ARCH-06: while PV control is active, apply multiple input updates and an unplugging event within one interval; verify exactly one per-tick PV decision, latest consistent snapshot, **no added BALANCE smoothing**, deduplicated commands and preserved FAST/BALANCE asymmetry, SoC/economic decisions and phase-switch lockout.
16. PV-ARCH-07: inject authority revocation, OCPP disconnect, critical fault and late ACKs between ticks; verify prompt executor invalidation, per-dispatch live safety fences and independent physical feedback. Verify reconnect/readback without downtime accounting or accidental restart.
17. PV-BAL-03/07: validate **no smoothing in the new BALANCE storage-flow loop** regardless of legacy N; charge/discharge `(positive,0)`, `(0,positive)`, `(0,0)` and `(positive,positive)→effective 0 W`, missing input validity, and no additional Modbus/HA requests. Any remaining historical seconds-based smoothing must not be reinterpreted as cycles or silently applied to BALANCE.
18. PV-ECO-09/05: estimate Wh from latest valid raw grid import at each eligible tick; request OFF at first detected configured Wh-budget crossing (default 50 Wh), permit unavoidable physical delay and retain >15 s reset, history and restart checks. Repeated equal values are not duplicate charge events; extended unchanged valid input must count, actual unavailability or downtime must not count.
19. PV-ARCH-03/PV-ECO-05: make Fronius inverter, Smart Meter and BYD input values `unknown`/`unavailable` independently; affected PV profile becomes ineligible, independent NETZ remains functional under its own OCPP/authority prerequisites, and configured battery-free PV_SURPLUS remains possible with valid grid/EV inputs. No invented zero value or automatic NETZ switch.
20. PV-OCPP-05: Wallbox keeps reporting connected during CP pause; unplug while CP blocked. WBM shows last station report with no synthetic expiry/grey overlay until the next successful hourly CP probe triggers an explicit changed report; never infer unplug on 0 A/no transaction.
21. PV-OCPP-06: Verify the WBM initiates exactly one bootstrap after each accepted connection/BootNotification, including wallbox/station-service/HA restart and reconnect after network disruption. Populate all supported state changed-while-offline but then stable (including L2/L3 at zero); test supported readback versus guaranteed station reports, unavailable fields, missing query capability, generation fencing of late responses and return to change-only operation.

22. PV-DIAG-01–10: with level 2, log only changed decisions/reasons or genuine state transitions with their actual decision-input subset and candidate/applied distinction. Across hundreds of unchanged ticks and unchanged HA measurements, produce no ongoing level-2 PV logs or recorder entities; threshold crossings must log once. Level 3 permits bounded opt-in diagnostic detail without new polling. Confirm no credentials, raw payloads, transaction IDs, duplicated OCPP transition messages, or spurious warning/error severity.
23. PV-STEP-01–06, PV-RESP-01–08, PV-TUNE-01–12: test persisted/editable defaults (3 cycles, 10 cycles, ±100 W, 0 reduction-delay cycles, 50 Wh), removal of both obsolete delay entities/options/stored fields, independent regulation interval (seconds), no reset on active budget edit, immediate economic STOP precedence, and parameterized boundary behavior, plus uptake gate default 50% and FAST economic house-load restart default 500 W. Also verify one increase every three eligible ticks, phase-first series exhaustion before switching, mandatory 10-cycle no-ordinary-adjustment startup hold, measured EV >=50% gate for further increases, bounded downward correction after startup and during ongoing temporary underconsumption, urgent intent/SoC STOP irrespective of startup hold, and non-duplication of OCPP commands. The configured economic-Wh STOP **overrides** ordinary startup/reduction timing as already approved.
24. Require both distinct Fronius storage charge/discharge mappings for battery BALANCE; one zero is **not** proof that the other sensor is available. Both positive source readings create effective 0 W only for the battery-regulation calculation; invalid readings stay invalid. No new mandatory battery/household sensor for battery-free PV_SURPLUS or NETZ.

### PV-QA-03 – Hardware acceptance criteria [OPEN]

The **intent** is binding, rather than an unsupported fixed number: fast economically useful discharge in FAST; a stable control loop and appropriate disturbance response in BALANCE; no unnecessary zero-current pauses with 600 s charging-reenable lockout; traceable grid-import energy rather than instantaneous peaks. Before the next algorithmic change, define realistic target metrics for command changes, maximum deficit-correction time, energy utilization and restart reliability, preferably from controlled load-step/cloud scenarios.

---

## 8A. Cross-references and supersession review (25 October 2026)

This review changes **only the future PV target contract**, not beta.16's NETZ execution, Stationary, HA entity identities or current card display.

| Existing text or implementation | New decision / disposition |
| --- | --- |
| PV-FAST-02 budget `D`, beta.18 `pv_regulators.py` hard_max, FAST gain/grace | **Superseded for future regulation:** grid-point feedback + bounded active probing; no D ceiling. Existing module is historical reference, not current develop runtime. |
| PV-BAL-01 PV generation minus total load, beta.18 `balance_request()`/battery budget | **Superseded as primary battery-equipped controller:** signed battery-flow BALANCE with a grid economic fallback. Battery-free PV_SURPLUS PV-ARCH-02 unchanged. |
| PV-BAL-03 N-tick smoothing formerly over PV/total load | Historical N-cycle measurement smoothing does **not** apply to new BALANCE battery feedback; **no extra smoothing** until separate approval after testing. Any other legacy smoothing use/migration must be reviewed independently. |
| PV-ARCH-03 mapping `storage_discharge_power` | Keep discharging map and **add `storage_charge_power`**. Per PV-BAL-07, both positive values yield an effective 0 W only in WBM's BALANCE calculation; unknown/invalid inputs remain invalid. |
| PV-ECO-06 minimum-exceeds-D exception and PV-ECO-08 D restart math | D-dependent clauses superseded. Minimum-point behavior PV-ECO-06 and battery-charge/FAST-low-house-load economic restart PV-ECO-08 now agreed, with configured Wh budget, existing reset/OCPP gates and **no new anti-retry latch**. |
| WBM-ARCH-01–12 and WBM-RELEASE-01–14 / WBM-PV-RELEASE-01–07 | Preserve Stationary physical safety, manager release Start/Stop, OCPP fences and the fact that a computed 0 A is not operator OFF. |
| WBM-HS-UI-01–15 | Lovelace PV_OPTIMUM/MAXIMUM targets remain `—` until a canonical effective target exists; percentage formatting and preserved HA entities unchanged. |
| Diagnostic Level 2 previously OCPP significant status changes only | Extend with **deduplicated meaningful PV regulation decisions**, not raw measurements/tick logs: PV-DIAG-01–10. Retain previous OCPP log families. |

**Provenance:** Reviewed user approval on 25 October 2026 of the proposed shared FAST/BALANCE simplification, earlier v0.3.2-beta.18 files `pv_soc.py`, `pv_optimum.py`, `pv_regulators.py`, Stationary `services/dynamic_amps.py` and `control/amps.py`, and openWB software2's public EVU-point/PV-battery priority documentation (https://wiki.openwb.de/doku.php?id=openwb%3Avc%3A2.1.9%3Asoftware%3Aeinstell-konfig%3Aladeeinstellungen%3Apv-laden-speicherbeachtung). Those systems are **conceptual comparisons**, not imported implementation, normative external dependencies or evidence of real WBM performance.

## 9. Observations from beta.13 – explicitly no silent target-behavior changes

- **OBS-13-01:** In test `pv6`, FAST remained at 1p/9 A for a long period. A **candidate deviation** from PV-FAST-01, assessable only after examining `D`, actual `B`, EV/site evidence, confirmed response, regulator hard budget and startup/increase fences. A justified conservative reserve had previously been accepted.
- **OBS-13-02:** `PV_BALANCE` repeatedly oscillated between low and higher ampere values during 15:24–15:34 and 15:58–16:08. Relevant target contract: PV-BAL-02 and PV-QA-02. The specific subpath causing repeated direction changes is not yet proven. Causal analysis first, then a minimal fix.
- **OBS-13-03:** Dynamic T increases, `FINISHED` and SoC-related charging pauses are **intended strategy**, as clarified by the user. Pursue only concrete PV-PLAN/PV-SOC violations.
- **OBS-13-04:** Fronius, EV and grid values are asynchronous physically and through HA; apparent negative/positive power peaks are not direct proof of energy consumption. Assess the WBM decision against **valid data available at that time**; assess grid energy from separately provable measurement.
- **OBS-13-05:** The beta.13 source description reports 2,420 Python tests/145 frontend tests without hardware approval. The later `pv6` test is a **new hardware finding**, rather than retrospective confirmation of every automated scenario.

---

## 10. Explicitly superseded rules – do not restore to the target matrix

| Historical approach | Later binding replacement | Source |
| Manager physical safety proofs, ordinary input TTLs (including 900 s forecast), mandatory pre-command readbacks | Station safety and held observations; explicit supersession register covering PV-ARCH/PLAN/FAST/BAL/PHASE/OCPP/ECO/QA clauses and acceptance matrices | [WBM-ARCH-01–12, 18 October 2026](architecture-responsibility.md) |
|---|---|---|
| Day starts at PV >=100 W (sometimes 5 min) or sunrise | First `PV > household consumption excluding the selected EV` | PDF pp. 1, 98–114 |
| Sunset or sunset minus 1 h as mathematical planning horizon | Remaining **local calendar day until midnight**, with `FINISHED` once remaining PV <= remaining household demand | PDF pp. 98–119 |
| Nighttime: never FAST without PV | FAST from SoC >= U, also at night without PV | PDF pp. 92–99, 133–145 |
| STOP already at SoC < T | SoC state matrix with `L = T-H/2`; previous state matters | PDF pp. 133–145 |
| Separate SoC logic for Surplus, Optimum, Maximum | Shared SoC policy and regulators; profile supplies T | PDF pp. 140–148, 579–583 |
| Unlimited FAST minimum charging regardless of economic budget | Only **exact technically executable M** as exception, unified 50 Wh STOP decision | PDF pp. 568–575, 599–600 |
| SoC STOP solely due to phase lockout below T / separate 10 s grid-import interval | Shared SoC matrix and shared dynamic M/2 / 50 Wh policy | PDF pp. 477–480, 539–545, 599–600 |
| `hard_budget_pause`/H-minimum as **economic** immediate STOP | Exactly M may continue charging; economic STOP only through 50 Wh | PDF pp. 566–600 |
| 90 s deficit STOP in BALANCE and SURPLUS | Unified measured 50 Wh decision; `pv_stop_delay` is removed as an obsolete option for the new PV milestone | PDF pp. 542–545, 599–600, 606–613 |
| Time-based 10 s STOP above M/2 | Historical evolution: 50 Wh economic trigger, reset strictly >15 s; the later 10 s **report-gap** limit was itself superseded on 16 October 2026 by PV-ECO-05 (last valid value remains valid until changed/unavailable). | PDF pp. 539–545, 571–575; later approved decision |
| Additional 50 Wh gate *after* old STOPs | **Replace**, rather than overlay, old economic STOPs | PDF pp. 566–571, 599–600 |
| PV surplus alone required for restart (also with home battery) | Battery-capable **model-based** condition, >100 W improvement, normal profile eligibility | PDF pp. 572–600, 606–609 |
| PV_SURPLUS always requires separate PV/household-consumption entity | **Battery-free** SURPLUS needs grid import/export plus EV measurement | PDF pp. 575–580, 608–612 |
| Every sensor/setpoint drift cancels OCPP ACK/dispatch | After dispatch, process ordinary drift next cycle; actual live fences remain | PDF pp. 103–119, 524–527 |
| New 20–30 s interval as an easy oscillation “solution” | Not approved; assess existing source/response/asymmetry logic first | Discussion current at original reconciliation; no final approval in earlier discussion |
| General user permission pending until transaction-bound economic point prepared | General permission can precede preparation; transaction-bound `TxProfile` validation and all unrelated protections remain | 15 October 2026, PV-OCPP-04 |

*SUPERSEDED applies only to the named functional rule. Independent actual protection, device and authority conditions remain in force.*

---

## 11. Conflicts/obsolescence in existing documentation – review tasks, not code instructions

1. `docs/pv-optimum-design.md` itself notes that old FAST/minimum/gap rules were replaced by beta.9. Its old minimum and PV-wait sections must **not** override final beta.13 economic STOP policy.
2. `docs/beta9-implementation-report.md` covers a conservative economic H limit in its former immediate-STOP paths; the beta.13 minimum exception and unified 50 Wh take precedence for economic STOP. Source-generation and actuator-response discipline remain protected.
3. `README.md` and older profile/SoC documents contain older PV_MAXIMUM (“deferred”), minimum and `pv_stop_delay` wording. Mark these as historical or update them **in a separate documentation cleanup**, without redesigning logic.
4. `docs/pv-surplus-profile.md` documents older 90 s STOP timers, no longer normative for the economic path replaced by beta.13.
5. Historical audit `docs/pv-decision-logic-audit.md` was initially local/unversioned; the final implemented rule is available **in the tagged release** at `docs/pv-grid-import-policy-design.md`. The second source is primary for changes.
6. **OPEN historical T-versus-L conflict:** Earlier SoC wording used T as STOP threshold; PV-SOC-02 and the later approved matrix use L. The historical conflict remains visible in decision chain B and the supersession register. Do not silently reconcile any remaining contradictory older statement, infer that T has been reinstated, or claim all source/code inconsistencies resolved. Overall V3 review must track residual contradictory sources separately. Also, `M` denotes an SoC threshold in section 2 and minimum charging power in sections 6–8; `L` denotes an SoC threshold in section 2 and total consumer power in the restart formula. Preserve these context-dependent original symbols; do not merge their meanings.
7. **Targeted 15 October supersession:** Prepare-before-permission wording in current architecture/profile/recovery descriptions is historical at that precise dependency. PV-OCPP-04 now governs general permission. It does not rewrite immutable beta.13 provenance or establish that current code implements the new behavior. The subsequent approved Q1/Q2 decisions in section 6.4.4 resolve accounting attribution and accepted uncertainty; Q3 in section 6.4.9 resolves the OFF presentation policy. Concrete implementation, physical readback/provenance limitations and deployed hardware behavior remain subject to separate review and verification. Earlier design/analysis reports retain their historical OPEN wording; the later decisions govern the target contract.

---

## 12. Development and test procedure (explicitly agreed in the earlier discussion)

**PV-PROC-01 – Codex is an execution tool [APPROVED].** Discuss architecture and product decisions here first; give Codex **complete English-language** execution tasks. Codex must not resolve unanswered fundamental questions unilaterally through code. The user explicitly required “Codex tasks always in English” (translated quotation, PDF pp. 1, 405).

**PV-PROC-02 – HACS prerelease rather than direct installation [APPROVED].** Iterative hardware testing follows an explicitly authorized GitHub **prerelease** and installation through **HACS**. **No** manual direct installation/overwrite of the HA integration as a supposedly convenient test path. `main` and the latest stable release remain untouched until separately approved for stable release. Feature implementation, review, merge, tag/release, HACS installation and hardware test require separate authorization (PDF pp. 441–451, 615–619).

**PV-PROC-03 – Proportionate tests and source evidence [APPROVED].** Realistic focused hardware tests are desired; no unnecessarily elaborate snapshot/backup procedures merely for a development version. Versioned Git states allow code rollback. Test logs from `.analysis/` remain local and do **not** belong in the repository without a separate decision. Each analysis must assess the complete temporal sequence and sensor/command causality, rather than isolating one STOP (PDF pp. 221, 441–448, 529–531).

**PV-PROC-04 – Fronius/Modbus time reference [OPEN EXTERNAL DEPENDENCY CHECK].** The earlier wallbox OCPP MeterValues change from 10 s to 2 s and the Fronius Modbus/measurement timing investigation were separate instrumentation steps. A 2 s OCPP report does not automatically make Fronius/battery/grid values simultaneous. Investigation of a physical `observed_at` contract may belong in `fronius-pv-manager`; **no unauthorized polling changes there or changes to `wallbox-stationary`** as a side effect of a WBM fix (PDF pp. 307–320, 360–410, 529–535).

---

## 13. Proposed ongoing maintenance

- After review, version this file as the **single current functional PV target specification** at `docs/specifications/pv-regulation.md`, with continuous version history and immutable IDs.
- For each code iteration, use a short `docs/changes/PV-YYYY-NN.md` file or ticket covering: `Requirements`, `Observed actual behavior`, `Changed normative rule?`, `Affected code`, `Tests`, `Hardware verification`, `Explicitly superseded statements`.
- Retain history, including PDF page references, as decision evidence; technical implementation reports must not create new product policy of equal standing.
- Review/corrections take place on a dedicated branch, with full regression and no automatic changes to `main`, stable tags or wallbox firmware. Authorize hardware test and release separately.

## Sources

### Original discussion

- *Wallbox Modbus-Abendauswertung.pdf*, 619 pages, uploaded by the user on 13 October 2026 and **actually evaluated in the original source-reconciliation revision**. Page references refer to the PDF printout, rather than chat message IDs. Some originally linked Codex logs/patches are not contained in the PDF itself. Its filename is retained verbatim for identification.

### Normative or technical repository sources (tag `v0.3.2-beta.13`)

- [`docs/pv-grid-import-policy-design.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/docs/pv-grid-import-policy-design.md) – latest economic decision, energy accounting, restart, persistence, tests.
- [`docs/pv-soc-policy.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/docs/pv-soc-policy.md) – shared SoC thresholds and transition matrix.
- [`docs/pv-optimum-design.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/docs/pv-optimum-design.md) – daily forecast, state phases and historical FAST evolution.
- [`docs/phase-lockout-continuation-report.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/docs/phase-lockout-continuation-report.md) – phase restriction, minimum and stabilization.
- [`docs/beta12-implementation-report.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/docs/beta12-implementation-report.md) – confirmed/changed D1–D4 and status priority.
- [`docs/beta9-implementation-report.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/docs/beta9-implementation-report.md) – physical measurement generations, actuator response and conservative budget (partly superseded later).
- [`README.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/README.md) – integration settings (check older statements against later decisions).

### Later individually approved decision

- Explicit user-approved documentation task of 15 October 2026: general charging permission, retained legal startup current, physical CP observability and restart recovery; recorded in [decision-history.md](decision-history.md#15-october-2026--charging-permission-connection-readiness-and-recovery-approved). No new PDF page numbers are assigned to this later decision. Subsequent explicit approvals on the same date resolve Q1 prospective accounting, Q2 accepted unattributed-flow uncertainty and Q3 evidence-based OFF display; recorded separately in the [Q1–Q3 decision entry](decision-history.md#15-october-2026--subsequent-q1-q2-and-q3-decisions-approved). The 16 October architecture approval for event-fed observations, a single PV regulation tick and immediate safety exceptions is recorded in [the later history entry](decision-history.md#16-october-2026--event-fed-inputs-and-clocked-regulation-approved). These approvals authorize documentation only, not implementation or overall V3 sign-off.

**Approval note:** V3 is a repository-versioned **consolidated DRAFT**. Inclusion in GitHub is **not overall subject-matter approval** or an instruction to implement/deploy. Only explicit overall subject-matter approval makes it a normative requirements baseline. The individual PV-OCPP-04–06, PV-ARCH-05–08, PV-BAL-03 revision, PV-ECO-09, and revised PV-ECO-05/PV-ARCH-03 decisions are APPROVED; that does not approve V3 as a whole.
