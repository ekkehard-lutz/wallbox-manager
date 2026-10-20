# Architectural responsibility and last-value-held observations [APPROVED]

**Decision:** Explicit user approval, 18 October 2026, “Architectural
Responsibility Boundary and No-Expiry Observations”. Independently approved,
overarching normative architecture for passive v0.4 and future WBM work.
This does **not** approve the broader [PV V3 draft](pv-regulation.md).

The station is solely responsible for physical and electrical safety. WBM may
make suboptimal requests from incomplete or old observations. The station must
reject or safely handle unacceptable requests. WBM cannot certify hardware safety.

| Stable ID | Approved requirement |
| --- | --- |
| WBM-ARCH-01 | The station owns all hardware protection, physical current/phase limits, electrical fault handling, charging interlocks, CP operation and safe phase switching, including meter checks, relay sequencing and safe restoration. WBM does not duplicate these protections. |
| WBM-ARCH-02 | WBM owns profiles, desired charging points, optimization, diagnostics and truthful user presentation. |
| WBM-ARCH-03 | Every WBM charging command is a proposal to the station, not an independently safety-certified instruction. Incomplete or old observations may yield suboptimal proposals; station enforcement remains mandatory. |
| WBM-ARCH-04 | Station rejection, delay or adjustment is normal. WBM handles outcomes, retains requested/applied distinctions and must not claim success, invent a response or replay obsolete intent. |
| WBM-ARCH-05 | All ordinary measurements and reported states are last-value-held within their observation generation, without TTL or age-based expiry. This includes voltage, current, power, energy, phases, permission, authority, connector/charging status and ordinary HA/forecast inputs. |
| WBM-ARCH-06 | Absence of a new report is not a value change and causes no availability transition. Five seconds, five minutes and five hours have identical observation-validity semantics. |
| WBM-ARCH-07 | Explicit new valid/invalid reports, connection lifecycle transitions and observation-generation boundaries are distinct events. Preserve source ordering, duplicate protection, connection/boot fencing, offline presentation and per-field initialization. For ordinary phase reports, one distinct invalid report may retain the last valid current-generation value; two consecutive distinct invalid reports make it unavailable. Phase 2A adds one narrow exception: a correctly scoped HardWiredNotification for Connector.PhaseRotation with trigger Delta and explicitly empty actual_value is authoritative unknown and makes it unavailable immediately. A newer valid report establishes its value immediately. Canonical consumers and display use the same interpretation, without hidden safety proof. |
| WBM-ARCH-08 | Failed, rejected, malformed or timed-out reads and missing confirmation do not establish a change of an observed property. Only Accepted supported valid Actual GetVariables values update observations; supported explicit unknown/state-change events remain meaningful. A missing initial value stays uninitialized. |
| WBM-ARCH-09 | WBM requires neither fresh physical proof nor mandatory pre-command readback for each charging request. No age-based safety gate or independent physical-safety state machine is permitted. Existing supported reads can acquire observations or clarify command outcomes for optimization and presentation. |
| WBM-ARCH-10 | Requested points, protocol acceptance, actual applied points and measured responses remain distinct for optimization, causal accounting and truthful display. Missing confirmation means an unconfirmed outcome, not a changed measurement or a hardware safety proof failure. |
| WBM-ARCH-11 | Economic constraints, user intent, authority ownership, profile policy, transaction scope and command-generation/late-response fencing remain WBM responsibilities. SoC thresholds and economic STOP/restart rules are policy, not guarantees of physical station safety. Protocol response deadlines, transport keepalive, command sequencing and genuine business/session timers remain valid. |
| WBM-ARCH-12 | Keep one minimal canonical store and direct affected-entity publication. No freshness manager, duplicate interlock, observation-expiry timer, extra verification loop, cache or periodic task. Existing five-second permission readback may detect external changes where reliable equivalent notifications are absent; it never refreshes a TTL. Identical reads may refresh internal acquisition metadata but publish no changing public timestamps or redundant HA state. |

## Precedence and explicit supersession

These requirements override every conflicting older provision in the passive
milestone, PV V3 and architecture/OCPP implementation descriptions. Historical
records and tagged source evidence remain unchanged. Narrow supersession follows;
all unrelated economic/profile/authority rules remain in effect.

| Prior provision | Binding replacement |
| --- | --- |
| V04-08 accurate public acquisition metadata; beta.3 permission-specific timestamps/TTL | WBM-ARCH-05/06/12: accurate internal metadata, no public per-read acquisition timestamps, inert compatibility deadlines (`None`). |
| V04-10 future manager physical evidence, phase/current safety gates | WBM-ARCH-01/09/11: station safety; WBM optimization and policy using held observations. |
| PV-ARCH-04–09 references to live safety checks, fresh physical evidence and proof of executable points | WBM-ARCH-01/03/09/10: ordinary held observations, no per-request safety revalidation. Retain authority, intent, connection, transaction fencing, cadence and no reconnect command queue. |
| PV-PLAN-04 900 s forecast freshness class, stricter live TTLs and expiry diagnostics; PV-QA-02 row 2 stale-forecast expectation | WBM-ARCH-05/06: no forecast or ordinary-input age expiration. Retain 300 s planning and local-day/forecast-domain semantics. |
| PV-FAST-03/04, PV-BAL-02/03 fresh measurements or safety evidence | WBM-ARCH-05/09/10: held values remain valid; source/response causality and controlled ramps remain optimization policy, not fresh safety proof or mandatory pre-command reads. |
| PV-PHASE-01/02 physically proven current minimum and fresh retry input | WBM-ARCH-01/04/05/10: estimate policy minimum from held observations and reported station restrictions, handle rejected proposals normally; never invent an applied phase from a requested phase or elapsed lockout. |
| PV-OCPP-01/02, PV-OCPP-04 sections 6.4.1–10 and acceptance rows 1–23: electrical/phase safety gates, fresh evidence, independent proof and mandatory readback | WBM-ARCH-01/07–11: station owns physical protection and the entire 6.4.7 switching sequence; no WBM interlock or fresh pre-command proof. Readback/reporting can confirm outcomes for display/optimization; absence of confirmation retains prior observations. Preserve authority/transaction/intent/economic policy and lifecycle. |
| PV-OCPP-05/06 references to necessary phase/current safety proof | WBM-ARCH-07/09/10: retain generation-scoped initialization and reported-state semantics, without age expiry or a new safety-readiness manager. |
| PV-ECO-01/02/05/06/08/09 physical safety/readback gates and fresh measurements | WBM-ARCH-05/09–11: retain economic eligibility, actual/requested distinction, prospective causality and restart inhibition; no fresh safety proof, per-command physical recheck or age limit. In particular “Recheck correct physical determination of M before sending” in PV-ECO-06 is superseded. |
| PV-QA-01/02 freshness/safety assertions, including row 16 per-dispatch safety fences | WBM-ARCH-01/05/09–12: ages are diagnostic only; verify station outcome handling, coherent observations and WBM policy/lifecycle fencing, not WBM hardware protection. |
| beta.3 PhaseRotation five-second proof; ChargingEnabled fifteen-second confirmation; separate display/proof interpretation | WBM-ARCH-05–09/12: no deadlines, one canonical value; retained two-report phase transition policy. Failed GetVariables is not an invalid property report. |

Older `docs/architecture.md`, `docs/metering-runtime-state.md`,
`docs/physical-status-resync.md`, phase-lockout and grid/PV design reports describe
previous implementations. Their phase-age/permission proof, sample deadlines,
CP freshness and manager safety requirements have no normative precedence over
this decision. Their causal economic or transaction accounting descriptions must
be reviewed separately when active regulation is implemented; no age-based
observation expiry or redundant station safety enforcement may be reintroduced.

## Scope and accepted limitations

Change-oriented reports are assumed to announce real changes reliably. Silence
can leave an observation old indefinitely; this is accepted. Connection health
does not prove physical currentness. New connection/boot generations still need
supported observations individually; full station bootstrap reporting remains an
unverified interface gap. No station modification or deployment is authorized.
Passive v0.4 still sends no physical control. Future active control must resolve
supported command/outcome interfaces, optimization response causality and economic
policy without rebuilding station protection in WBM.
