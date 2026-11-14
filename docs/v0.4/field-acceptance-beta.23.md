# Wallbox Manager v0.4.0-beta.23 — Field Acceptance and Findings

Software baseline: version `0.4.0-beta.23`, tag `v0.4.0-beta.23`, release
commit `443f4ca88efa2c28f80f0baf025d8a28823232db`.
[Published prerelease](https://github.com/ekkehard-lutz/wallbox-manager/releases/tag/v0.4.0-beta.23)

Use this register for operator-controlled field testing. The
[current requirements](../specifications/v0.4-current-requirements.md) and
[WBM-ARCH-01–12](../specifications/architecture-responsibility.md) govern the
acceptance criteria. Stationary owns physical safety and interlocks. Record
operator intent, OCPP acknowledgement, confirmed station offer and measured EV
response separately. The [release notes](release-notes-beta.23.md) describe the
software baseline and the [audit matrix](release-readiness-audit.md#13-outstanding-physical-acceptance-matrix)
defines the PA scenarios. [Release validation](release-validation-beta.23.md#local-validation)
records automated evidence, including 1,195 Python tests, 95 frontend
regressions and headless Chrome checks. Only applicable operator observations
count toward physical acceptance.

## Status definitions

| Status | Meaning |
| --- | --- |
| PASS | Operator-observed result meets the tested acceptance criteria. |
| PARTIAL | Only part of a larger acceptance scenario has been verified. |
| FAIL | Confirmed deviation from the expected behavior. |
| OPEN | Untested or unresolved. |
| BLOCKED | Another issue prevents the test from proceeding. |
| RESOLVED | Finding addressed with evidence. |
| ACCEPTED | Explicitly accepted limitation. |

Apply the status to the stated scope. A passing individual test contributes
evidence to its PA scenario. Close a PA scenario only after all applicable
criteria have been verified.

## Field observation log

### 12 November 2026

The entries below reflect the operator's observations. Session timestamps are
Home Assistant (HA) sensor values in UTC.

| ID and scenario | Operator action and observed evidence | Result and scope |
| --- | --- | --- |
| FA-01 · HACS installation | Installed beta.23 through HACS. HA displayed `0.4.0-beta.23`. Initial integration setup was blocked by the historical registry entry in FA-02. | **PASS** after FA-02 was resolved. Contributes to PA-01. |
| FA-02 · Historical entity-registry conflict | Setup reported `Registered entity does not match the configured binding`. The operator identified obsolete `select.aktive_wallbox`, with a legacy unique ID ending in `:active_wallbox`, and removed it through HA. Wallbox Manager then reloaded successfully. | **RESOLVED**. The registry cleanup was sufficient for this private prerelease installation. No migration or product-code change is required. Contributes to PA-01. |
| FA-03 · OCPP bootstrap and authority | Connected over OCPP 2.1 and completed BootNotification. Stationary initially showed Local authority. The operator used Take Control, and Stationary showed Remote authority. Hardware charging permission was reported enabled. Electrical readings arrived shortly after bootstrap. The temporary absence of readings matched the operator's previous experience. See the snapshot below. | **PASS** for initial bootstrap and explicit takeover. PA-02 and PA-03 remain partial. |
| FA-04 · Initial NETZ Start | Selected NETZ, requested 1.4 kW nominal with no start delay or duration limit, and started charging. Stationary offered 1 phase at 6 A. Measured EV power was approximately 1.36 kW. The UI showed charging active and Stop available. | **PASS** for this initial Start. Contributes to PA-04 and PA-15. |
| FA-05 · NETZ Apply and phase change | During the same session, increased requested power to 11.0 kW and used Apply Charging Parameters. Stationary operated at 3 phases, 16 A. Measured EV power was approximately 10.73 kW. The UI showed the new operating point. | **PASS** for this Apply and phase change. Lockout and timing tests remain **OPEN**. Contributes to PA-04, PA-06 and PA-15. |
| FA-06 · NETZ Stop and unplug | Used Stop Charging. Stationary returned to 3 phases, 0 A, and the UI returned to Start Charging. The operator then unplugged the vehicle. The connector returned to Available and EV power returned to 0 kW. | **PASS** for ordinary Stop and unplug. Offline, faulted and permission-disabled Stop remain **OPEN**. Contributes to PA-04, PA-07 and PA-15. |
| FA-07 · Home battery reserve | During NETZ charging the temporary discharge reserve was 26%. Battery discharge stopped at approximately 26% SoC. After charging ended, the original 25% minimum reserve was restored. | **PASS** for ordinary reserve takeover and restoration. Reload, failed writes, external edits and abnormal recovery remain physically untested. Contributes to PA-13. |
| F01 · Lovelace session duration | Lovelace showed `0:00` while session energy increased to approximately 3.4 kWh. HA reported start `2026-11-12T10:33:10+00:00`, end `2026-11-12T12:19:42+00:00`, duration state `1.77574393916667` and session energy `3.375 kWh`. The start/end interval is approximately 1 h 46 min 32 s. | **FAIL** for the duration display. The [finding](#f01--lovelace-session-duration) remains **OPEN**. Contributes to PA-07 and PA-15. |

FA-03 electrical snapshot: L1/L2/L3 voltage 226.1/227.4/226.9 V. L1 current
0.034 A and L2/L3 current 0 A. Active power 0 W and lifetime imported energy
19077.242 kWh.

### Additional NETZ acceptance observations (reported by 14 November 2026)

These operator reports followed the first field observations. Their individual
test timestamps were not captured, so the date above is a reporting cutoff,
not an asserted execution time.

| ID and scenario | Operator action and observed evidence | Result and scope |
| --- | --- | --- |
| FA-08 · NETZ Start before plugging in | With the vehicle absent and NETZ set to 11.0 kW, the operator explicitly pressed `Ladung starten`. Release remained ON without an EV or current flow. On a later plug-in, charging began automatically at 3 phases/16 A, approximately 10.72 kW. | **PASS** for preparatory user-authorized Start and subsequent plug-in within the still-active release. Contributes to PA-04/07. No delayed timer was set. |
| FA-09 · Replug after a pause | During an active unbounded NETZ release, the vehicle was unplugged, plugged in again after approximately 60 minutes and charged automatically. The operator subsequently unplugged again and explicitly ended the release; the card returned to `Ladung starten`. | **PASS** for replug and manual Stop in this scenario. Does not prove same external transaction ID, command fencing or reconnect readback. Contributes to PA-04/07. |
| FA-10 · NETZ timing without vehicle | Without an EV, the operator approved 2 minutes of Startverzögerung and 3 minutes Ladedauer. Delay counted down; after its expiry, the duration counted down and release automatically retired at the original deadline. During the run, the operator changed **only** requested power (11.0 to 6.9 kW) and used Apply; this did not shift the time schedule. Both consumed time fields cleared. | **PASS** for these no-vehicle timing and power-only Apply cases. PA-05 remains **PARTIAL** pending interruption/lockout cases. |
| FA-11 · Timed charging and live extension | An approved 2-minute delay/3-minute duration started without the vehicle; it was plugged in **during** the delay without restarting it. Charging began afterward at 3 phases/10 A (approximately 6.44 kW). During charging the operator changed displayed remaining duration from 00:01 to 00:04 via Apply. Total physical charging interval after delay was about 6 minutes; automatic Stop returned to 0 A, `Ladung starten` and cleared time fields. | **PASS** for late plug-in, active charging in the timing window and explicit duration extension. Actual elapsed observation supplied by operator, not a captured timestamp trace. |
| FA-12 · Timing editor grace and power edit | Operator checked timing draft rollback: with timing editor focus active, edits remain visible; after leaving both timing inputs the separate 15-second grace applies, including across a minute display boundary, and the live countdown returns after expiry. A **power-only** unapproved draft continues to show `Ladeparameter übernehmen` until explicitly handled. | **PASS** for observed draft/editor behavior. No product finding; permanent power-draft indication is current intended behavior. |

### 14 November 2026 — Stationary restart and authority tests

| ID and scenario | Operator action and observed evidence | Result and scope |
| --- | --- | --- |
| FA-13 · Remote → Local → Remote authority | With no EV plugged in, NETZ release was armed under Remote (card `Ladung beenden`). The operator selected local `PV Überschuss` in Stationary; the wallbox UI showed the local mode and 0 A, WBM displayed `Steuerung übernehmen` and `Ladung starten` (release retired). The operator then explicitly retook Remote; Stationary showed Remote/0 A, while WBM still showed `Ladung starten` rather than restarting the previous release. | **PASS** for intentional authority loss, retirement and explicit takeover without automatic charging. Contributes to PA-03 and PA-04. Hardware permission changes were not tested. |
| FA-14 · Stationary service restart during charging | Operator invoked `sudo systemctl restart wallbox` while the EV was actively charging with NETZ requested 6.9 kW/3 phases/10 A (about 6.4 kW observed). Charging continued physically, but WBM showed `— → 3-phasig 10A`, meaning requested point known and current-generation applied point not confirmed. Changing requested power **once** to 7.6 kW and pressing Apply restored the confirmed display; a **second** Apply returned the request to 6.9 kW. Reserve diagnostics showed confirmed 30%-then-7% changes during the continued charge. See F02. | **PARTIAL** physical continuation after Stationary restart; **FAIL/OPEN** autonomous point reconciliation and reserve retention under this condition. Contributes to PA-02/04/13 and F02. |

The operator **intentionally** lowered the WBM NETZ discharge reserve from 30%
to 10% **after** the initial restart screenshots. Later card display
`18.2% → 10.0%` reflects the changed NETZ target/preview; it is not evidence
that the mapped Fronius minimum reserve was set to 10%.

## Findings register

| ID | Date | Subsystem | Expected behavior | Observed behavior | Evidence | Severity | Status | Suspected cause | Next investigation | Proposed fix release |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [F01](#f01--lovelace-session-duration) | 12 November 2026 | Lovelace session display | Display elapsed duration in hours and minutes consistent with the bound HA session. | Card showed `0:00` for a session lasting approximately 1 h 46 min 32 s. | [Operator log](#12-november-2026) and [beta.23 source inspection](#f01--lovelace-session-duration). | P2 · UI presentation | **OPEN** | Likely mismatch between the HA state unit and the frontend's seconds assumption. The live unit is unconfirmed. | Inspect the live `unit_of_measurement` together with the numeric state and compare the card output with elapsed time. | beta.24 candidate |
| [F02](#f02--stationary-reconnect-operating-point-and-battery-reserve) | 14 November 2026 | OCPP reconnect / NETZ battery reserve | A continuing authorized NETZ charge reconciles its current-generation applied point and retains an appropriate owned temporary reserve until charge ends or another legitimate retirement event. | After Stationary restart physical charging continued (~6.4 kW); applied point became unknown (`— → 3-phasig 10A`) until the **first** manual Apply at 7.6 kW. WBM also confirmed repeated writes of the reserve from 30% back to original 7% while charging was reported. | [FA-14](#14-november-2026--stationary-restart-and-authority-tests), [diagnostic timeline](#f02--stationary-reconnect-operating-point-and-battery-reserve). | P2 · functional energy management (raise if impact warrants) | **OPEN** | F02a: missing/unconfirmed OCPP TxProfile readback after generation change with no automatic command replay. F02b: NETZ reserve eligibility depends on confirmed point and current-generation state, causing a possible premature restoration. Neither causal mechanism has yet been reproduced in an isolated test. | Reproduce with synthetic Stationary reconnect and persisted same-ID charging; trace confirmed point, transaction, current/phase evidence, reserve ownership/restore trigger. Verify actual Fronius reserve, not only card preview. Investigate both restore events separately. | beta.24 candidate |

### F01 — Lovelace session duration

To reproduce, observe the entry-bound Lovelace card during and after a NETZ
session long enough to show a nonzero number of minutes. Compare the duration
row with HA's session start and end. In HA Developer Tools, capture both the
state and `unit_of_measurement` of
`sensor.wallbox01_evse_1_1_session_dauer` along with the card display and
installed card version. The original field evidence contains the state value
only.

In beta.23 the [card](../../custom_components/wallbox_manager/www/wallbox-manager-card.js#L630)
divides the numeric state by 60 to derive minutes. The
[backend sensor](../../custom_components/wallbox_manager/sensor.py#L131)
declares seconds as its native duration unit. HA may expose a converted unit.
If the live attributes confirm the mismatch, normalize the displayed value
using the actual HA state unit before formatting hours and minutes. Keep OCPP
session accounting and the stored session schema outside the proposed fix.

### F02 — Stationary reconnect operating point and battery reserve

**F02a — Actual point after reconnect.** Stationary remained physically
charging at approximately 6.4 kW through `sudo systemctl restart wallbox`.
WBM retained its desired `3-phasig 10A` but did not show a confirmed actual
point (`— → 3-phasig 10A`). **One** successful `Ladeparameter übernehmen` at
7.6 kW was sufficient to restore the confirmed display. The subsequent
Apply back to 6.9 kW was solely the operator's desired rollback of power,
not required to restore confirmation.

Do not infer that an old-generation point should be reused without a new
confirmation. Investigate why supported OCPP readback/reconciliation did
not establish the actual current-generation point while physical charging
continued, and whether the first explicit Apply established valid new proof.

**F02b — Battery reserve restoration despite continuing physical charging.**
With the operator's NETZ reserve configured to 30% and the original Fronius
reserve 7%, the following WBM level-2 diagnostic events were captured
(HA local log timestamps, 14 November 2026):

| Time | OCPP generation / event | Confirmed reserve or state |
| --- | --- | --- |
| 09:46:59 | [2,1] physical phases | 3 |
| 09:47:21 | [2,1] battery_reserve confirmation_pending / write_requested / confirmed / active | 30% |
| 10:14:27 | [2,1] battery_reserve confirmation_pending / write_requested / confirmed / restored | 7% |
| 10:15:38 | [3,1] boot_notification | Re-registration observed |
| 10:16:04 | [3,1] battery_reserve confirmation_pending / write_requested / confirmed / active | 30% |
| 10:16:50 | [3,1] battery_reserve confirmation_pending / write_requested / confirmed / restored | 7% |
| 10:16:58 | [4,1] boot_notification | Another registration observed |

The log confirms WBM successfully **requested and received HA confirmation**
for the reserve writes; it does not independently prove Fronius kept the value
between events. The first 7% restore **preceded** the captured 10:15:38 boot,
so do not attribute every restore exclusively to the later reboot. Both
restores need a separate event/generation/state cause analysis.

Relevant current beta.23 paths: `grid.py` invalidates a previous OCPP
generation's point and requires supported current-generation readback.
`grid_battery.py` derives `charging` from multiple conditions including
`grid.confirmed`, current point/transaction generation, state
`charging`, and measured positive session power. When that predicate becomes
false it can call `reserve.update(None)`, which restores an owned original
reserve. This is a **plausible causal chain**, not a confirmed root cause.
A planned reserve shown in the card is not the observed Fronius reserve.

**Investigation and acceptance:** Reproduce Stationary restart with
physically continuous charging and unchanged current transaction; capture
generation, `ControlAuthority`, `ChargingEnabled`, TransactionEvent IDs,
GetCompositeSchedule and physical-phase responses, WBM requested/confirmed
point, measured EV power and actual mapped Fronius reserve. Determine which
individual predicate transitioned and exactly why each reserve restoration
ran. Preserve last-value-held semantics, single canonical reconciliation
and existing authority/transaction fencing; do not add speculative polling,
an independent electrical safety manager or unsafe optimistic confirmation.
Fixes must not cause repeated unwanted positive commands or overwrite
external reserve edits.

## Feature requests — approved operator intent, not implemented

### FR-01 — Resume an approved NETZ charge and its absolute schedule after HA restart

**Decision date:** 14 November 2026. **Status:** APPROVED for future
implementation; **not implemented in beta.23**. Group implementation and
regression review with the collected beta.23 findings before the next beta,
rather than changing production code during acceptance.

**Current beta.23 behavior:** HA reload retires the manager charge release and
active NETZ schedule (WBM-TIME-07, WBM-TIME-25 and WBM-RELEASE-01).
The operator requests a policy change, not a fix for a beta.23 regression.

**Desired behavior:**

- A previously explicit and durably approved NETZ Start survives a Home
  Assistant restart, including approved power, delay, optional duration and
  original absolute planned start/end timestamps. Restart is not a new Start
  and must not extend or reset the schedule.
- If the end deadline is still in the future, restore the remaining window.
  After valid registration, current-generation transaction/station evidence,
  Remote authority and hardware permission, reconcile the actual offer and
  continue the approved request. No unconditional command replay or automatic
  Remote takeover.
- If the deadline elapsed during the outage, retire schedule and manager
  release when WBM resumes. Request or confirm 0 A when registered connection,
  valid transaction and authority permit. Never restore a positive proposal
  after an expired deadline.
- If the run had no duration limit, retain the prior explicit release until
  user Stop or another genuine retirement condition. Never invent a new Start.
- **Pragmatic private-use policy:** A precise deadline stop is not guaranteed
  while HA/WBM or the OCPP link is unavailable. It is acceptable that charging
  continues temporarily under Stationary until WBM can reconnect and enforce
  the overdue Stop. No extra Stationary-side timer or duplicated scheduler.
- Physical/manual intervention remains possible: unplugging interrupts
  charging; station-side Stop, disabling hardware permission or returning
  authority to Local must not be overridden by WBM. WBM may only learn the
  changed physical state after the connection is restored. Unplugging alone
  need not cancel an otherwise active WBM business window: as already
  observed, replugging within the valid window may resume the charge.
  Determine exactly how Stationary expresses an explicit station-side Stop
  so reconnection respects the intervention.
- Treat ambiguous, damaged or unapproved persisted intent conservatively:
  it must never restore positive release. Keep operator Stop precedence,
  generation fencing, session scope, truthful readback and battery reserve
  recovery/ownership.
- Do not promise physically uninterrupted charging across a normal HA
  restart without first examining WBM shutdown behavior and Stationary's
  OCPP disconnect handling.

**Future validation:**

1. Restart during NETZ charge with finite deadline: resume only if eligible,
   preserve original end time.
2. Restart during start delay, including no plugged vehicle: preserve start
   and end timestamps and original delay deadline.
3. Restart with end time missed: retire release and reconcile 0 A when
   connection/authority return, recording outage and stop times.
4. Restart during unlimited NETZ release: restore approved intent, not a new
   approval.
5. Explicit Stop before restart: never resurrect the retired intent.
6. Reconnect under Local, hardware-disabled, unplugged, changed transaction
   or explicit station-side stop: never issue unauthorized positive commands.
7. Verify persistence failures, battery reserve, UI and the actual Stationary
   disconnection policy.

**Normative follow-up:** Revise WBM-TIME-07/25, WBM-RELEASE-01/04/08 and
related recovery/teardown requirements during implementation review.
Until then the beta.23 release-OFF-on-reload behavior remains the current
implementation baseline.

## Physical acceptance coverage

The automated entries summarize existing release and audit evidence
in the evidence column. The coverage status reflects field evidence only.

| PA | Requirement area | Evidence currently available | Coverage | Remaining practical tests | Related findings |
| --- | --- | --- | --- | --- | --- |
| PA-01 | HACS setup, binding and reload | Field: FA-01/02 verify installed version and successful reload after registry cleanup. Automated: setup and reload regressions. | **PARTIAL** | Verify preserved current entry/entity identities and settings, clean teardown, one listener and release OFF after a controlled reload. | |
| PA-02 | OCPP bootstrap, reconnect and telemetry | Field: FA-03 verifies initial boot, and FA-14 confirms charging continues through a Stationary service restart but the current-generation applied offer is initially unknown. Automated: generation fencing and missing-baseline tests. | **PARTIAL** | Trace OCPP point readback/reconciliation after reboot, including unchanged reports, inventory, generation, authoritative current point and no command replay; investigate F02a. | F02a |
| PA-03 | Authority and hardware permission | Field: FA-03 verifies explicit Local → Remote. FA-13 verifies deliberate Remote → Local retires release and subsequent explicit takeover does not auto-start. Automated: authority and permission tests. | **PARTIAL** | Change hardware ChargingEnabled externally under Local and Remote and verify ACK/Actual, preserved independence and no unexpected takeover. | |
| PA-04 | NETZ Start, Apply and Stop | Field: FA-04–06 ordinary 1p/3p Start/Apply/Stop, FA-08 pre-armed Start before plug-in, FA-09 replug, FA-13 Stop-on-authority-loss, FA-14 manual Apply after reconnect. Automated: action/approval and gating regressions. | **PARTIAL** | Verify draft isolation, Stop with pending edits, offline/permission-disabled/faulted outcomes and unattended post-reconnect point reconciliation (F02a). | F02a |
| PA-05 | NETZ timing and reconnect | Field: FA-10 without EV verifies 2-minute delay/3-minute duration and power-only Apply without deadline shift. FA-11 verifies plug-in during delay, real charging, live duration extension, timed Stop. FA-12 verifies editor rollback and independent power drafts. Automated fake-clock/OCPP tests. | **PARTIAL** | Test same-ID reconciliation within/after scheduled window under OCPP outage and phase/re-enable lockouts. Future FR-01 alters HA-restart continuity and is not a beta.23 PASS. | FR-01 (future requirement) |
| PA-06 | NETZ steps, caps and lockouts | Field: FA-04/05 verify the 1-to-3-phase change. Automated: step, cap and lockout tests. | **PARTIAL** | Check nominal steps and 0 A phase-mode cap. Observe supported phase-switch and re-enable lockout timing, including reject/wait, retry and confirmed offer. | |
| PA-07 | Session lifecycle and attribution | Field: FA-04–06 charging/end, FA-08–09 replug with release retained, FA-11 real charging with timer, F01 wrong duration display. Automated journal and replay regressions. | **PARTIAL** | Verify same-ID transaction continuity and authentic Ended across reconnect, pause, HA restart recovery, full provenance/time and distinct transaction scope; retest F01 UI. | F01 |
| PA-08 | Battery-free PV_SURPLUS | Automated: battery-free policy and release regressions. | **OPEN** | Start without a battery mapping or forecast. Vary net export, capture grid and EV power, and verify controlled steps and zero pause with release retained. | |
| PA-09 | Battery targets and PV planning | Automated: SoC transitions and planner tests. | **OPEN** | Check configured battery-equipped SURPLUS, PV_OPTIMUM and PV_MAXIMUM targets and availability before Start and across SoC changes. Verify OPTIMUM forecast transitions and MAXIMUM reserve-based target. | |
| PA-10 | FAST and BALANCE regulation | Automated: direction, policy and simulated response timing. | **OPEN** | Capture Level 2 diagnostics through FAST/BALANCE transitions, upward and downward response, phase selection, confirmation and EV settling. | |
| PA-11 | Economic STOP and restart | Automated: measured import, budget, permission gaps and restart gates. | **OPEN** | Observe budget STOP, confirmed zero offer and measured EV zero. Verify restart only after strictly more than 15 seconds of qualifying import evidence and the remaining eligibility gates. | |
| PA-12 | PV transaction rollover | Automated: same-ID recovery and new-ID reset, including absent prior Ended. | **OPEN** | Compare durable Wh and STOP history after a same-ID reconnect and a genuine new Stationary transaction. Verify the new budget on the first eligible cycle and confirm scope isolation. | |
| PA-13 | NETZ battery reserve | Field: FA-07 ordinary 26% takeover and restoration PASS. FA-14 shows confirmed WBM 30%-then-7% reserve writes while the operator reports continued charging after Stationary restart, i.e. abnormal reserve retention deviation. Automated ownership/recovery regressions. | **FAIL** for observed reconnect/continuing-charge retention; ordinary path PASS. | Reproduce and explain the two restores (F02b); verify actual mapped Fronius reserve after manual Apply, ownership, external edit preservation, failure/reload handling and safe recovery. | F02b |
| PA-14 | Fronius/BYD source timing | Automated: source normalization and missing-input rules. | **OPEN** | Compare actual source event timing, units and signs with controller snapshots, including both-positive neutral, unavailable inputs and held values during silence. | |
| PA-15 | Lovelace desktop and mobile | Field: FA-04–06 verify ordinary action/status presentation. F01 confirms a duration display failure. Automated: frontend and Chrome tests. | **FAIL** | Retest duration and verify desktop/mobile layouts, entry binding, localization, formatting, controls, warnings, PV target and resource digest after reload. | F01 |
| PA-16 | Private LAN and authenticated VPN | Automated: partial loopback routing coverage. | **OPEN** | Verify listener and station-path routing on the intended LAN and VPN, and confirm no reachability from untrusted or public networks. | |
| PA-17 | HA CPU and Recorder | Automated: partial publication-counter coverage. | **OPEN** | Compare sustained enabled/disabled CPU and actual Recorder writes with normal filters during steady and reconnect traffic. Verify V04-12's passive average CPU increase limit of 0.1 percentage points and sparse writes for identical observations. | |

## Beta.24 regression checklist

- [ ] F02a reconnect baseline. Reproduce a Stationary service restart
  during a known same-ID active NETZ charge. Capture verified OCPP generation,
  transaction, actual/desired point, physically measured charging, readback
  response and whether a new confirmed point is recovered **without Apply**.
- [ ] F02a explicit Apply comparator. Demonstrate that the **first** successful
  power Apply restores verified point confirmation; a second Apply to return
  to previous power is optional and must not be needed for confirmation.
- [ ] F02b reserve owner. Record mapped `number.speicher_storage_minimum_reserve`
  before and after Stationary restart, corresponding HA SoC, original 7%
  reserve, WBM NETZ 30% request and complete level-2 battery_reserve stages.
  Keep the operator's later intentional 10% setting change separate.
- [ ] F02b trace both restore decisions. Reproduce or explain the 10:14:27
  and 10:16:50 restorations separately; verify no restore during a continuing
  legitimately owned NETZ charge solely because the applied point temporarily
  lacks current-generation confirmation. Distinguish source observation,
  confirmed write and read-only card preview.
- [ ] F02 reconnect safeguards. Preserve authority/permission/scope
  fencing, stop precedence, ability to recover from missing readback,
  Stationary physical safety ownership and compare-before-restore semantics;
  no extra polling or duplicate control loop.
- [ ] F02 final retest. Record corrective commit/release, date, observations,
  Fronius reserve readback and complete physical result under one controlled
  reconnect.

- [ ] F01 evidence prerequisite. Capture the beta.23 entity state and actual
  `unit_of_measurement` to confirm or revise the suspected cause.
- [ ] F01 active session. On the corrective candidate, verify that the card
  advances in hours and minutes for the observed HA unit and for native
  seconds. A session lasting at least one minute should show a nonzero value.
- [ ] F01 ended session. Compare the stopped display with HA start and end
  timestamps. An interval of 1 h 46 min 32 s should display `1:46`. Verify
  that the display holds after the session ends and that unavailable duration
  stays unavailable.
- [ ] F01 scope. Verify the fix is confined to presentation. Check that the
  session start, end and energy evidence remains consistent and that OCPP
  accounting and the stored session schema are unaffected.
- [ ] F01 retest record. Record the candidate commit/release, date, raw state
  and unit, timestamps, card result and final status.

## Follow-up history

Append dated observations and link the relevant FA, PA and F IDs. Include the
tested build, Stationary version, entry scope, reproduction steps, expected and
observed results, and any supporting screenshots, HA states or scoped logs.
Record the result at the time of observation and preserve original FAIL and OPEN
entries when updating the current status. Allocate new finding IDs sequentially
from F03.

| Finding | Original observation | Corrective commit/release | Retest date | Retest result | Final status |
| --- | --- | --- | --- | --- | --- |
| F01 | [12 November 2026](#12-november-2026) | Pending | Pending | Pending | Pending · currently OPEN |
| F02 | [14 November 2026](#14-november-2026--stationary-restart-and-authority-tests) | Pending | Pending | Pending | Pending · currently OPEN |

## Source cross-checks

- openWB/core revision `849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
  `packages/control/chargepoint/chargepoint.py` lines 154–188. The plug and
  interlock checks and unplug-triggered OCPP transaction stop support the
  station responsibility boundary used in PA-04, PA-06 and PA-07. This is a
  reference implementation cross-check. Field evidence comes from the
  operator's Stationary installation. [Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py#L154-L188)
- WBM `v0.3.2-beta.18` (`87963014c9af73416c438807f523a3da2a98bd22`),
  `custom_components/wallbox_manager/session_storage.py`. The historical
  Store scheduled a one-second delayed save and flushed on orderly close.
  PA-07 uses the current schema-2 recovery criteria and the published
  beta.23 durability boundary. [Session journal recovery](release-notes-beta.23.md#session-journal-recovery)
