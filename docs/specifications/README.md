# Wallbox Manager – Specifications

This directory contains the consolidated PV charging regulation requirements and their decision history.

| Document | Purpose | Status |
| --- | --- | --- |
| [PV regulation – Requirements baseline V3](pv-regulation.md) | Current consolidated target specification draft with stable requirement IDs, acceptance criteria, sources and open issues | **DRAFT – overall subject-matter approval pending** |
| [Decision history, October 2026](decision-history.md) | NON-NORMATIVE history: approved decisions, superseded interim solutions and explicitly rejected approaches | Reference / history |

## Mandatory working procedure for Codex and subsequent iterations

1. **Before every PV regulation task**, read [pv-regulation.md](pv-regulation.md) in full, particularly the current SoC matrix, shared economic STOP/restart policy and explicitly superseded rules.
2. [decision-history.md](decision-history.md) explains the origin and supersession of earlier decisions; **do not implement old or rejected rules again**.
3. V3 remains a **DRAFT** until explicit overall subject-matter approval. Identify contradictions between the draft, tagged beta.13 implementation, logs and older reports as open decisions; do not reinterpret them unilaterally. The individually **APPROVED** decisions PV-OCPP-04 (15 October 2026), PV-OCPP-05/06, PV-ARCH-05–08, PV-BAL-03 revision, PV-ECO-09, revised PV-ECO-05 and clarified PV-ARCH-03 (16 October 2026) do not approve V3 as a whole. PV-OCPP-05/06 supersede the prior CP-based display expiry requirement and require a full current-generation connection baseline. The 10-second measurement-report gap rule has been explicitly superseded; NETZ is independent of Fronius/BYD HA measurements.
4. For each change, document the affected **requirement IDs**, observed actual behavior, claimed deviation from the target, related requirements, tests and hardware evidence. Changes to target behavior require a separate decision.
5. Existing OCPP, SoC, authority, safety, phase and economic STOP guarantees remain in force unless explicitly changed. The approved event-fed, clocked regulation must not introduce competing event-triggered PV loops or bypass prompt safety, physical confirmation or economic evidence rules.
6. Codex tasks are written in English; implementation, review, merge, GitHub prerelease and hardware test are separate steps. Documentation does not automatically authorize changes to `main`, wallbox firmware or a Home Assistant installation.

**Source status:** These Markdown files do not import the PDFs or historical hardware logs into the repository. Page references refer to the externally retained 619-page discussion printout. Links to tagged repository documents are listed in the requirements baseline. The original discussion document's filename and technical identifiers are retained verbatim for traceability.
