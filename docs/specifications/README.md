**25 October 2026 economic restart-loop decision:** No new artificial WBM retry cooldown, sensor-change prerequisite or anti-oscillation latch is approved. Observe genuine repetitive attempts during hardware testing; existing Stationary lockouts and shared OCPP/SoC/import-Wh constraints remain. See [PV-ECO-08](pv-regulation.md).

**25 October 2026 configurable nighttime FAST economic restart:** [PV-TUNE-12](pv-operating-point-response.md) configures the **strict household-load threshold** for battery-backed FAST economic restart (default **500 W**, household load excluding selected EV). [PV-ECO-08](pv-regulation.md) admits economic restart on measured battery charging **>0 W**, or in FAST on valid household load **< configured threshold**, subject to Wh reset, SoC/authority/transaction and physical OFF conditions. No new mandatory household mapping for battery-free SURPLUS/NETZ. Exact post-STOP no-repeat edge test remains an implementation detail. Documentation only.

**25 October 2026 BALANCE STOP A finalized:** [PV-BAL-06](pv-regulation.md) now requires STOP on the first regulation tick with `SoC < T-H/2`, a genuinely applied **1p/6 A** EV operating point following the configured startup hold, and still **negative signed storage power**. No separate persistence counter or delay. Previous shared SoC restart rules remain. Documentation only.

**25 October 2026 BALANCE STOP direction — shared SoC matrix pending revision:** [PV-BAL-06](pv-regulation.md#pv-bal-06--target-deadband-discrete-ev-bias-and-stop-25-october-agreed-direction-transition-details-open) adds separate storage charge/discharge Fronius mappings, a discrete EV-step below/above ideal outside the SoC target deadband ±H/2, and BALANCE STOP for unattainable positive battery charging even at minimum technical EV point or for strictly `SoC < T-H`. The former `SoC <= T-H/2` STOP is superseded for the future regulator, but the **common STOP restart thresholds have now been reconciled**: SoC strictly above T+H/2 with surplus for BALANCE, or at/above T+H for FAST. The EV uptake percentage is configurable with default 50% ([PV-TUNE-11](pv-operating-point-response.md)). No implementation authorized.

**25 October 2026 PV tuning decision:** [PV-TUNE-01–10](pv-operating-point-response.md) establishes five configurable integration parameters: upward interval **3 regulation cycles**, startup hold **10 regulation cycles**, signed-grid deadband **±100 W**, ordinary reduction delay **0 regulation cycles**, and economic grid-import budget **50 Wh**. Remove obsolete PV start/stop delay entities and stored settings entirely in the future PV implementation. Economic STOP always overrides startup/reduction holds. No code change authorized.

**25 October 2026 common PV steps/settling amendment:** [PV-STEP-01–06 and PV-RESP-01–08](pv-operating-point-response.md) specify phase-first 230-V technical series, three-cycle upward cadence and a shared 50%-measured EV response / ten-cycle startup rule for both FAST and BALANCE. The grid deadband and economic STOP precedence are now resolved under PV-TUNE-01–10. No code authorized.

**25 October 2026 PV regulator redesign (documentation target; V3 still DRAFT):** [Shared PV V3: revised FAST/BALANCE and non-D economics](pv-regulation.md#4-fast_discharge--grid-point-led-utilization-25-october-direction) specifies the user-accepted common SoC architecture, grid-point-led FAST with bounded upward exploration, signed battery-flow-led BALANCE, and removal of the old configurable storage-discharge limit **from future controller policy**. The old HA Number remains for compatibility. The economic restart trigger is now specified in PV-ECO-08; signed battery-source mapping, deterministic STOP/retry provenance details and regulator acceptance tests still need implementation verification. [PV-DIAG-01–10](pv-decision-diagnostics.md) specifies sparse, decision-explanatory Level-2 PV logs. This documentation does **not** approve V3 as a whole or authorize implementation.

**Latest approved Lovelace display amendment (25 October 2026):** [WBM-HS-UI-01–15](lovelace-home-battery-display.md) adds a read-only home-battery SoC → profile target/current NETZ reserve comparison and removes five PV tuning inputs only from the card. Existing HA Number entities, permissions, persistence, backend profile algorithms and battery-free PV_SURPLUS remain unchanged. The PV V3 draft remains a draft.

**Latest approved operational amendment (24 October 2026):** [WBM-DISP-01–03 / MSG-01–07 / TIME-01–24](operational-display-and-timing.md) specifies one-decimal session-energy presentation, newest active warning/error only, wall-clock NETZ countdowns and time-edits acting on running schedules with a 15-second approval window. Documentation only; supersedes only conflicting historical timing/card-text requirements.

**Latest approved NETZ amendment:** [WBM-GRID-31–40](grid-profile.md#amendment--nominal-netz-steps-and-navigation-24-october-2026) fixes planning at nominal 230 V, defines rounded technical steps and backend phase-aware navigation. Older measured-voltage/UI-step clauses are superseded only as listed there.

**New approved target (24 October 2026):** [Manager Ladefreigabe and unified NETZ/PV card action](charge-release.md). This supersedes the conflicting automatic wake-up, hardware permission, SET and profile-action rules below. Implemented on coordinated feature branches; see [implementation and migration](../coordinated-control-redesign.md). Independent review and deployment remain separate.

# Wallbox Manager – Specifications

This directory contains WBM architecture, passive milestone requirements and the
PV regulation draft with its decision history.

**Overarching APPROVED architecture:** [WBM-ARCH-01–12: responsibility boundary
and no-expiry observations](architecture-responsibility.md) (18 October 2026).
The station alone owns physical/electrical safety; WBM owns optimization, policy
and presentation. This decision takes precedence over conflicting older clauses;
V3 remains DRAFT overall.

| Document | Purpose | Status |
| --- | --- | --- |
| [Architectural responsibility and observations](architecture-responsibility.md) | Overarching WBM-ARCH-01–12, with explicit supersession register | **APPROVED independently** |
| [Active OCPP control foundation](active-ocpp-control.md) | Explicit takeover and independent charging permission; successor milestone | **User-authorized implementation scope; hardware acceptance separate** |
| [Lovelace home battery and simplified PV controls](lovelace-home-battery-display.md) | Profile-dependent home battery status, removal of five card-only editors, compatibility and tests | **APPROVED UI requirements; implementation separate** |
| [NETZ / Grid fixed-point profile](grid-profile.md) | Next event-driven NETZ control, phase range/limits, recognized lockout retry, battery reserve | **APPROVED target; not yet implemented** |
| [Automatic vehicle wake-up](automatic-vehicle-wakeup.md) | Historical remote-setting scope; superseded by local Stationary INI | **Superseded configuration ownership** |
| [Legacy manual compatibility](manual-vehicle-wakeup.md) | Station DataTransfer only; no manual HA button | Superseded UI scope |
| [Passive v0.4](passive-v0.4.md) | Current passive milestone | **APPROVED, subject to WBM-ARCH-01–12** |
| [Shared PV operating points and EV settling](pv-operating-point-response.md) | Three-cycle upward ramp, phase-first technical point navigation and EV 50%/10-cycle stabilization for FAST and BALANCE | **Agreed design; economic boundary OPEN** |
| [Level-2 PV decision diagnostics](pv-decision-diagnostics.md) | Minimal measurement context for changed PV regulation decisions; suppression of unchanged ticks | **APPROVED direction; detailed implementation choices OPEN** |
| [PV regulation – Requirements baseline V3](pv-regulation.md) | Current consolidated target specification draft with stable requirement IDs, acceptance criteria, sources and open issues | **DRAFT – overall subject-matter approval pending** |
| [Decision history, October 2026](decision-history.md) | NON-NORMATIVE history: approved decisions, superseded interim solutions and explicitly rejected approaches | Reference / history |

## Mandatory working procedure for Codex and subsequent iterations

1. **Before every PV regulation task**, read [pv-regulation.md](pv-regulation.md) in full, particularly the current SoC matrix, shared economic STOP/restart policy and explicitly superseded rules.
2. [decision-history.md](decision-history.md) explains the origin and supersession of earlier decisions; **do not implement old or rejected rules again**.
3. V3 remains a **DRAFT** until explicit overall subject-matter approval. Identify contradictions between the draft, tagged beta.13 implementation, logs and older reports as open decisions; do not reinterpret them unilaterally. The individually **APPROVED** decisions PV-OCPP-04 (15 October 2026), PV-OCPP-05/06, PV-ARCH-05–09 (including the 17 October reconnect-cadence decision), PV-BAL-03 revision, PV-ECO-09, revised PV-ECO-05 and clarified PV-ARCH-03 (16 October 2026) do not approve V3 as a whole. PV-OCPP-05/06 supersede the prior CP-based display expiry requirement and require a full current-generation connection baseline. WBM initiates the connection/bootstrap synchronization after registration; station-originated measurement data may still require guaranteed station-side initial push where no standard readback is supported. The 10-second measurement-report gap rule has been explicitly superseded; NETZ is independent of Fronius/BYD HA measurements.
4. For each change, document the affected **requirement IDs**, observed actual behavior, claimed deviation from the target, related requirements, tests and hardware evidence. Changes to target behavior require a separate decision.
5. Apply WBM-ARCH-01–12 first. Retain OCPP lifecycle, authority, intent and economic/profile policy. Older manager-side physical safety, freshness-proof and mandatory pre-command readback requirements are explicitly superseded by its register; do not reintroduce them through historical documents. No competing event-triggered PV loop.
6. Codex tasks are written in English; implementation, review, merge, GitHub prerelease and hardware test are separate steps. Documentation does not automatically authorize changes to `main`, wallbox firmware or a Home Assistant installation.

**Source status:** These Markdown files do not import the PDFs or historical hardware logs into the repository. Page references refer to the externally retained 619-page discussion printout. Links to tagged repository documents are listed in the requirements baseline. The original discussion document's filename and technical identifiers are retained verbatim for traceability.

The next separately approved NETZ implementation target is [WBM-GRID-01–16](grid-profile.md); the existing active OCPP control foundation remains its prerequisite.

The active successor milestone is [the OCPP control foundation](active-ocpp-control.md).
It narrowly supersedes V04-03/06 for two explicit user operations and preserves
the approval and historical scope of [passive v0.4](passive-v0.4.md). Regulation requirements above are
preserved for subsequent work; they do not require implementing regulation in the
passive milestone. Historical implementation reports are not specifications.
