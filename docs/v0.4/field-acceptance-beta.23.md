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

## Findings register

| ID | Date | Subsystem | Expected behavior | Observed behavior | Evidence | Severity | Status | Suspected cause | Next investigation | Proposed fix release |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [F01](#f01--lovelace-session-duration) | 12 November 2026 | Lovelace session display | Display elapsed duration in hours and minutes consistent with the bound HA session. | Card showed `0:00` for a session lasting approximately 1 h 46 min 32 s. | [Operator log](#12-november-2026) and [beta.23 source inspection](#f01--lovelace-session-duration). | P2 · UI presentation | **OPEN** | Likely mismatch between the HA state unit and the frontend's seconds assumption. The live unit is unconfirmed. | Inspect the live `unit_of_measurement` together with the numeric state and compare the card output with elapsed time. | beta.24 candidate |

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

## Physical acceptance coverage

The automated entries summarize existing release and audit evidence
in the evidence column. The coverage status reflects field evidence only.

| PA | Requirement area | Evidence currently available | Coverage | Remaining practical tests | Related findings |
| --- | --- | --- | --- | --- | --- |
| PA-01 | HACS setup, binding and reload | Field: FA-01/02 verify installed version and successful reload after registry cleanup. Automated: setup and reload regressions. | **PARTIAL** | Verify preserved current entry/entity identities and settings, clean teardown, one listener and release OFF after a controlled reload. | |
| PA-02 | OCPP bootstrap, reconnect and telemetry | Field: FA-03 verifies initial connection, BootNotification and arrival of readings. Automated: generation fencing and missing-baseline tests. | **PARTIAL** | Capture inventory and scoped Actual readbacks across reconnect. Verify per-field initialization, unavailable fields and unchanged observations without redundant updates. | |
| PA-03 | Authority and hardware permission | Field: FA-03 verifies Local to Remote via explicit Take Control and observed enabled permission. Automated: authority and permission tests, including the Stationary source fixture. | **PARTIAL** | Change hardware permission externally under Local and Remote. Compare acknowledgements, Actual readback, release and Stationary state. Verify return to Local and release retirement. | |
| PA-04 | NETZ Start, Apply and Stop | Field: FA-04/05/06 verify ordinary actions. Automated: action, approval and hardware gating regressions. | **PARTIAL** | Check draft isolation and Stop with pending edits, including offline, faulted and permission-disabled cases. Verify release retires without approving drafts or issuing a positive proposal. | |
| PA-05 | NETZ timing and reconnect | Field: FA-04 used immediate Start with no duration limit. Automated: fake clock and OCPP resume tests. | **OPEN** | Exercise nonzero delay and finite duration, editing within the lease, same-ID reconciliation in-window, expiry across reconnect and explicit second Start. Record absolute deadlines. | |
| PA-06 | NETZ steps, caps and lockouts | Field: FA-04/05 verify the 1-to-3-phase change. Automated: step, cap and lockout tests. | **PARTIAL** | Check nominal steps and 0 A phase-mode cap. Observe supported phase-switch and re-enable lockout timing, including reject/wait, retry and confirmed offer. | |
| PA-07 | Session lifecycle and attribution | Field: FA-04–06 and F01 record charge, unplug and HA session readings. Automated: boundaries, replay and journal recovery regressions. | **PARTIAL** | Verify pause, same-ID continuity across reconnect, recovery across HA restart and authentic Ended. Capture scoped transaction evidence, registered generation and boundary provenance. | F01 (display) |
| PA-08 | Battery-free PV_SURPLUS | Automated: battery-free policy and release regressions. | **OPEN** | Start without a battery mapping or forecast. Vary net export, capture grid and EV power, and verify controlled steps and zero pause with release retained. | |
| PA-09 | Battery targets and PV planning | Automated: SoC transitions and planner tests. | **OPEN** | Check configured battery-equipped SURPLUS, PV_OPTIMUM and PV_MAXIMUM targets and availability before Start and across SoC changes. Verify OPTIMUM forecast transitions and MAXIMUM reserve-based target. | |
| PA-10 | FAST and BALANCE regulation | Automated: direction, policy and simulated response timing. | **OPEN** | Capture Level 2 diagnostics through FAST/BALANCE transitions, upward and downward response, phase selection, confirmation and EV settling. | |
| PA-11 | Economic STOP and restart | Automated: measured import, budget, permission gaps and restart gates. | **OPEN** | Observe budget STOP, confirmed zero offer and measured EV zero. Verify restart only after strictly more than 15 seconds of qualifying import evidence and the remaining eligibility gates. | |
| PA-12 | PV transaction rollover | Automated: same-ID recovery and new-ID reset, including absent prior Ended. | **OPEN** | Compare durable Wh and STOP history after a same-ID reconnect and a genuine new Stationary transaction. Verify the new budget on the first eligible cycle and confirm scope isolation. | |
| PA-13 | NETZ battery reserve | Field: FA-07 verifies ordinary takeover and restoration. Automated: ownership and recovery regressions. | **PARTIAL** | Verify read-only preview, reload recovery, external edit preservation, failed writes and restoration under abnormal conditions. | |
| PA-14 | Fronius/BYD source timing | Automated: source normalization and missing-input rules. | **OPEN** | Compare actual source event timing, units and signs with controller snapshots, including both-positive neutral, unavailable inputs and held values during silence. | |
| PA-15 | Lovelace desktop and mobile | Field: FA-04–06 verify ordinary action/status presentation. F01 confirms a duration display failure. Automated: frontend and Chrome tests. | **FAIL** | Retest duration and verify desktop/mobile layouts, entry binding, localization, formatting, controls, warnings, PV target and resource digest after reload. | F01 |
| PA-16 | Private LAN and authenticated VPN | Automated: partial loopback routing coverage. | **OPEN** | Verify listener and station-path routing on the intended LAN and VPN, and confirm no reachability from untrusted or public networks. | |
| PA-17 | HA CPU and Recorder | Automated: partial publication-counter coverage. | **OPEN** | Compare sustained enabled/disabled CPU and actual Recorder writes with normal filters during steady and reconnect traffic. Verify V04-12's passive average CPU increase limit of 0.1 percentage points and sparse writes for identical observations. | |

## Beta.24 regression checklist

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
from F02.

| Finding | Original observation | Corrective commit/release | Retest date | Retest result | Final status |
| --- | --- | --- | --- | --- | --- |
| F01 | [12 November 2026](#12-november-2026) | Pending | Pending | Pending | Pending · currently OPEN |

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
