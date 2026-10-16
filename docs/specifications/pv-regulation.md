# Wallbox Manager – PV Regulation: Consolidated Requirements Baseline V3

**Status:** DRAFT for overall subject-matter approval; **no new implementation instruction without review**

**Updated:** 16 October 2026; original source reconciliation: 13 October 2026

**Reference:** `wallbox-manager` v0.3.2-beta.13 (release commit `aaef76d41295cf0b0255933f72b6d65448f6864a`), GitHub branch `develop` / release tag `v0.3.2-beta.13`

**Work type:** Source reconciliation, English migration and separately approved PV-OCPP-04 (15 October) and PV-ARCH-05–08 (16 October) decisions; documentation only, no control-software, HA or station change.

**Primary sources:** User-supplied PDF printout *Wallbox Modbus-Abendauswertung* (619 pages, discussion of 4–12 October 2026), particularly pp. 1–19, 67–83, 92–119, 132–148, 209–246, 266–320, 401–449, 460–480, 501–528, 529–545 and 566–619; repository documents, **especially the latest** `docs/pv-grid-import-policy-design.md` from `v0.3.2-beta.13` (see source list below). PV-OCPP-04 is sourced separately to the explicit user approval of 15 October 2026, recorded in the [decision history](decision-history.md#15-october-2026--charging-permission-connection-readiness-and-recovery-approved).

**Evidence boundary:** The PDF printout contains the visible discussion text, but not automatically the full contents of every referenced local log, Codex artifact or hidden attachment. Requirements identified here as APPROVED were supported by the visible discussion or the specification released with beta.13. Whether beta.13 meets them at runtime is a separate assessment. The migration preserves this original evidence statement; it does not claim a new inspection of the external PDF or hardware logs.

## 0. Validity, precedence and change rules

- **APPROVED:** Explicit agreement in the discussion, most recently authorized by the user, or a technical clarification subsequently established and approved under that agreement. Remains valid until superseded by a later explicit decision.
- **TECHNICALLY SPECIFIED:** Concrete execution semantics from the final implementation documentation released with beta.13. This is not automatically a universal product requirement for future redesigns; changes must be justified.
- **HISTORICAL / SUPERSEDED:** Retained for traceability only; **do not** apply as a parallel requirement.
- **OBSERVED:** Finding from a hardware log or discussion. Without comparison against the target specification, this does not authorize a change.
- **OPEN:** No binding threshold or proven cause exists. Do not introduce an arbitrary new rule.

**Precedence in a conflict:** (1) Latest explicitly confirmed decision in the discussion → (2) its detailed, approved realization in the beta.13 design/review report → (3) older current specifications that have not been superseded → (4) older draft/implementation reports as history. This precedence governs the **target behavior contract**; actual behavior and code require separate assessment. The 15 October 2026 decision supersedes only the permission-preparation dependency specified in PV-OCPP-04. The 16 October 2026 decision replaces ordinary measurement-triggered PV power regulation with one clocked regulator (PV-ARCH-05–08), while retaining immediate safety and command lifecycle processing, evidence/freshness requirements and the existing economic policy. Neither decision approves the overall V3 draft.

**For every future change:** Identify the requirement ID, unchanged/superseded target behavior, observed actual behavior, deviation, effects on other IDs, concrete test cases, expected diagnostic evidence and, where applicable, *explicit* approval of a target-behavior change. No iteration may silently discard an approved requirement merely because a new log exists.

---

## 1. Functional architecture and profile availability

### PV-ARCH-01 – Shared regulator with a home battery [APPROVED]

All three PV profiles with a home battery use **one shared** SoC state machine, FAST/BALANCE power regulation, technical operating-point selection, economic 50 Wh logic and OCPP execution. The profiles differ primarily **in their choice of battery target SoC T**, rather than using three separately copied power regulators. **PV_MAXIMUM and PV_SURPLUS must produce identical charging decisions and OCPP operating points when T and all other parameters are identical.**

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
- Missing mandatory entities must not produce invented zero values or accidental permission. Temporary unavailability must be distinguished from permanently missing profile mappings.
- A real change in profile availability is product policy and requires separate approval.

Evidence: PDF pp. 575–580, 609; `docs/pv-optimum-design.md`, `docs/pv-grid-import-policy-design.md`.

### PV-ARCH-04 – Ownership boundary [APPROVED]

`wallbox-manager` owns profile policy, the charging operating point and OCPP control. The Fronius/BYD integration supplies measurements; Wallbox Manager does **not** control the home battery through a parallel Modbus path. The wallbox hardware owns CP, vehicle state and physical phase relays. A future energy manager must not create a second competing regulator. Reuse means **shared functions**, rather than duplicated algorithms.

Evidence: PDF pp. 1–19, 209–246, 579–583.

### PV-ARCH-05 – Two event-fed input interfaces and one observation snapshot [APPROVED]

Wallbox Manager has exactly two incoming observation interfaces: **(1) OCPP to the wallbox** for telemetry, station/transaction state and command responses, and **(2) internal Home Assistant state entities** for PV, household/grid, battery and forecast information. Both are treated as reliable event-fed sources for the information they actually deliver. Initialize known HA entity state and obtain supported wallbox discovery/readback once on setup, boot, reconnect or required recovery; thereafter maintain one canonical observation store. Subscribe only to relevant configured HA entities. Ordinary incoming events **update observations without running PV regulation**. Unchanged value reports may update timestamps/provenance without repeated business-state publication or control computation.

Keep value, identity, source time, receipt time, validity and OCPP connection/boot generation distinguishable. Communication liveness does **not** prove each sensor or physical variable is fresh. OCPP does not universally guarantee push reports for all variables; where confirmed state cannot be obtained through notifications, retain specifically justified **bounded** reads/readbacks. A HA dashboard may display meaningful new observations promptly without triggering a PV-control step.

### PV-ARCH-06 – One periodic PV regulation step with a current snapshot [APPROVED]

An active authorized **automatic PV profile** runs **one configurable regulation cycle**, for example every **5 seconds**; this is an illustration, not a new fixed timing parameter. Each cycle reads a consistent snapshot, applies the existing shared profile, SoC, FAST/BALANCE, electrical and 50 Wh policies, and submits a new target only when required. Ordinary changes in PV, grid, battery, vehicle connection or profile selection are reflected in the **next** cycle, not in an additional event-triggered control pass. An unchanged target must not cause redundant OCPP commands; queued and confirmed states remain separate. No competing duplicate PV loop or per-event power recalculation is permitted.

Smoothing, where justified, is owned by the regulator and samples configured inputs once per control cycle. For example three consecutive 5-second cycle samples form a three-element sliding average; they do **not** establish three independent physical OCPP/HA measurement generations. **PV-BAL-03** continues to define independent regulation-interval/averaging settings and smoothing scope (PV and total consumer power, not SoC, EV response, voltage or safety evidence). **PV-ECO-05** still requires source/acceptance timestamp continuity, at most 10 seconds each: reading the same cached value at consecutive ticks must never invent source evidence, counted Wh or supported reset time.

When WBM only observes, when authority is absent, or when no automatic PV control is active, there is **no background cyclic PV policy evaluation** merely because battery SoC or other inputs are configured. Fixed/manual control does not need a continuously running PV regulator. Genuine time-based obligations (e.g. roughly 300-second forecast planning, local-midnight transition, permitted dwell/lockouts) remain scheduled as needed. The older **PV-PLAN-04** allowance for immediate recalculation on defined measurement events no longer authorizes a parallel ordinary PV power-regulation path; immediate exceptional processing is limited by PV-ARCH-07.

### PV-ARCH-07 – Immediate safety and asynchronous OCPP execution [APPROVED]

Loss of control authority, OCPP disconnect and critical hardware/safety faults must invalidate control readiness promptly, **independent of the PV regulation tick**. The station remains responsible for physical CP and local protections. A normal unplugging/status event can be incorporated into the next PV decision, but queued/new commands still have to pass the existing live pre-dispatch authority, permission, connection, transaction, intent and safety checks. OCPP CALL/CALLRESULT/ERROR handling, command serialization, delayed acknowledgements, readback and confirmation of the **actual physical operating point** are asynchronous and separate from the regulator; protocol acceptance never substitutes for physical proof.

WebSocket Ping/Pong already supplies transport liveness; choosing to ping only after inactivity is an optional optimization, **not** a new correctness precondition. Liveness cannot extend individual metering/phase/voltage safety evidence indefinitely. Boot, disconnect and reconnect invalidate or resynchronize affected execution evidence. Preserve PV-OCPP-01–04, verified phase switching, CP lockouts, response/measurement causality, existing SoC/FAST/BALANCE safeguards and 50 Wh accounting/recovery restrictions; no new automatic authority takeover or unsafe restart.

### PV-ARCH-08 – Remove redundant work and validate performance [APPROVED architectural approach]

Implement the above architecture by **removing or replacing** redundant event listeners, periodic tasks, publications and duplicated regulation paths, not by adding another coordination layer. Separate OCPP input/recovery, HA observation intake, one PV decision engine and guarded asynchronous OCPP execution. Every implementation review must identify which old path becomes obsolete, preserve invariants with tests, and report simplifications/removals as well as additions. Diagnostics and HA state updates must not trigger fresh full-profile calculations when only presentation changes.

Use the proven clocked design of **wallbox-stationary** as a **conceptual reference, not drop-in code**: its [dynamic regulator](https://github.com/ekkehard-lutz/wallbox-stationary/blob/6edf3b0a8b46bc1a1f2c34f1e986c179915dd2f0/services/dynamic_amps.py) runs one T_TAKT loop, reads a measurement snapshot and buffered grid history, and conditionally applies a current target. That code still reads inverter values inside the tick and polls the HA forecast; its input acquisition is **not yet the event-fed interface required here**. Its PV loop also does not supply the required dynamic 1p↔3p selection, verified switching and phase lockouts of WBM.

Validate observation-only CPU cost with WBM disabled/enabled without takeover under comparable conditions, profile active-cycle computations, redundant entity publications, safety invalidation between ticks, and preservation of all earlier PV-OCPP/PV-ECO requirements. A numeric CPU pass threshold remains OPEN until measurement context is established; observed high CPU is not proof of a specific root cause. Source: [16 October decision](decision-history.md#16-october-2026--event-fed-inputs-and-clocked-regulation-approved).

---

## 2. Shared battery SoC state machine

### PV-SOC-01 – One target SoC T, one hysteresis parameter H [APPROVED]

`R` = reported minimum home-battery reserve, `H` = shared parameter `soc_hysterese`, default **2 percentage points**, `T` = profile-dependent battery target SoC. Valid settings satisfy:

`R + H <= T <= 100 - H`.

Three thresholds:

- `L = T - H/2` (lower STOP threshold)
- `M = T + H/2` (condition for starting with PV surplus)
- `U = T + H` (FAST entry threshold)

Do **not** introduce separate configuration parameters for “hysteresis” and “buffer”.

Evidence: PDF pp. 133–145, 148; `docs/pv-soc-policy.md`.

### PV-SOC-02 – Complete matrix dependent on previous state [APPROVED]

| Battery SoC | Previously STOP / paused / new session | Previously PV_BALANCE | Previously FAST_DISCHARGE |
|---|---|---|---|
| `SoC <= L` | STOP | STOP | STOP |
| `L < SoC < M` | STOP | Continue PV_BALANCE | Switch to PV_BALANCE |
| `M <= SoC < U` | PV_BALANCE **only** if `PV > household consumption excluding the selected EV`; otherwise STOP | Continue PV_BALANCE | Continue FAST_DISCHARGE |
| `SoC >= U` | Start FAST_DISCHARGE | Switch to FAST_DISCHARGE | Continue FAST_DISCHARGE |

The PV surplus condition is **start evidence**, rather than a continuous requirement to STOP in BALANCE. It is **not** a prerequisite for FAST_DISCHARGE (which is also possible at night). If SoC falls below L, the SoC STOP takes precedence over economic waiting periods. Exact thresholds and equality cases follow `docs/pv-soc-policy.md` and the matrix above.

Evidence: PDF pp. 92–99, 133–145; `docs/pv-soc-policy.md`.

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

The local date change resets the daily latch. Daily state is persisted with versioning; a reload must not fabricate a first PV surplus.

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

Regular replanning occurs approximately every **300 s**; defined events may trigger immediate recalculation. Solcast updates may affect T between calculations and change it in **either direction** while `DYNAMIC` remains active. The external integration owns API requests and internal forecast updates; WBM makes **no** Solcast requests of its own (do not circumvent free API limits). The remaining forecast has its own slower freshness class, currently **900 s**; other required live measurements retain stricter freshness/validity rules. Forecast expiry must be diagnosable.

Evidence: PDF pp. 37–52, 101–119; `docs/pv-optimum-design.md`.

---

## 4. FAST_DISCHARGE – Using battery headroom

### PV-FAST-01 – Purpose and baseline [APPROVED]

From U upwards, the vehicle should promptly, **but in a controlled manner**, use the permitted battery discharge power available **in addition to current household load**. Do not impose a blanket 9 A limit. With actual headroom evidence, regulation should increase beyond the minimum point. Full utilization down to the last watt is **not** a quality promise; a justified conservative reserve was explicitly accepted (beta.10 practical test, PDF p. 476).

### PV-FAST-02 – The configured discharge limit is economic [APPROVED; important correction to V2]

The user-specific battery-discharge limit `D` (e.g. 3,500 or 4,500 W in tests) is **the ordinary economic budget for total actual battery discharge**, including other household load. It is **not a physical battery protection cutoff that WBM can guarantee**. Ordinary positive operating points above the minimum must respect this budget. **At exactly the smallest technically executable positive operating point**, it may temporarily be exceeded as a *narrow economic exception*; the shared 50 Wh decision then limits economic continuation solely through **actual measured grid import**. Actual current, phase, device/inverter safety limits must never be relabeled “economic” and bypassed.

Evidence: PDF pp. 568–571; `docs/pv-grid-import-policy-design.md`, decision hierarchy. **Note:** 50 Wh refers exclusively to **grid import**, rather than battery-discharge energy.

### PV-FAST-03 – Temporal feedback and asymmetry [APPROVED]

- Measured EV power is **not** the offered or acknowledged OCPP setpoint.
- A new increase requires enough **new** relevant source information and the defined evidence of physical response; repeated HA reports alone must not fabricate an upward ramp.
- Each measurement generation is used at most once as new evidence for an increase. No further ordinary increase while a current change remains unanswered.
- **Increase slowly; reduce quickly and sufficiently** for uncovered grid import, actual budget exceedance or consumption/PV steps. A safety reduction must not wait for an increase timer.
- Existing response/settling and grid-import tolerances of the functional FAST policy remain in force **except where explicitly superseded by the newer shared 50 Wh STOP policy**.
- No new universal fixed 20/30 s increase interval or “+1 A per measurement report” without evidence that it fits this architecture.

Evidence: PDF pp. 31–40, 209–246, 266–320, 400–426; `docs/beta9-implementation-report.md`, `custom_components/wallbox_manager/pv_regulators.py`.

### PV-FAST-04 – Measurement discipline and hard operating-point limits [TECHNICALLY SPECIFIED]

The beta.9 budget calculation uses fresh raw power measurements, a 100 W economic uncertainty reserve, source generations, a conservative EV baseline for asynchronous EV/site measurements and separate feedback following OCPP confirmation. A positive target power alone must not bypass discrete current/phase limits or the currently valid **ordinary** power ceiling. Minimum-exception evidence is restricted **exactly to the technical minimum**, rather than other operating points or unconfirmed physical state. The newer beta.13 policy replaced economic minimum STOP with 50 Wh; this must be explicitly accounted for when applying the original beta.9 formulas.

Evidence: `docs/beta9-implementation-report.md`, `docs/pv-grid-import-policy-design.md`.

---

## 5. PV_BALANCE – Surplus utilization and stability

### PV-BAL-01 – Battery configuration [APPROVED]

With a home battery, shared power regulation applies. `PV_BALANCE` follows available surplus, accounts for measured **total consumption including the selected wallbox**, and corrects for **its actual charging power**, without incorrectly subtracting other EVs:

`P_Balance = P_PV - P_Gesamtverbrauch_inkl_ausgewählter_EV + P_ausgewählte_EV_ist`.

The original formula identifiers are retained: balance power = PV power − total consumption including the selected EV + actual power of the selected EV.

Where the economic battery allocation calls for BALANCE without intentional battery discharge, the renewed economic assessment uses `D = 0`. For battery-free `PV_SURPLUS`, **PV-ARCH-02** applies instead of this additional PV/consumption mapping contract.

Evidence: PDF pp. 19–27, 579–583; `docs/pv-optimum-design.md`, `docs/pv-grid-import-policy-design.md`.

### PV-BAL-02 – Asymmetric, stable regulation [APPROVED]

Wallbox power should increase only in a controlled manner, but **decrease promptly and as needed** for real power deficits, clouds and sudden household loads. Do not counteract every apparent sensor step or the delayed measurement pattern created by the wallbox's own commands. Repeated measurement combinations and unmeasured OCPP responses must cause neither unjustified increases nor repeated frantic direction changes. Measurement freshness, source generation, actuator response and temporal asynchrony must be treated **separately**.

**Actual-implementation review point:** Sophisticated beta.9 source/response discipline is well developed in the documented `FastDischargeRegulator`; whether `PV_BALANCE` is equally protected across all currently relevant regulator paths requires assessment against beta.13 code and the pv6 log. This **does not authorize** copying FAST logic or changing STOP policy.

Evidence: PDF pp. 209–246, 401–426, 529–535, 540–545; hardware test `pv6` current at the original reconciliation.

### PV-BAL-03 – Freshness and smoothing [APPROVED EXISTING PARAMETERS; no new introduction]

Regulation interval (e.g. 5 s) and averaging window (current default 5 s) are **separate** settings. According to existing documentation, averaging applies only to **PV power and total consumer power**, rather than SoC, electrical safety, voltage or measured EV power. A 5 s cycle does not prove new physically synchronous Fronius/EV data. Additional smoothing must not improperly conceal a dangerous load step and must be assessed against existing measurement-generation/response policy.

Evidence: PDF pp. 31–40, 209–246, 529–535; `README.md` (Regulation parameters).

### PV-BAL-04 – Shared economic STOP policy only [APPROVED]

The old BALANCE/SURPLUS “90 seconds of continuously insufficient surplus” STOP is **replaced by the shared 50 Wh rule** wherever its measured grid-import evidence exists. The configuration value `pv_stop_delay` may remain stored for compatibility, but is **no longer the economic STOP timer**. Distinguish this from ordinary SoC STOPs, physical zero-current OFF and technical startup restrictions.

Evidence: PDF pp. 542–545, 566–600, 606–613; `docs/pv-grid-import-policy-design.md`.

### PV-BAL-05 – Review criteria for the next hardware iteration [OPEN]

Reliable quantitative limits for direction changes per unit time, correction time following a load step, minimum potential required for an increase or maximum acceptable PV-surplus losses have **not** been agreed. These will be defined only after measurement/causality analysis. The already approved qualitative asymmetry remains binding.

---

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
- Source and acceptance timestamps satisfying the existing measurement-quality contract.
- Reconciliation of applicable persisted economic history.

The **first counted interval must be fully supported by actual qualifying measurement evidence**. Do not count a preceding interval whose start lacked the required evidence. If transaction identity arrives after physical charging or measurements, begin only valid prospective accounting once the conditions hold; later knowledge of identity must not retrospectively attribute earlier charging. No general pre-identity buffering/replay mechanism is approved. Unknown gaps and downtime produce no integration or reset credit; do not fabricate intervals, transactions or accounting scopes.

Preserve the unified **50 Wh** policy exactly: count the **complete measured positive grid-import power only while `P_import > M/2`**; both source-time and acceptance-time gaps must be **at most 10 seconds**; reset requires **strictly more than 15 seconds** of supported `P_import <= M/2`. [PV-ECO-05](#pv-eco-05--measurement-gaps-and-causality-approved--technically-specified), [PV-ECO-07](#pv-eco-07--stop-provenance-and-transaction-binding-approved--technically-specified) and [PV-ECO-08](#pv-eco-08--automatic-economic-restart-approved) retain their measurement, persistence and restart guarantees. No persisted budget/STOP history is reset or transferred by observational activation; no new energy allowance, timer or change to the economic restart model is approved.

Keep these four facts distinct: **observed physical charging**, **an active economic accounting interval**, **an authorized manager positive command**, and **an approved restart following economic STOP**. Observational activity activation is **not an authorized positive restart**. It must not call a restart-confirmation mechanism that clears valid STOP provenance merely because charging was observed. A genuinely ended transaction followed by a proven new transaction follows the existing lifecycle rules for a new scope; a changed transaction ID alone during uncertain continuous activity must not reset exhaustion or reuse another transaction's energy. HA restart/reload requires reconciliation of persisted history and new valid evidence, without downtime integration or a fabricated fresh allowance.

**Q2 — Temporary unattributed charging [APPROVED, 15 October 2026].** A temporarily unattributed physical charging interval is accepted while transaction or causal accounting evidence is unavailable; confirmed general charging permission may remain ON, and no automatic permission-OFF fallback is authorized solely for that missing evidence. No mandatory pre-plug 0 A TxProfile is required. Diagnose the missing transaction, attribution or measurement prerequisites explicitly, and distinguish observed physical activity from economic monitoring that is not yet eligible. **Do not claim that the 50 Wh economic monitoring is active during an unattributed interval.** Begin valid prospective accounting when Q1 is satisfied and proceed with selected-profile regulation promptly when its actual execution prerequisites hold. Existing economic STOP/restart restrictions remain mandatory; do not issue automatic positive manager commands when the existing economic recovery contract inhibits them.

**Accepted enforcement limitation:** If the station is physically charging without a usable OCPP transaction, the manager cannot send a valid transaction-bound 0 A TxProfile. Retained-current charging may continue until valid control prerequisites become available or existing protections intervene. **This duration is not guaranteed to be bounded, and the manager cannot claim enforcement of the 50 Wh command-trigger threshold during that interval.** The accepted uncertainty must be visible in diagnostics and in later hardware acceptance. It is not a new general budget or permission to bypass electrical safety. No new timeout, charging allowance, alternative control path, CP command, station-side intervention or emergency permission-OFF policy is approved. Independent physical hardware safety mechanisms and normal authority constraints remain in force.

#### 6.4.5 Connected-vehicle pauses and CP ownership

For a legitimate economic, profile or protective interruption, request the applicable verified 0 A operating point through existing transaction-bound OCPP control. General `ChargingEnabled` remains ON for an ordinary charging pause. `wallbox-stationary` performs physical CP interruption and remains responsible for physical vehicle-connection detection. Do not implement CP control in the Home Assistant integration or introduce a second CP state machine.

The existing economic STOP, exact minimum-current exception and restart restrictions remain valid. A confirmed positive-to-zero OCPP transition can invoke the station's charging-reenable lockout; do not bypass it. Explicit user OFF is a separate permission action and retains its priority. If a required transaction-bound pause cannot be sent, diagnose that limitation and retain existing protective behavior; do not fabricate a transaction or a successful OFF.

#### 6.4.6 Existing station-side CP probing [RETAINED]

During a CP-interrupted session with a connected vehicle, the accepted station-side behavior remains:

```
CP probe interval: 3600 seconds
CP probe duration: 2 seconds
```

The station periodically releases CP to determine whether the vehicle remains connected. Once physical disconnection is confirmed, CP returns to normal continuously active operation so a new connection can be detected. Existing station-side connection debouncing and evidence rules remain authoritative. CP interruption alone does not establish immediate vehicle disconnection. These intervals are unchanged; do not assume they are INI-configurable without proof from the actual deployed station implementation. Recording this accepted behavior does not claim a new deployment inspection or hardware test.

#### 6.4.7 Physical phase switching [RETAINED]

The existing atomic, guarded 1p↔3p sequence remains unchanged:

1. Set EVSE target current to 0 A.
2. Block CP.
3. Verify current reduction through the meter.
4. Switch the physical phase relay.
5. Validate/restore the intended current.
6. Restore CP when safe.

Retain locks, electrical confirmation, failure handling and safety fences. Ordinary pause CP handling and phase-switch CP handling must not interfere. The station retains physical ownership of CP and phase relays in any future implementation.

#### 6.4.8 State reconstruction after restart or reconnection

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

**Unverified on hardware — deployment and acceptance:** This source inspection does not establish the installed revision of the physical wallbox, receipt of periodic reports from it, or successful physical-state reconstruction. Those remain subject to real OCPP/hardware verification. Until valid evidence arrives, retain unknown/awaiting-observation or last-known-but-unverified presentation; do not claim live Available/Idle recovery. The [earlier physical-status investigation](../physical-status-resync.md) remains historical evidence for its explicitly inspected older revision; its proposed periodic reporting is now implemented in the later source above, while its request-driven alternative remains unsupported. No new station-side protocol requirement or accepted charging behavior is introduced by this clarification.

#### 6.4.9 Dashboard presentation and physical evidence

Represent charging permission, vehicle connection, actual charging activity, CP observability, OCPP connectivity, and physical-observation freshness/provenance separately. Do not grey connection/charging status merely because no vehicle or transaction exists. Grey presentation reflects whether the physical observation remains valid under the supported CP/OCPP evidence model; it must not be defined solely by `ChargingEnabled`, transaction presence or an arbitrary elapsed time.

| Case | Physical and communication evidence | Required presentation |
|---|---|---|
| A – Vehicle absent, live observation | Permission ON; physically confirmed absent; Available; Idle; CP active; OCPP connected | Normal connection status **Available / Frei**, charging state **Idle / Bereit**; no grey styling |
| B – Connected and charging | Current confirmed physical connection/charging observations | Display those current confirmed states normally |
| C – Connected, paused, CP interrupted | Last-known connected/charging observation; physical observation no longer directly available under CP block | Retain last-known physical states, identify the observability limitation; grey styling is appropriate; never falsely show unplugged |
| D – OCPP unavailable | Last-known or unknown physical state; communication/freshness limitation | Identify the limitation; grey styling is appropriate |
| E – Disconnected after probe | Station physically confirms unplug, CP resumes ordinary observation, valid current Available/Idle evidence | Restore normal **Available / Frei**, **Idle / Bereit** without another user action |
| F – Restart before physical confirmation | Current physical state not yet established | Explicit unknown, pending or last-known-but-unverified state; no fabricated live Available/Idle |

**Q3 — Explicit permission OFF and physical status [APPROVED, 15 October 2026].** Display physical connection and charging state independently of general charging permission whenever genuinely current, independent physical evidence supports that presentation. With explicit permission OFF, operational OCPP, independently freshly confirmed physical vehicle absence and independently supported CP observability, the dashboard may display **Available / Frei** and **Idle / Bereit** normally while charging permission remains visibly **OFF**. Do not grey physical status merely because permission is OFF when that independent evidence is valid.

If those conditions cannot be established, display **unknown / awaiting observation** or a clearly identified **last-known, currently unverified** state with its freshness limitation. Never infer absence or CP observability from `ChargingEnabled=false`, missing transaction identity, missing meter values or an old Available event. Cases A–F above remain unchanged.

**Remaining OFF-state evidence limitation:** The reviewed station-side periodic physical reader in section 6.4.8 has disabled-permission gates that may prevent fresh observations while permission is OFF. Q3 does not authorize removing those gates or implementing additional station-side readback. Any interface enhancement remains deferred until a concrete requirement and separate implementation approval justify it; the installed revision and actual hardware reporting behavior remain unverified. The [subsequent decision record](decision-history.md#15-october-2026--subsequent-q1-q2-and-q3-decisions-approved) distinguishes this evidence limitation from the now-approved presentation policy.

Do not simply remove opacity styling to hide stale data. Correct any source-side status reporting defect at its proper ownership boundary. Available/Idle is a live standby state only when supported by the physical evidence, rather than inferred from an empty transaction list.

#### 6.4.10 Acceptance matrix [APPROVED decision; verification pending]

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

## 7. Unified economic 50 Wh grid-import policy

**This section replaces historically different economic STOP rules.** It follows the five explicit architecture approvals (PDF pp. 570–575), the subsequently specified profile/restart rule (pp. 575–600), and `docs/pv-grid-import-policy-design.md` from beta.13.

### PV-ECO-01 – Scope and priority [APPROVED]

Unified economic STOP for **PV_MAXIMUM, PV_OPTIMUM, PV_SURPLUS with battery and PV_SURPLUS without battery**, provided the required **valid measured grid-import evidence** exists. It **replaces** the previous economic minimum-budget immediate STOP, separate 10 s minimum-import STOP and BALANCE/SURPLUS deficit timers. No second parallel economic STOP. SoC protection, technical protection limits, authority, permission, OCPP lifecycle, startup eligibility and existing technical retries are **not** replaced by 50 Wh.

### PV-ECO-02 – Dynamic comparison threshold [APPROVED]

`T_import = M / 2`, where `M` is the **currently actually executable, verified positive minimum charging point in watts**. At 230 V and 1p/6 A, approximately 690 W; at 3p/6 A, approximately 2,070 W. A changed actual phase capability or voltage changes the threshold from its causal observation time, **not retroactively**. The 50% value is not a fixed power setting.

### PV-ECO-03 – Exactly which energy counts [APPROVED]

When **actual measured positive grid-import power `P_import > T_import`**, the **entire** grid-import power over the supported duration counts towards the budget, rather than just the portion above T_import. For `P_import <= T_import`, do not integrate; already accumulated energy initially remains. Budget exhaustion at **50 Wh** triggers the economic OFF request. The 50 Wh value is a **command-trigger threshold**, rather than a guaranteed physical ceiling on subsequent grid import (measurement and OCPP latency).

### PV-ECO-04 – Reset / dynamic interval [APPROVED]

Only **strictly more than 15 seconds** of uninterrupted **supported** grid import `P_import <= T_import` resets the budget. Exactly 15 seconds is insufficient. Brief periods below the threshold pause accumulation without deleting the budget. A phase/voltage change changes the threshold prospectively and does not arbitrarily reset accumulated energy.

### PV-ECO-05 – Measurement gaps and causality [APPROVED + TECHNICALLY SPECIFIED]

Each counted interval requires valid distinct measurement generations and gaps of **at most 10 seconds each** in both source and acceptance time. Larger/unknown gaps: **neither integrate energy nor advance the 15 s reset interval**; retain existing supported energy, then establish a new measurement anchor. No duplicate integration of repeated reports, old samples or conflicting samples. The current implementation holds the last valid value constant between two valid source timestamps (step integration), using exact watt-seconds rather than error-prone rounding. Threshold changes, OFF/charging-activity changes and causal timestamps split intervals. No fictitious energy from continuous HA updates of the same physical source.

### PV-ECO-06 – Narrow technical-minimum exception [APPROVED]

If the ordinary economic budget permits no positive point, an **already permitted** charging process may continue at **exactly M**, even if this temporarily exceeds `D`; **no arbitrarily higher point** may invoke this exception. Startup-response reserve, OFF-response evidence, SoC/profile thresholds, actual current/phase limits and authority/OCPP fences remain mandatory. Recheck correct physical determination of M before sending. Economic continuation ends only through the 50 Wh rule or an **independently valid** other STOP reason.

**15 October boundary:** These conditions govern manager operating-point control and economic continuation. They are not a transaction-bound preparation prerequisite for general permission under PV-OCPP-04. Temporarily retained legal startup current is separately permitted there; it is not an expansion of the exact-M economic exception or authorization to bypass existing STOP/restart inhibition.

### PV-ECO-07 – STOP provenance and transaction binding [APPROVED + TECHNICALLY SPECIFIED]

Grid-import energy, activity/pending/in-flight/paused state, STOP cause and restart reference are **session- and authority-bound**. Persist them in Home Assistant Store where the same entry, wallbox identity, authority, connector, external OCPP transaction and fixed policy-context data can be proven. Neither reload nor profile change/reselection of a PV profile may blindly reset a consumed budget. During downtime, **do not** integrate energy or advance the reset interval. Uncertain recovery permits no automatic positive point. Explicit OFF, NETZ scope exit and proven genuine transaction end close the scope under implemented rules. Late storage operations or delayed ACKs must not undo a newer OFF.

Under PV-OCPP-04, general permission without a real transaction creates no accounting scope and clears no history. The approved Q1/Q2 decisions in [section 6.4.4](#644-transaction-bound-control-and-economic-accounting) require prospective real-transaction-scoped observational accounting, reconciled persisted history and explicit diagnosis of accepted unattributed flow; no retrospective pre-identity replay or reset is authorized. Observational activation cannot authorize restart or clear STOP provenance. Recovery inhibition of automatic positive points does not mean general permission must await transaction preparation.

### PV-ECO-08 – Automatic economic restart [APPROVED]

A decrease in grid import alone, a mere 15 s budget reset, a new measurement event or expiry of a waiting period causes **no** restart following economic STOP. Restart additionally requires **all** profile/SoC/authority/permission/transaction/electrical/startup conditions, valid fresh measurements and a **material improvement** in the economic situation compared with the reference saved at the first proven STOP. Battery support must not be rejected by assuming “zero available”; an additional mandatory PV-only sensor or invented restart timer is prohibited. First assess the ordinary preferred point and, where applicable, the exact technically reachable minimum.

The condition **technically specified** with beta.13 and requiring verification is:

```
C_now - max(W_candidate, M_now) > C_stop - M_stop + 100 W
```

With `E` = measured EV power, `I` = grid import, `X` = grid export, `G=I-X`, `B` = measured battery discharge, `PV` = generation, `L` = total consumer power including EV, `D` = configured economic battery allocation in FAST (in BALANCE `D=0`):

- Without battery: `C = E - G`.
- With battery: `C = min(E + D - B - G, E + D + PV - L) - 100 W`.

The comparison uses actual EV power and a physically executable minimum. A hypothetical later phase switch is not a cheaper current operating point. Before comparison, EV and site measurements must be unambiguously **after** confirmed OFF, including EV zero-response evidence (current threshold ≤100 W). Equality or a change of only 100 W is insufficient. This contract is a **model**, rather than a promise that the battery will respond immediately on the next start; subsequent 50 Wh monitoring therefore remains necessary.

Evidence for PV-ECO-01 through -08: PDF pp. 539–545, 566–583, 595–600, 605–613; `docs/pv-grid-import-policy-design.md`, sections *Decision hierarchy*, *Accounting convention*, *Model-based restart*, *Persistence and recovery*. The targeted later permission/accounting clarification is PV-OCPP-04.

---

## 8. Technical validation, diagnostics and quality contract

### PV-QA-01 – Do not confuse diagnostics with effectiveness [APPROVED]

Logs distinguish planned, sent, accepted, confirmed and **physically measured effective** operating points. OCPP `Accepted` does not establish an appropriate grid/battery balance. Diagnostics include source generation, time basis, measurement age, economic `M`, `T_import`, energy in Wh, reset progress, previous STOP reference, state transition, request/applied point and reason. Diagnostic levels 0–3 remain distinct; level 3 must not trigger a second sensor query or cause unnecessary load.

For PV-OCPP-04, also distinguish permission intent/ACK/readback, transaction identity, physical connection/charging state, CP observability, OCPP connectivity, observation source/time/freshness and missing reconstruction prerequisites. An accepted permission command is neither profile confirmation nor physical vehicle evidence.

Evidence: PDF pp. 99–119, 235–252, 529–535; `docs/pv-surplus-profile.md`, `docs/pv-grid-import-policy-design.md`; PV-OCPP-04 for the new diagnostic distinctions.

### PV-QA-02 – Mandatory regressions [APPROVED INVARIANTS + PROPOSED TEST CASES]

1. Shared SoC matrix with exact L/M/U boundaries, nighttime FAST, START versus continuation, identical PV_MAXIMUM/PV_SURPLUS results for the same T.
2. DYNAMIC/FINISHED latch, local midnight/DST, forecast steps in either direction, 300 s replanning, stale forecast without unnecessary Solcast requests.
3. FAST: use a new sensor set only once; EV response before further increase; enough economic headroom; no indefinite response lock; fast actual reduction.
4. BALANCE: constant sunlight/load, deliberate PV/household steps, asynchronous reports, configurable smoothing, no self-induced 5 s 6 A/14 A oscillation.
5. 1p↔3p phase switching with lockout; retain 3p minimum during confirmed 3p restriction; no immediate reversal or fabricated lockout-expiry time.
6. Dynamic technically **reachable** minimum and 50% threshold, including changed voltages/phase evidence; no minimum exception above M.
7. 50 Wh energy: entire measured import power only for P>T; >15 s reset; 10 s source **and** acceptance gaps; duplicate/gap/stale/equality cases and OFF activity.
8. Economic STOP priority: no old 10 s, 90 s, H-minimum or other independent economic OFF path; genuine SoC/safety STOPs remain.
9. Persistence: HA restart/reload, active scope, genuine transaction end, profile change, authority/connection change, late ACK/checkpoints and failure cases.
10. Restart: strict `>` model comparison, 100 W margin, phase minimum and increased D, zero-response/source causality, unchanged situation blocked, real improvement allowed.
11. Battery-free SURPLUS using **only** grid import/export + EV mapping; incomplete configuration remains blocked.
12. No automatic Local→Remote takeover, no unauthorized commands, authority takes status priority.
13. PV-OCPP-04 acceptance matrix: permission without transaction/preparation; retained legal current; normal live vehicle-absent standby; CP-blocked/probe/disconnection evidence; all restart/reconnect cases; explicit OFF, transaction-scoped persistence and unresolved interface limitations. Keep command request, ACK, confirmed state, physical observation and dashboard presentation separate in verification.
14. PV-ARCH-05/06/08: replay repeated/equal and rapidly changing OCPP and HA measurements with WBM observing only or without authority; no background PV regulation, redundant OCPP targets or expensive all-entity reactions. Compare HA Core CPU WBM enabled/disabled and record profiler evidence, not an invented numeric threshold.
15. PV-ARCH-06: while PV control is active, apply multiple input updates and an unplugging event within one interval; verify exactly one per-tick PV decision, latest consistent snapshot, correct configurable smoothing, deduplicated commands, and preserved FAST/BALANCE asymmetry, SoC/economic decisions and phase-switch lockout.
16. PV-ARCH-07: inject authority revocation, OCPP disconnect, critical fault and late ACKs between ticks; verify prompt executor invalidation, per-dispatch live safety fences and independent physical feedback. Verify reconnect/readback without fabricated measurement generations, fictitious 50 Wh accounting or accidental restart.

### PV-QA-03 – Hardware acceptance criteria [OPEN]

The **intent** is binding, rather than an unsupported fixed number: fast economically useful discharge in FAST; a stable control loop and appropriate disturbance response in BALANCE; no unnecessary zero-current pauses with 600 s charging-reenable lockout; traceable grid-import energy rather than instantaneous peaks. Before the next algorithmic change, define realistic target metrics for command changes, maximum deficit-correction time, energy utilization and restart reliability, preferably from controlled load-step/cloud scenarios.

---

## 9. Observations from beta.13 – explicitly no silent target-behavior changes

- **OBS-13-01:** In test `pv6`, FAST remained at 1p/9 A for a long period. A **candidate deviation** from PV-FAST-01, assessable only after examining `D`, actual `B`, EV/site evidence, confirmed response, regulator hard budget and startup/increase fences. A justified conservative reserve had previously been accepted.
- **OBS-13-02:** `PV_BALANCE` repeatedly oscillated between low and higher ampere values during 15:24–15:34 and 15:58–16:08. Relevant target contract: PV-BAL-02 and PV-QA-02. The specific subpath causing repeated direction changes is not yet proven. Causal analysis first, then a minimal fix.
- **OBS-13-03:** Dynamic T increases, `FINISHED` and SoC-related charging pauses are **intended strategy**, as clarified by the user. Pursue only concrete PV-PLAN/PV-SOC violations.
- **OBS-13-04:** Fronius, EV and grid values are asynchronous physically and through HA; apparent negative/positive power peaks are not direct proof of energy consumption. Assess the WBM decision against **valid data available at that time**; assess grid energy from separately provable measurement.
- **OBS-13-05:** The beta.13 source description reports 2,420 Python tests/145 frontend tests without hardware approval. The later `pv6` test is a **new hardware finding**, rather than retrospective confirmation of every automated scenario.

---

## 10. Explicitly superseded rules – do not restore to the target matrix

| Historical approach | Later binding replacement | Source |
|---|---|---|
| Day starts at PV >=100 W (sometimes 5 min) or sunrise | First `PV > household consumption excluding the selected EV` | PDF pp. 1, 98–114 |
| Sunset or sunset minus 1 h as mathematical planning horizon | Remaining **local calendar day until midnight**, with `FINISHED` once remaining PV <= remaining household demand | PDF pp. 98–119 |
| Nighttime: never FAST without PV | FAST from SoC >= U, also at night without PV | PDF pp. 92–99, 133–145 |
| STOP already at SoC < T | SoC state matrix with `L = T-H/2`; previous state matters | PDF pp. 133–145 |
| Separate SoC logic for Surplus, Optimum, Maximum | Shared SoC policy and regulators; profile supplies T | PDF pp. 140–148, 579–583 |
| Unlimited FAST minimum charging regardless of economic budget | Only **exact technically executable M** as exception, unified 50 Wh STOP decision | PDF pp. 568–575, 599–600 |
| SoC STOP solely due to phase lockout below T / separate 10 s grid-import interval | Shared SoC matrix and shared dynamic M/2 / 50 Wh policy | PDF pp. 477–480, 539–545, 599–600 |
| `hard_budget_pause`/H-minimum as **economic** immediate STOP | Exactly M may continue charging; economic STOP only through 50 Wh | PDF pp. 566–600 |
| 90 s deficit STOP in BALANCE and SURPLUS | Unified measured 50 Wh decision; `pv_stop_delay` remains only a stored legacy option | PDF pp. 542–545, 599–600, 606–613 |
| Time-based 10 s STOP above M/2 | 50 Wh actual import energy; reset strictly >15 s; 10 s is only the measurement-gap limit | PDF pp. 539–545, 571–575 |
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

**Approval note:** V3 is a repository-versioned **consolidated DRAFT**. Inclusion in GitHub is **not overall subject-matter approval** or an instruction to implement/deploy. Only explicit overall subject-matter approval makes it a normative requirements baseline. The individual PV-OCPP-04 and PV-ARCH-05–08 decisions are APPROVED; that does not approve V3 as a whole.
