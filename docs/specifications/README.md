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
| [Automatic vehicle wake-up](automatic-vehicle-wakeup.md) | Station-owned detection/delay/CP; WBM enable switch only | **APPROVED for implementation** |
| [Legacy manual compatibility](manual-vehicle-wakeup.md) | Station DataTransfer only; no manual HA button | Superseded UI scope |
| [Passive v0.4](passive-v0.4.md) | Current passive milestone | **APPROVED, subject to WBM-ARCH-01–12** |
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

The active successor milestone is [the OCPP control foundation](active-ocpp-control.md).
It narrowly supersedes V04-03/06 for two explicit user operations and preserves
the approval and historical scope of [passive v0.4](passive-v0.4.md). Regulation requirements above are
preserved for subsequent work; they do not require implementing regulation in the
passive milestone. Historical implementation reports are not specifications.
