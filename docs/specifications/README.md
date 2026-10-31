**31 October 2026 — beta.21 publication scope:** Independent review approved
feature `a024c8ebfa6ae76a9e5d3ac993bdb75baafe8414`, including predecessor
`e61a16d4c4c3fe671a2ccb2086c48c0b76979219`. The operator separately authorizes
release preparation, complete validation, history-preserving develop/main merges,
an annotated tag and GitHub prerelease publication after exact-SHA CI gates.
The existing layout and **kW with two locale-aware decimals** are explicitly
accepted without further changes. No new requirement or control behavior is added.
Installation, HA restart/reload and hardware actions remain excluded. See
[beta.21 release notes](../v0.4/release-notes-beta.21.md) and
[release validation](../v0.4/release-validation-beta.21.md). Earlier feature-only
boundaries below retain their historical meaning.

**31 October 2026 — approved review follow-up:**
[PV Start acknowledgement](charge-release.md#approved-follow-up--pv-start-acknowledgement-31-october-2026)
adds WBM-ACTION-SAFE-09 / WBM-RELEASE-PREP-06.
[Status layout and source power](lovelace-home-battery-display.md#approved-follow-up--status-layout-and-source-power-31-october-2026)
adds WBM-HS-LAYOUT-01–04 / WBM-HS-POWER-01–06 and explicitly supersedes the
identified historical layout/caption clauses. Planned NETZ reserve and all control
algorithms remain unchanged. Feature commit/push only; no merge or deployment.

**31 October 2026 — approved safe-action/state/preview correction:**
[WBM-ACTION-SAFE-01–08 and WBM-RELEASE-PREP-01–05](charge-release.md#approved-correction--safe-explicit-actions-and-preparatory-release-31-october-2026)
and [WBM-HS-UI-SYNC-01–04 / WBM-HS-UI-NETZ-01–06](lovelace-home-battery-display.md#approved-synchronization-and-netz-preview-amendment-31-october-2026)
supersede only the identified historical transaction-absence and observed-only
NETZ display clauses. Implementation/tests/feature push are authorized; review,
merge, version, release, deployment and hardware operations remain separate.
WBM-ARCH-01–12 and control algorithms remain unchanged.

**27 October 2026 — beta.20 publication scope:** The separately authorized
release task packages approved feature `f4df7ac34a7f87acc170ee008362a43c067e2e65`
without implementation or policy changes. Normal history-preserving merges,
annotated tag and GitHub prerelease publication are authorized after successful
candidate/develop/main/tag validation. Installation, HA restart, configuration and
hardware actions remain excluded. See the [beta.20 release notes](../v0.4/release-notes-beta.20.md)
and [release validation](../v0.4/release-validation-beta.20.md). Earlier feature-only
approval boundaries below retain their historical meaning.

**27 October 2026 — approved observability amendment:** A dedicated
[PV Target SoC sensor](pv-target-soc-sensor.md) projects the canonical target with
change-only HA publication (WBM-PV-TARGET-01–06). The
[pause extension](pv-decision-diagnostics.md#approved-pause-context-extension--27-october-2026)
adds four captured fields to existing unchanged eligible pause samples and sparse
H configuration provenance (PV-DIAG-01–10; PV-SOC-01–03 unchanged). No regulation,
merge, release, deployment or hardware action follows from this approval.

**26 October 2026 — later cycle-sample and caption refinement:** The operator now
approves exactly one compact `pv_sample` for each otherwise unchanged eligible
active PV decision, as a bounded exception to beta.18's sparse-only policy.
Changed decisions retain their complete record; inactive/passive planning stays
change driven. No extra acquisition, control cycle or Recorder work is introduced.
The Home Battery heading sits directly above its percentage pair, without a
redundant visible caption; accessible SoC/reference text and the upper charging
caption remain. See [PV-DIAG](pv-decision-diagnostics.md) and
[WBM-HS-UI-02](lovelace-home-battery-display.md). That feature task authorized implementation only. The subsequent beta.19
publication task explicitly authorizes release preparation, validated normal
merges, annotated tag and GitHub prerelease publication; it authorizes no new
algorithm, deployment, hardware activity or live setting change. See the
[beta.19 release notes](../v0.4/release-notes-beta.19.md).

**26 October 2026 — publication status and approved diagnostic expansion:**
The approved shared PV Regulation V3 runtime was implemented and published in
[beta.17](../v0.4/release-notes-beta.17.md), including **seven new PV regulation
parameters**. The consolidated [V3 baseline](pv-regulation.md) remains DRAFT
as an overall requirements document; that status does not mean the approved
implemented features are absent. [Shared operating points and response](pv-operating-point-response.md)
and the common SoC, FAST/BALANCE, economic Wh and non-D restart policies are
implemented in beta.17. Beta.18 adds the explicitly approved
[five-power-value Level-2 context](pv-decision-diagnostics.md) to reconstruct the
available EV/grid/PV/battery/household situation at a relevant decision. Logging
remains sparse and decision-oriented. Physical hardware acceptance and the
[future FAST optimization](../pv-fast-power-diagnostics.md) remain outstanding;
no new FAST algorithm or live setting change is approved. Historic entries in
[decision-history](decision-history.md) record provenance, not parallel rules.

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
| [Lovelace home battery and simplified PV controls](lovelace-home-battery-display.md) | Profile-dependent home battery status, removal of five card-only editors, compatibility and tests | **Base published by beta.17; caption refinement approved after beta.18** |
| [NETZ / Grid fixed-point profile](grid-profile.md) | Implemented event-driven NETZ control, phase range/limits, recognized lockout retry, battery reserve | **APPROVED; published before beta.17; unchanged in beta.18** |
| [Automatic vehicle wake-up](automatic-vehicle-wakeup.md) | Historical remote-setting scope; superseded by local Stationary INI | **Superseded configuration ownership** |
| [Legacy manual compatibility](manual-vehicle-wakeup.md) | Station DataTransfer only; no manual HA button | Superseded UI scope |
| [Passive v0.4](passive-v0.4.md) | Current passive milestone | **APPROVED, subject to WBM-ARCH-01–12** |
| [Shared PV operating points and EV settling](pv-operating-point-response.md) | Configurable cycle timing, phase-first navigation, EV uptake, 100 W default grid deadband, Wh budget and 500 W default FAST restart house load | **Approved decisions implemented in beta.17; physical acceptance outstanding** |
| [Dedicated PV target SoC sensor](pv-target-soc-sensor.md) | Canonical target, identity, availability and Recorder efficiency | **APPROVED 27 October 2026** |
| [Level-2 PV decision diagnostics](pv-decision-diagnostics.md) | Full transitions plus compact five-power context for unchanged eligible active cycles | **Beta.18 five-value expansion published; later compact-cycle exception approved** |
| [PV regulation – Requirements baseline V3](pv-regulation.md) | Current consolidated target specification draft with stable requirement IDs, acceptance criteria, sources and open issues | **DRAFT overall; approved runtime published in beta.17** |
| [Decision history, October 2026](decision-history.md) | NON-NORMATIVE history: approved decisions, superseded interim solutions and explicitly rejected approaches | Reference / history |

## Mandatory working procedure for Codex and subsequent iterations

1. **Before every PV regulation task**, read [pv-regulation.md](pv-regulation.md) in full, particularly the current SoC matrix, shared economic STOP/restart policy and explicitly superseded rules.
2. [decision-history.md](decision-history.md) explains the origin and supersession of earlier decisions; **do not implement old or rejected rules again**.
3. V3 remains a **DRAFT** until explicit overall subject-matter approval. Identify contradictions between the draft, published beta.17 implementation, immutable historical tags, logs and older reports as open decisions; do not reinterpret them unilaterally. The individually **APPROVED** decisions PV-OCPP-04 (15 October 2026), PV-OCPP-05/06, PV-ARCH-05–09 (including the 17 October reconnect-cadence decision), PV-BAL-03 revision, PV-ECO-09, revised PV-ECO-05 and clarified PV-ARCH-03 (16 October 2026) do not approve V3 as a whole. PV-OCPP-05/06 supersede the prior CP-based display expiry requirement and require a full current-generation connection baseline. WBM initiates the connection/bootstrap synchronization after registration; station-originated measurement data may still require guaranteed station-side initial push where no standard readback is supported. The 10-second measurement-report gap rule has been explicitly superseded; NETZ is independent of Fronius/BYD HA measurements.
4. For each change, document the affected **requirement IDs**, observed actual behavior, claimed deviation from the target, related requirements, tests and hardware evidence. Changes to target behavior require a separate decision.
5. Apply WBM-ARCH-01–12 first. Retain OCPP lifecycle, authority, intent and economic/profile policy. Older manager-side physical safety, freshness-proof and mandatory pre-command readback requirements are explicitly superseded by its register; do not reintroduce them through historical documents. No competing event-triggered PV loop.
6. Codex tasks are written in English; implementation, review, merge, GitHub prerelease and hardware test are separate steps. Documentation does not automatically authorize changes to `main`, wallbox firmware or a Home Assistant installation.

**Source status:** These Markdown files do not import the PDFs or historical hardware logs into the repository. Page references refer to the externally retained 619-page discussion printout. Links to tagged repository documents are listed in the requirements baseline. The original discussion document's filename and technical identifiers are retained verbatim for traceability.

## Implementation and acceptance status

Approved NETZ and active OCPP foundation requirements are implemented; see
[NETZ implementation](../grid-fixed-profile-implementation.md),
[charge release](charge-release.md) and the beta.17 release notes. The
[passive v0.4](passive-v0.4.md) milestone and older future-tense design entries
remain historical scope records, not a claim that the present release is passive.
Beta.17 publishes the shared PV scheduler, release-independent target planning,
SoC matrix, FAST/BALANCE, economic STOP/restart and seven tuning parameters.
Beta.18 changes observability only; see the
[release notes](../v0.4/release-notes-beta.18.md) and
[validation report](../v0.4/release-validation-beta.18.md).

Automated replay and simulated OCPP acceptance are distinct from physical EV,
inverter/battery response, source timing, complete station bootstrap and live HA
performance acceptance. Those practical checks remain outstanding. Publication
approval does not authorize installation or hardware operation. No future FAST
settling change is approved by this documentation synchronization.
