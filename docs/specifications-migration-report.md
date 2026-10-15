# English specifications migration and charging-permission decision report

Date: 15 October 2026. Repository: `ekkehard-lutz/wallbox-manager`.
Branch: `fix/pv-fast-confirmed-offer-economic-deficit`.
Baseline HEAD: `70f5ee3807034b9062ea16db483366275bb8a646`
(`Release v0.3.2-beta.14 for PV feedback stability hardware validation`).

Documentation changes are ready for external review. Consolidated V3 remains
**DRAFT**; the individual **PV-OCPP-04** decision is **APPROVED**.
All changes remain unstaged. No implementation, version change, commit, release,
push, merge, deployment or hardware test was performed.

## Inventory and exact migration mapping

The complete original hierarchy contained exactly three files and no nested
subdirectories. Every original file was read in full. All three have separate
English counterparts; nothing was combined or omitted. The original directory
has been removed, with no compatibility directory, duplicate German version or
redirect retained in the repository. Ordinary filesystem operations were used;
no index-changing move, reset, clean, stash or branch change was used.

| Original file | Final file | Original logical lines | Final logical lines |
|---|---|---:|---:|
| `docs/lastenheft/README.md` | `docs/specifications/README.md` | 19 | 19 |
| `docs/lastenheft/pv-regelung.md` | `docs/specifications/pv-regulation.md` | 408 | 581 |
| `docs/lastenheft/entscheidungsverlauf.md` | `docs/specifications/decision-history.md` | 72 | 99 |

The final specification hierarchy is exactly `README.md`, `pv-regulation.md`
and `decision-history.md` under `docs/specifications/`. This report is outside
that hierarchy. Logical-line counts include a final line without a newline in
the original sources; this explains differences from the initial `wc -l` counts.

## Translation and completeness method

The translation preserves the original hierarchy, formulas, requirement IDs,
status classifications, dated sequence, evidence limitations, rejected approaches,
PDF page references and immutable tagged source URLs. Requirement statuses use
APPROVED, TECHNICALLY SPECIFIED, OPEN, OBSERVED and HISTORICAL / SUPERSEDED.
The decision history remains explicitly INFORMATIVE / NON-NORMATIVE.
German formula identifiers, existing symbols/configuration keys, the original
PDF filename and requested bilingual dashboard labels are deliberately retained.
Their surrounding explanations are English. The source PDF itself was not
reopened; the original source-reconciliation claims remain explicitly historical.

Comparison used a pre-edit copy of every original specification, rather than
`git diff` alone. All replacement files are untracked and were read directly.
Each original heading is mapped below, each of the 40 existing requirement blocks
was compared for numeric occurrences and PDF page sequences, and all 19 original
decision rows were compared individually. English thousands/decimal separators
were normalized for numeric comparison (e.g. 3.500 → 3,500 and 4.033,94 → 4,033.94).
Dates were rendered in English while retaining their actual dates. Complete fenced
formula blocks and the original inline power/model formulas are preserved verbatim.
This structural comparison supplements a section-by-section semantic review;
it does not purport to prove translation equivalence from token counts alone.

| Original content group | Comparison result |
|---|---|
| Architecture and availability | All four requirements retained; shared regulator, identical-T equivalence, battery-free mapping, forecast requirement and ownership boundaries preserved |
| Shared SoC policy | All three requirements, exact inequalities, previous-state matrix, per-profile targets and nighttime FAST preserved |
| Daily planning | All four requirements, first strict surplus, midnight/DST calculation, FINISHED latch, capacity distinction and forecast freshness retained |
| FAST | All four requirements, economic D distinction, exact-minimum exception, conservative reserve, source generations and physical-response/asymmetric timing retained |
| BALANCE | All five requirements, selected-EV formula, separate smoothing, economic timer supersession and unresolved quantitative quality limits retained |
| Phase and existing OCPP | Exact executable floor, positive same-phase fallback, physical phase proof, serialized dispatch/live fences and authority priorities retained; only permission ordering explicitly superseded |
| Economics | All eight requirements, measured 50 Wh integration, supported reset/gap rules, persistence and strict modeled restart comparison retained |
| QA | All three requirements and original 12 regression items retained; diagnostics clarified and permission matrix added as item 13; hardware metrics remain OPEN |
| Observations and supersessions | All five OBS-13 records and all 15 original supersession-table rows retained; one targeted permission supersession added |
| Documentation conflicts | All five original review items retained; residual T/L conflict and symbol ambiguity explicitly flagged; new permission/interface review boundary added |
| Procedure and maintenance | All four PV-PROC IDs retained; separate HACS prerelease/hardware authorization, proportional tests and external timing dependency preserved |
| Sources | Original 619-page discussion reference and all seven tagged repository URLs retained byte-for-byte; later decision separately sourced |
| History | All 19 original decision rows, seven chains A–G, four unresolved issues, process rules and recommended-use section retained; 15 October decision added |

## Requirement ID inventory and targeted amendments

No existing ID was renumbered, reused or removed. Existing inventory (40):

- `PV-ARCH-01`, `PV-ARCH-02`, `PV-ARCH-03`, `PV-ARCH-04`.
- `PV-BAL-01`, `PV-BAL-02`, `PV-BAL-03`, `PV-BAL-04`, `PV-BAL-05`.
- `PV-ECO-01`, `PV-ECO-02`, `PV-ECO-03`, `PV-ECO-04`, `PV-ECO-05`, `PV-ECO-06`, `PV-ECO-07`, `PV-ECO-08`.
- `PV-FAST-01`, `PV-FAST-02`, `PV-FAST-03`, `PV-FAST-04`.
- `PV-OCPP-01`, `PV-OCPP-02`, `PV-OCPP-03`.
- `PV-PHASE-01`, `PV-PHASE-02`.
- `PV-PLAN-01`, `PV-PLAN-02`, `PV-PLAN-03`, `PV-PLAN-04`.
- `PV-PROC-01`, `PV-PROC-02`, `PV-PROC-03`, `PV-PROC-04`.
- `PV-QA-01`, `PV-QA-02`, `PV-QA-03`.
- `PV-SOC-01`, `PV-SOC-02`, `PV-SOC-03`.

The sole new requirement is **PV-OCPP-04 — Charging Permission, Connection
Readiness and Physical CP Observability [APPROVED]**, with 10 subsections and
23 acceptance rows. It separates permission from positive current, real
transaction identity from physical connection, and request/ACK/readback from
physical response. It covers retained legal startup current, station CP ownership,
unchanged probing/phase switching, accounting boundaries, restart/reconnection
reconstruction and all six dashboard cases A–F.

| Existing requirement amended | Precise clarification |
|---|---|
| PV-OCPP-01 | Transaction binding belongs to TxProfile control; general permission has its independent contract; ordinary pauses retain permission |
| PV-OCPP-02 | Recovery retains lifecycle and late-ACK fences; accepted permission does not confirm a profile/physical response |
| PV-OCPP-03 | No implicit Local→Remote takeover or recovery-based grant of ownership |
| PV-ECO-06 | Exact-M exception still governs economic continuation; it is not a general-permission preparation gate |
| PV-ECO-07 | Permission creates no artificial scope/reset; real-transaction reconciliation and unresolved retained-current attribution are explicit |
| PV-QA-01 | Diagnostics distinguish permission intent/ACK/readback, physical evidence, CP observability and communication/freshness |
| PV-QA-02 | Add the comprehensive permission/recovery acceptance matrix while retaining all original regression cases |

**Superseded assumption:** General user ON must await a transaction-bound economic
operating-point preparation. Only that dependency is superseded. Positive and
0 A TxProfile commands still require valid transaction identity, authority,
serialization, current/phase validation, lifecycle fences and hardware evidence.
The decision does not remove electrical safety, phase safety, economic STOP,
model-based restart, response gates or accounting persistence.

The 15 October 2026 decision-history entry separately identifies newly approved
behavior, explicitly retained guarantees, exact supersession and unresolved
interfaces. Its source is the user's later approval, not invented pages in the
older PDF. No full V3 approval is inferred.

## Numerical threshold comparison

All numeric occurrences in each existing requirement block are retained after
locale normalization; no missing occurrences were found. This includes numeric
examples, requirement-local citations and equality cases. Added dates,
clarification cross-references and new CP constants are separate additions.

| Quantity / rule | Original → final meaning |
|---|---|
| SoC H and valid target | Default 2 percentage points; `R + H <= T <= 100 - H`; unchanged |
| SoC thresholds | `L=T-H/2`, `M=T+H/2`, `U=T+H`; full previous-state/equality matrix unchanged |
| Day-start/end alternatives | Historical 100 W / 5 min and sunset minus 1 h remain superseded |
| Forecast/planning | Approximately 300 s replanning, 900 s forecast freshness, formula divisor 86400; unchanged |
| Example discharge budgets | 3,500 / 4,500 W; unchanged economic interpretation |
| Economic uncertainty | 100 W conservative reserve; unchanged |
| BALANCE timing examples | 5 s interval/default averaging, 6 A / 14 A oscillation example; unchanged |
| Executable minimum examples | 1p/6 A or 3p/6 A; no invented 1,380 W floor |
| Dynamic import threshold | `T_import=M/2`; 230 V examples 690 / 2,070 W; unchanged |
| Measured import budget | 50 Wh entire measured import only above threshold; not excess-only energy or physical cap |
| Reset | Strictly >15 s supported at/below threshold; exactly 15 s insufficient |
| Supported gaps | Source and acceptance gap each <=10 s; no downtime/unknown-gap extrapolation |
| Restart model | Exact original formula, strict `>`, >100 W improvement, EV zero response ≤100 W; unchanged |
| Station lockout | Historical/tested 600 s reenable restriction retained |
| Superseded economic timers | Separate 10 s and 90 s STOPs remain superseded; no revived parallel timer |
| Historical timing evidence | 3 s import reaction; 10 s → 2 s MeterValues trial; 90 s obsolete forecast freshness; preserved in history |
| Historical numerical example | 4,033.94 W versus 4,121.19 W; unchanged |
| Historical test totals/time windows | 2,420 Python / 145 frontend; 15:24–15:34 and 15:58–16:08; unchanged evidence limits |
| Retained CP probing, newly recorded | 3600 s interval / 2 s duration; unchanged accepted station behavior; no INI configurability asserted |

## Decision record and unresolved-issue comparison

The following maps all original decision rows in order. Original date/page cells
are quoted solely as audit evidence; the actual history is fully English.

| Original date / pages | Final English subject |
|---|---|
| 04.10., S. 1–19 | Introduction of `PV_OPTIMUM`, three SoC/consumption parameters, capacity |
| 04.–05.10., S. 19–40 | Reuse; FAST regulation |
| 05.10., S. 44–52 | Solcast forecast freshness |
| 05.10., S. 67–90 | Premature 0 A pauses and phase lockout |
| 06.10., S. 92–99 | Night operation / SoC |
| 06.10., S. 98–119 | Day start, end conditions, enable, diagnostics |
| 07.10., S. 132–148 | Shared SoC state machine |
| 08.–09.10., S. 209–246, 266–320, 401–449 | Oscillation, measurement generations, beta.9 regulators |
| 09.10., S. 320–410 | Measurement acquisition / Wallbox-Stationary |
| 09.10., S. 441–451 | Development/deployment |
| 09.10., S. 460–476 | beta.9/10 startup problem and headroom |
| 10.10., S. 477–480 | Phase lockout |
| 11.10., S. 485–528 | beta.11/12 findings |
| 11.10., S. 529–545 | pv5: load steps, economic STOP |
| 11.–12.10., S. 566–575 | Architecture audit, five approvals |
| 12.10., S. 575–583 | Profile availability and equivalence |
| 12.10., S. 595–600 | Restart, Codex approval |
| 12.10., S. 605–613 | Implementation/review |
| 12.10., S. 615–619 | beta.13 publication |

All policy/example numbers within those original rows were retained. The new
20th row is dated 15 October 2026. Chains A–G remain separately identifiable;
none was merged into a simplified policy summary.

Existing OPEN issues remain OPEN: BALANCE quantitative acceptance, FAST 9 A
versus justified headroom, physical source simultaneity and missing external
logs/attachments. Hardware acceptance metrics and the external Fronius timing
contract remain unresolved. Historical T-versus-L STOP-threshold wording remains
an explicitly flagged residual conflict; the later L matrix is preserved without
claiming every older source/code inconsistency has been resolved. Overloaded
M/L symbols are explained by context without renaming original formulas.

New acceptance obligations that current evidence/interfaces may not satisfy:

- Fresh physical presence/absence, charging state and CP-validity reconstruction
  after reload, restart, missed events or OCPP reconnect. A later inspection of
  `wallbox-stationary/main` at `2baf0daa8c446dad7570e5daa5f0f1cba486e915`
  confirms an existing periodic hardware refresh on a nominal 60-second schedule,
  with Available/Occupied reports even when unchanged and active-transaction
  Updated/MeterValuePeriodic Charging/EVConnected reports when valid. Busy control,
  startup, disabled permission, blocked CP, invalid reads and unconfirmed departure
  can prevent a valid refresh. The complete request-driven vehicle/charging/CP
  snapshot remains unsupported, and the physical wallbox's installed revision and
  actual reporting/recovery behavior remain unverified. The earlier physical-status
  investigation describes its older inspected revision and is unchanged. Heartbeat,
  metering and permission alone do not establish physical state. Acceptance rows
  2, 12–18 and 23 still require adequate physical evidence and real OCPP/hardware
  verification; until then retain unknown/last-known-unverified state.
- Full charging snapshots without a transaction, and explicit provenance for CP
  observability, require a separately reviewed interface capability where existing
  events cannot supply them. Available alone is insufficient without physical/CP
  validity; Occupied alone does not distinguish Charging from EVConnected.
- `GetCompositeSchedule` is not assumed available without an active transaction.
  Current/phase readback may remain unknown; never substitute a desired schedule
  for physical phase/current response evidence.
- Measured retained-current startup before real transaction identity or manager
  command evidence has an OPEN attribution question (acceptance rows 3, 4 and 22).
  The command/response accounting boundary is preserved; there is no fabricated
  retrospective integration, new accounting scope or automatic budget reset.

The remaining interface and deployment questions are distinct from the periodic
reporting already implemented in the inspected source. Complete no-transaction
charging snapshots and request-driven readback remain limited; TriggerMessage
remains unsupported. No new station protocol requirement or passing acceptance
scenario is asserted. The retained-current accounting attribution remains OPEN.
The precise verified gates and messages are recorded in
[PV-OCPP-04 section 6.4.8](specifications/pv-regulation.md#648-state-reconstruction-after-restart-or-reconnection).

## Repository references and historical exceptions

The active root README link now targets `docs/specifications/README.md`.
The migrated README links to `pv-regulation.md` and `decision-history.md`;
the maintenance path and all new cross-references use the English hierarchy.
Targeted clarification links were added to `docs/architecture.md`,
`docs/pv-optimum-design.md`, `docs/pv-surplus-profile.md` and
`docs/pv-grid-import-policy-design.md`, marking prior preparation ordering as
historical while preserving unrelated rules and actual implementation provenance.
No code/test reference adjustment was necessary.

A full hidden/ignored-file-inclusive search excluding `.git` found only the two
pre-existing old-path evidence locations below before this report was written.
Neither is an active Markdown navigation link. This report adds old spellings
solely for the required old/new audit mapping and quoted original headings.

| Deliberately preserved match | Reason |
|---|---|
| `docs/pv-economic-relaunch-verification.md:4` | Dated 14 October investigation records which original directory was read for that candidate; preserve the evidence statement, rather than recasting history |
| `docs/pv7-beta14-investigation.md:50` | Read-inventory evidence names the old README and PV filename; preserve the pre-existing untracked investigation associated with F1/F2 |
| `docs/pv7-fast-economic-corrections.md:13` | Historical “Lastenheft/decision history” wording in the untouched F1/F2 report; no active link, and user explicitly excluded modifying that candidate |
| `docs/beta12-implementation-report.md:73,99,133` | German runtime terminology and quoted historical UI strings, not specification navigation; the task does not translate unrelated reports |
| `custom_components/wallbox_manager/www/wallbox-manager-card.js:423` | German localized runtime UI strings, not documentation references; production/dashboard behavior is out of scope |
| This report | Old directory/filenames and original headings are intentional migration audit evidence, not compatibility links |

All seven original `v0.3.2-beta.13` GitHub source URLs are unchanged. Their tagged
provenance must not be redirected to paths absent from that release. Relative
local links and new fragment destinations were validated from their actual new
locations. No old active relative link remains. The seven historical release URLs were preserved and
compared byte-for-byte; their remote availability was not fetched or asserted.
For the final clarification, GitHub main was independently resolved to the station
commit cited in section 6.4.8 and its pinned source was inspected read-only.

## Original/new heading mapping

Every original heading has exactly one translated counterpart below. Quoted
German headings in this audit table are not untranslated specification content.
New PV-OCPP-04, its 10 subsections, the later source heading and new history entry
are additions outside this original-heading mapping.

### `README.md` → `README.md` (2 original headings)

| Original heading | English heading |
|---|---|
| Wallbox Manager – Lastenheft | Wallbox Manager – Specifications |
| Verbindliche Arbeitsweise für Codex und weitere Iterationen | Mandatory working procedure for Codex and subsequent iterations |

### `pv-regelung.md` → `pv-regulation.md` (54 original headings)

| Original heading | English heading |
|---|---|
| Wallbox Manager – PV-Regelung: konsolidierte Anforderungsbasis V3 | Wallbox Manager – PV Regulation: Consolidated Requirements Baseline V3 |
| 0. Gültigkeit, Rangfolge und Änderungsregeln | 0. Validity, precedence and change rules |
| 1. Fachliche Architektur und Profilverfügbarkeit | 1. Functional architecture and profile availability |
| PV-ARCH-01 – Gemeinsamer Regler mit Hausbatterie [BESCHLOSSEN] | PV-ARCH-01 – Shared regulator with a home battery [APPROVED] |
| PV-ARCH-02 – PV_SURPLUS ohne Hausbatterie [BESCHLOSSEN] | PV-ARCH-02 – PV_SURPLUS without a home battery [APPROVED] |
| PV-ARCH-03 – Profile mit Batterie und Forecast [BESCHLOSSEN] | PV-ARCH-03 – Profiles with battery and forecast [APPROVED] |
| PV-ARCH-04 – Zuständigkeitsgrenze [BESCHLOSSEN] | PV-ARCH-04 – Ownership boundary [APPROVED] |
| 2. Gemeinsame Batterie-SoC-Zustandsmaschine | 2. Shared battery SoC state machine |
| PV-SOC-01 – Ein Ziel-SoC T, ein Hystereseparameter H [BESCHLOSSEN] | PV-SOC-01 – One target SoC T, one hysteresis parameter H [APPROVED] |
| PV-SOC-02 – Vollständige, vom Vorzustand abhängige Matrix [BESCHLOSSEN] | PV-SOC-02 – Complete matrix dependent on previous state [APPROVED] |
| PV-SOC-03 – Zielwert je Profil [BESCHLOSSEN] | PV-SOC-03 – Target by profile [APPROVED] |
| 3. PV_OPTIMUM und Tagesprognose | 3. PV_OPTIMUM and the daily forecast |
| PV-PLAN-01 – Zweck [BESCHLOSSEN] | PV-PLAN-01 – Purpose [APPROVED] |
| PV-PLAN-02 – Drei Tagesphasen [BESCHLOSSEN; spätere Entscheidung ersetzt frühere] | PV-PLAN-02 – Three daily phases [APPROVED; later decision supersedes earlier behavior] |
| PV-PLAN-03 – Berechnung und Zeitmaß [BESCHLOSSEN] | PV-PLAN-03 – Calculation and time basis [APPROVED] |
| PV-PLAN-04 – Planung und Aktualität [BESCHLOSSEN] | PV-PLAN-04 – Planning and freshness [APPROVED] |
| 4. FAST_DISCHARGE – Batterie-Headroom nutzen | 4. FAST_DISCHARGE – Using battery headroom |
| PV-FAST-01 – Zweck und Ausgangsbasis [BESCHLOSSEN] | PV-FAST-01 – Purpose and baseline [APPROVED] |
| PV-FAST-02 – Konfigurierte Entladegrenze ist wirtschaftlich [BESCHLOSSEN; wichtige Korrektur zu V2] | PV-FAST-02 – The configured discharge limit is economic [APPROVED; important correction to V2] |
| PV-FAST-03 – Zeitliche Rückkopplung und Asymmetrie [BESCHLOSSEN] | PV-FAST-03 – Temporal feedback and asymmetry [APPROVED] |
| PV-FAST-04 – Messwertdisziplin und harte Betriebspunkte [TECHNISCH PRÄZISIERT] | PV-FAST-04 – Measurement discipline and hard operating-point limits [TECHNICALLY SPECIFIED] |
| 5. PV_BALANCE – Überschussnutzung und Stabilität | 5. PV_BALANCE – Surplus utilization and stability |
| PV-BAL-01 – Batterieausstattung [BESCHLOSSEN] | PV-BAL-01 – Battery configuration [APPROVED] |
| PV-BAL-02 – Asymmetrische, stabile Regelung [BESCHLOSSEN] | PV-BAL-02 – Asymmetric, stable regulation [APPROVED] |
| PV-BAL-03 – Frische und Glättung [BESCHLOSSENE EXISTIERENDE PARAMETER, keine Neueinführung] | PV-BAL-03 – Freshness and smoothing [APPROVED EXISTING PARAMETERS; no new introduction] |
| PV-BAL-04 – Nur gemeinsame Wirtschaftsstopp-Policy [BESCHLOSSEN] | PV-BAL-04 – Shared economic STOP policy only [APPROVED] |
| PV-BAL-05 – Prüfkriterien für die nächste Hardwareiteration [OFFEN] | PV-BAL-05 – Review criteria for the next hardware iteration [OPEN] |
| 6. Technischer Mindestpunkt, Phasensperren und OCPP | 6. Technical minimum, phase restrictions and OCPP |
| PV-PHASE-01 – Nachweisbarer technisch ausführbarer Mindestpunkt [BESCHLOSSEN] | PV-PHASE-01 – Provable technically executable minimum [APPROVED] |
| PV-PHASE-02 – Weiterladen bei Phasenumschaltsperre [BESCHLOSSEN] | PV-PHASE-02 – Continue charging under phase-switch lockout [APPROVED] |
| PV-OCPP-01 – Befehls- und Istzustand [BESCHLOSSEN] | PV-OCPP-01 – Commanded and actual state [APPROVED] |
| PV-OCPP-02 – Kommandosnapshot und Messwertänderung [BESCHLOSSEN] | PV-OCPP-02 – Command snapshot and measurement changes [APPROVED] |
| PV-OCPP-03 – Keine stillen Autoritätsübernahmen [BESCHLOSSEN] | PV-OCPP-03 – No silent authority takeover [APPROVED] |
| 7. Einheitliche wirtschaftliche 50-Wh-Netzbezugspolitik | 7. Unified economic 50 Wh grid-import policy |
| PV-ECO-01 – Geltungsbereich und Priorität [BESCHLOSSEN] | PV-ECO-01 – Scope and priority [APPROVED] |
| PV-ECO-02 – Dynamische Vergleichsschwelle [BESCHLOSSEN] | PV-ECO-02 – Dynamic comparison threshold [APPROVED] |
| PV-ECO-03 – Genau welche Energie zählt [BESCHLOSSEN] | PV-ECO-03 – Exactly which energy counts [APPROVED] |
| PV-ECO-04 – Rücksetzung / Dynamische Frist [BESCHLOSSEN] | PV-ECO-04 – Reset / dynamic interval [APPROVED] |
| PV-ECO-05 – Messlücken und Kausalität [BESCHLOSSEN + TECHNISCH PRÄZISIERT] | PV-ECO-05 – Measurement gaps and causality [APPROVED + TECHNICALLY SPECIFIED] |
| PV-ECO-06 – Eng begrenzte technische Mindestpunkt-Ausnahme [BESCHLOSSEN] | PV-ECO-06 – Narrow technical-minimum exception [APPROVED] |
| PV-ECO-07 – STOP-Provenienz und Transaktionsbindung [BESCHLOSSEN + TECHNISCH PRÄZISIERT] | PV-ECO-07 – STOP provenance and transaction binding [APPROVED + TECHNICALLY SPECIFIED] |
| PV-ECO-08 – Wirtschaftlicher automatischer Wiederanlauf [BESCHLOSSEN] | PV-ECO-08 – Automatic economic restart [APPROVED] |
| 8. Technische Validierung, Diagnose und Qualitätsvertrag | 8. Technical validation, diagnostics and quality contract |
| PV-QA-01 – Diagnose nicht mit Wirksamkeit verwechseln [BESCHLOSSEN] | PV-QA-01 – Do not confuse diagnostics with effectiveness [APPROVED] |
| PV-QA-02 – Pflichtregressionen [BESCHLOSSENE INVARIANTEN + VORGESCHLAGENE TESTFÄLLE] | PV-QA-02 – Mandatory regressions [APPROVED INVARIANTS + PROPOSED TEST CASES] |
| PV-QA-03 – Hardware-Abnahmekriterien [OFFEN] | PV-QA-03 – Hardware acceptance criteria [OPEN] |
| 9. Beobachtungen aus beta.13 – ausdrücklich **keine** stillschweigenden Soll-Änderungen | 9. Observations from beta.13 – explicitly no silent target-behavior changes |
| 10. Explizit abgelöste Regeln – **nicht** in die Soll-Matrix zurückführen | 10. Explicitly superseded rules – do not restore to the target matrix |
| 11. Widersprüche/Veraltung in bestehender Dokumentation – Review-Aufgaben, **keine** Codeaufträge | 11. Conflicts/obsolescence in existing documentation – review tasks, not code instructions |
| 12. Entwicklungs- und Testverfahren (ausdrücklich im Altchat festgelegt) | 12. Development and test procedure (explicitly agreed in the earlier discussion) |
| 13. Vorschlag für dauerhafte Pflege | 13. Proposed ongoing maintenance |
| Quellen | Sources |
| Originalverlauf | Original discussion |
| Normative bzw. technische Repository-Quellen (Tag `v0.3.2-beta.13`) | Normative or technical repository sources (tag `v0.3.2-beta.13`) |

### `entscheidungsverlauf.md` → `decision-history.md` (13 original headings)

| Original heading | English heading |
|---|---|
| Wallbox Manager – Entscheidungsverlauf und Ablöseregister (04.–12.10.2026) | Wallbox Manager – Decision History and Supersession Register (4–15 October 2026) |
| Entscheidungen in chronologischer Reihenfolge | Decisions in chronological order |
| Wichtigste Entscheidungsketten – wo ein zunächst plausibler Entwurf ausdrücklich ersetzt wurde | Main decision chains – where an initially plausible draft was explicitly replaced |
| A. PV-OPTIMUM-Tagesende | A. PV_OPTIMUM end of day |
| B. SoC-Stoppgrenze | B. SoC STOP threshold |
| C. OCPP-Nullstrom und Lockout | C. OCPP zero current and lockout |
| D. Batterie-Limit „hart“ oder wirtschaftlich? | D. Battery limit: hard or economic? |
| E. Wirtschaftsstopp | E. Economic STOP |
| F. Neustart nach 50 Wh | F. Restart after 50 Wh |
| G. Zeitliche Regelung und Pumpen | G. Temporal regulation and oscillation |
| Offene Punkte, bei denen die Chronik **keine** abschließende neue Norm liefert | Open issues for which the history provides no final new norm |
| Entwicklungsverfahren, das ebenfalls schon vereinbart war | Previously agreed development procedure |
| Empfohlene Verwendung | Recommended use |

## Working-tree isolation and validation results

A pre-edit inventory captured SHA-256 hashes of 236 tracked/untracked files,
original document copies, `git status --short` and exact index entries outside
the repository. Comparing the final working tree against that baseline found
only the intended documentation modifications/deletions and new documentation.
The Git index is byte-for-byte unchanged at the entry level and remains unstaged.
The existing F1/F2 production/test candidate, additional reports/manifests,
`pv2.log` and untracked regression test retain their baseline hashes.

| Check | Result |
|---|---|
| Complete old/new inventory | 3 → 3 files; no omissions, duplicate hierarchy or compatibility directory |
| Original heading mapping | 2 README + 54 PV + 13 history headings all mapped |
| Requirement IDs | All 40 original IDs retained; exactly one new ID, PV-OCPP-04 |
| Numeric occurrence comparison | All 40 original requirement blocks retain their numeric occurrences after locale normalization |
| Formula comparison | Original fenced formula blocks and inline power/model formulas retained verbatim |
| PDF references | All original requirement-local PDF page sequences preserved |
| Decision comparison | All 19 original rows retained in order; policy/example numbers preserved; new dated row added |
| Status/evidence boundaries | Overall DRAFT, history NON-NORMATIVE, original OPEN issues and hardware limits preserved |
| Obsolete-reference search | No active old-path links; all retained historical/audit matches explained above |
| Local link/fragment validation | 54 checked after the final clarification, no unresolved target or fragment |
| Whitespace | `git diff --check` passed; untracked replacement/report content also checked explicitly with no-index comparison |
| F1/F2/unrelated file hashes | Unchanged from initial working-tree contents |
| Index/branch/HEAD | Unchanged; no staged changes |
| Runtime/automated/hardware tests | Not run: documentation-only migration; no hardware completion claimed |

The final PV-OCPP-04 clarification changed only section 6.4.8 of the migrated
specification, the corresponding unresolved-boundary paragraph in the 15 October
decision entry, and the interface/deployment findings and administrative counts in
this report. A separate pre-clarification hash comparison confirms all other files,
including the historical physical-status investigation and F1/F2 production/tests,
remain untouched. Sections 6.4.9 and 6.4.10, all 23 acceptance rows, requirement
headings/IDs, existing constants and formulas are unchanged. The three new station
source anchors resolve in the pinned commit. All 54 relevant local links and
tracked/untracked whitespace checks pass. Git branch, HEAD, index and the complete
working-tree status below are unchanged from the start of this clarification;
no runtime/hardware tests or deployments were performed.

### Files changed by this task

- Modified tracked documentation: `README.md`, `docs/architecture.md`,
  `docs/pv-grid-import-policy-design.md`, `docs/pv-optimum-design.md`,
  `docs/pv-surplus-profile.md`.
- Deleted tracked original documents: the three old paths in the mapping above.
- New untracked documentation: `docs/specifications/README.md`,
  `docs/specifications/pv-regulation.md`, `docs/specifications/decision-history.md`,
  `docs/specifications-migration-report.md`.

### Final complete working-tree status

The production/test modifications and pre-existing untracked files below were
already present before this documentation task and remain untouched. Git shows
ordinary unstaged deletion/addition for the migration until a future authorized
review/commit determines rename presentation.

```text
 M README.md
 M custom_components/wallbox_manager/control/runtime.py
 M custom_components/wallbox_manager/pv_regulators.py
 M docs/architecture.md
 D docs/lastenheft/README.md
 D docs/lastenheft/entscheidungsverlauf.md
 D docs/lastenheft/pv-regelung.md
 M docs/pv-grid-import-policy-design.md
 M docs/pv-optimum-design.md
 M docs/pv-surplus-profile.md
 M tests/test_pv_beta12.py
?? docs/ocpp-meter-values-interval-investigation.md
?? docs/pv-decision-logic-audit.md
?? docs/pv7-beta14-investigation.md
?? docs/pv7-fast-economic-corrections.md
?? docs/specifications-migration-report.md
?? docs/specifications/
?? docs/validation/beta9-candidate-source-manifest.json
?? docs/validation/beta9-deployment-rollback.md
?? docs/validation/beta9-hardware-readiness.md
?? docs/validation/beta9-hardware-test-plan.md
?? pv2.log
?? tests/test_pv7_fast_corrections.py
```
