# Wallbox Manager v0.4.0-beta.24 — Field acceptance

**Test date:** 15 November 2026 (operator field report, local time Europe/Vienna)  
**Software:** Wallbox Manager `0.4.0-beta.24`, installed from HACS; Home Assistant Core `2026.10.3`, HAOS `18.4`.  
**Scope of this record:** Tests performed **without a vehicle connected**. No real charging current was produced. The operator supplied Lovelace, HA action and restart/terminal screenshots in the acceptance conversation. Screenshots are not committed as independent trace artifacts.  
**Release:** [v0.4.0-beta.24](https://github.com/ekkehard-lutz/wallbox-manager/releases/tag/v0.4.0-beta.24) (release commit `6a4f033ceada732646fd58a6158f904f353f1632`).  
**Previous findings:** [beta.23 operator field acceptance](https://github.com/ekkehard-lutz/wallbox-manager/blob/a4d5d9cc19a833c8246d4921caab6b8da1a544f0/docs/v0.4/field-acceptance-beta.23.md).  
**Candidate/retest specification:** [beta.24 validation](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.4.0-beta.24/docs/v0.4/release-validation-beta.24.md) and [implementation handoff](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.4.0-beta.24/docs/v0.4/beta.24-candidate.md).

These observations apply to the installed beta.24 build and the tested paths only. They do not imply a full physical acceptance, raw OCPP-command verification, or verification of untested service/race paths.

## Status definitions

- **PASS:** Operator-confirmed behavior met the specified test criteria for the stated scope.
- **PARTIAL:** Some criteria verified; the feature still has untested aspects.
- **OPEN:** Not tested or not yet concluded.
- **OUT OF SCOPE:** Deliberately not retested as part of this beta.24 acceptance.

## Field observations — no vehicle connected

| ID | Procedure and evidence | Result |
| --- | --- | --- |
| T00 · HACS install and bootstrap | Installed `0.4.0-beta.24` using HACS. The installed manifest reported beta.24. Home Assistant Core was restarted **before** reloading the Lovelace frontend card. Retired integration parameters disappeared from the configuration UI. Stationary telemetry and the existing wallbox card returned; connector `Free`, charging state `Ready`, EV power `0.00 kW`. | **PASS** for installation, version and idle UI operation. No independent OCPP audit or entity-ID inventory was captured. |
| T01 · Idle baseline | NETZ selected, release OFF (`Start charging`), no vehicle. Card showed last-session energy `18.9 kWh` and duration `3:57`, with current charging power `0.00 kW`. Battery `29.8% → 30.0%` was a profile target/preview, **not** a readback of the Fronius minimum reserve. | **PASS** for visible idle baseline; **OPEN** for F01 real-session duration and actual reserve register. |
| F03-UI · Local authority gate | Stationary switched to Local. WBM showed `Take Control`; NETZ `Start charging` became disabled. Draft nominal power could still be edited (`3.7 → 4.8 kW`) without starting charging. | **PASS** for Local UI gate and draft editing. |
| F03-legacy · Backend Local rejection | Under Local authority, HA Developer Tools invoked `button.press` on the WBM `Ladefreigabe` / charge-action button. HA rejected the call with `Charge action: control_unavailable`. No manager release was observed. | **PASS** for tested legacy button backend path; separate `wallbox_manager.charge_action` service and concurrent authority races remain **OPEN**. |
| F03-takeover · Explicit Remote | Operator clicked `Take Control`. Takeover control disappeared; NETZ `Start charging` became enabled. Requested `4.8 kW` remained, release stayed OFF, connector stayed `Free` and EV power stayed `0.00 kW`. | **PASS** for explicit takeover with no automatic NETZ release. |
| FR-02-nav · Fixed nominal steps | With both 1p and 3p station-side user current caps set to **16 A**, the UI navigated `4.8 → 4.1 → 3.7 → 4.1 → 4.8 kW` across the 1p/3p boundary. The operator also confirmed that ascending steps stop at `11.0 kW` (3p/16 A). | **PASS** for the observed fixed-step navigation and 16 A upper bound, with no physical charging. |
| FR-02-select · Removed approximation | HA Developer Tools → States no longer contained `select.wallbox01_evse_1_1_leistungsannaherung`. The obsolete configuration parameter was also absent after HACS install. | **PASS** for the removed Select/entity in this installation. |
| FR-01a · Restart during delay | Approved NETZ release with **5-minute delay** plus **5-minute duration**, no vehicle, requested `11.0 kW`. After HA Core restart during delay, the release still showed `Stop charging`; the delay continued at its remaining time (screenshots show `00:02 → 00:01`, not a new `00:05`). Requested power and 5-minute run duration remained. Temporary telemetry unavailability after restart cleared on reconnect. | **PASS** for preserving approved delayed release and original countdown during an HA Core restart without a vehicle. |
| FR-01b · Restart during run window | Delay ended and the run-duration countdown began. Operator performed a second HA Core restart in the active window; the release and remaining duration were retained. It subsequently retired automatically, both time editors cleared and the card returned to `Start charging`. Operator measured **exactly 10:00 minutes** from original approval to expiry, including the initial 5-minute delay. | **PASS** for in-window recovery and original absolute expiry without a vehicle. |
| FR-01c · Unlimited restart | Approved NETZ release with both delay and duration unset. After HA Core restart the UI again showed `Stop charging`, `11.0 kW`, and both timing fields empty. Operator explicitly ended the release; UI returned to `Start charging`. | **PASS** for unlimited approval persistence and manual retirement without a vehicle. |
| FR-01d · Expired while HA offline | Approved a **3-minute** NETZ duration (no delay), then used `ha core stop` (reported successful). Started Core with `ha core start` about **3.5 minutes after the original approval**, after the original expiry. Following reconnect the card showed release OFF (`Start charging`), blank timing fields, requested `11.0 kW` retained, connector `Free`, `0.00 kW` EV power. | **PASS** for visible expired-release retirement after HA downtime without a vehicle. A station-side positive-offer/replay audit was not captured. |

### Interpretation and constraints

- Both station current limits were already **1p = 16 A, 3p = 16 A** and were not changed for this acceptance. Using nominal 230 V, 1p legal steps are 6–16 A (1.38–3.68 kW) and 3p legal steps are 6–16 A (4.14–11.04 kW). These ranges **do not overlap**; phase retention inside an overlap cannot be tested under this configuration. Observed card values are shown rounded to one decimal place.
- The operator-tested F03 gate covers the card, legacy button, draft changes and explicit takeover. It **does not** cover every explicit service request, backend race or station-side assertion.
- The FR-01 tests confirm UI persistence, original timing and visible release retirement without an EV. They **do not** establish positive OCPP proposal, confirmed Stationary offer, same-transaction recovery, physical charging continuation, or station-side Stop while HA is offline.
- The card's retained last-session duration `3:57` does **not** validate F01. The previous beta.23 defect affected the duration of an actual session; capture sensor value, `unit_of_measurement`, elapsed wall-clock time, and card display during/after the next actual charge.
- NETZ battery SoC preview `29.8% → 30.0%` is **not** the Fronius reserve register value; no physical reserve ownership/restore behavior was accepted in this run.
- Existing PV control was **not retested**, by explicit operator decision, because the PV regulation code was not changed by beta.24 and earlier practical battery-backed PV charging had been tested. This is an accepted reuse of prior evidence, **not** new beta.24 regression evidence.

## Pending acceptance with a vehicle connected

| ID | Physical check | Status |
| --- | --- | --- |
| F01 | Compare live and ended session duration against elapsed time and HA sensor state/unit. | **OPEN** |
| F02a | During authorized NETZ charging, restart Stationary. Confirm same-generation actual composite/phase operating-point readback recovers autonomously without manual Apply or unwanted repeat positive proposals. Compare physical current and power. | **OPEN** |
| F02b | Record **actual** Fronius reserve before charging; verify temporary NETZ reserve activation, uninterrupted retention through Stationary restart/reconnect, restoration after genuine Stop, and precedence of an externally changed reserve. Earlier exact restore triggers remain unproven. | **OPEN** |
| FR-01 | Verify actual charging continuation through HA Core restart for active, delayed, unlimited and expired schedules as applicable, including confirmed station offer/transaction evidence; check station-side Stop during HA downtime prevents stale positive replay. | **OPEN** |
| FR-02 | Verify fixed nearest under active charging, current/phase feedback and phase transition at 1p/16 A ↔ 3p/6 A. No phase-overlap scenario exists with these two 16 A caps. | **OPEN** |
| F03 | Optionally exercise explicit `charge_action` backend service and authority-loss race separately from proven UI/legacy path. | **OPEN** |
| PV regression | Not rerun for beta.24 by operator decision; earlier operator-confirmed PV battery control tests are retained as prior evidence. | **OUT OF SCOPE** |

**Pre-vehicle boundary:** The most recent documented NETZ state after FR-01d is release **OFF**, connector `Free`, EV power `0.00 kW`. Confirm station authority, hardware permission and the actual Fronius reserve before starting a physical test. Stationary retains responsibility for electrical protection, hardware current caps, interlocks and phase switching.
