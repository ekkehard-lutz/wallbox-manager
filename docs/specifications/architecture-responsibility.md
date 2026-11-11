# Architectural responsibility and last-value-held observations [APPROVED]

**Decision:** Explicit user approval, 18 October 2026, “Architectural
Responsibility Boundary and No-Expiry Observations”. Independently approved,
overarching normative architecture for all v0.4 profiles and OCPP control.
This does **not** approve the broader [PV V3 draft](pv-regulation.md).

The station is solely responsible for physical and electrical safety. WBM may
make suboptimal requests from incomplete or old observations. The station must
reject or safely handle unacceptable requests. WBM cannot certify hardware safety.

| Stable ID | Approved requirement |
| --- | --- |
| WBM-ARCH-01 | The station owns all hardware protection, physical current/phase limits, electrical fault handling, charging interlocks, CP operation and safe phase switching, including meter checks, relay sequencing and safe restoration. WBM does not duplicate these protections. |
| WBM-ARCH-02 | WBM owns profiles, desired charging points, optimization, diagnostics and truthful user presentation. Stationary owns automatic vehicle wake-up configuration and execution under the 24 October decision and WBM-RELEASE-14. WBM owns independent manager release. |
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
| V04-08 accurate public acquisition metadata; beta.3 permission-specific timestamps/TTL | WBM-ARCH-05/06/12: accurate internal metadata, no public per-read acquisition timestamps or compatibility deadline attributes. NETZ business schedule deadlines remain. |
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

Older implementation reports and designs are historical evidence. The
[release specification](charge-release.md) governs the independent
`ChargingEnabled` and manager release controls. The former WBM wake-up enable
exception in WBM-ARCH-02 was superseded on 24 October. The [current baseline](v0.4-current-requirements.md)
identifies the detailed contracts and acceptance evidence for active NETZ and PV.

## Scope and accepted limitations

Silence can leave a value old indefinitely. Connection health does not
establish physical currentness. Each supported field needs its own observation
in a new connection/boot generation. Full station bootstrap reporting remains
an interface acceptance gap. Scope, intent, authority, transaction and economic
eligibility still govern the implemented active profiles.

## 27 October 2026 target publication application

[WBM-PV-TARGET-01–06](pv-target-soc-sensor.md) applies WBM-ARCH-05/06/12 to the
canonical planning target: one scoped HA sensor, no volatile telemetry attributes,
no unchanged-cycle writes, no extra target calculation, acquisition or timer.
OCPP generation broadcasts do not republish an unchanged HA-only target. This
observability extension does not alter the architecture responsibility boundary.

## Approved OCPP LAN trust boundary

**Operator decision, 7 November 2026.** Wallbox Manager runs on an
operator-controlled, trusted private LAN used by trusted devices and family
members. Remote administrative and charging-control access is permitted only
through an authenticated VPN that provides access to that LAN. Direct public
Internet exposure of the OCPP listener is outside the supported deployment model.

Within this boundary, the OCPP listener may use unencrypted WebSockets
(`ws://`) without client certificates or application-level authentication.
Binding to `0.0.0.0:9000` is permitted. The station ID is used only for routing
and provides no peer authentication. The operator accepts that a compromised
device with access to the trusted LAN could observe or disclose communication,
impersonate a station, replace or disrupt an existing connection, or submit
manipulated protocol observations or responses.

The operator must verify during commissioning that the configured listener
is reachable only from the intended trusted LAN or through the authenticated
VPN and is not directly published to the public Internet. This decision
defines the supported deployment model. The network check is operator-controlled.

This decision supplements WBM-ARCH-01–12. The approved deployment requires
no additional WBM TLS, certificate management, authentication layer, IP allowlist,
network watchdog or hardware safety mechanism. Stationary retains exclusive
physical and electrical safety responsibility.
