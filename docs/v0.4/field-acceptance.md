# Wallbox Manager v0.4.0 field acceptance evidence

**Test dates:** 15–16 November 2026 (operator field reports, local time Europe/Vienna)

**Software:** Wallbox Manager `0.4.0-beta.24`, installed from HACS; Home Assistant Core `2026.10.3`, HAOS `18.4`.

**Scope of this record:** First, tests **without a vehicle connected**; subsequently, **BYD Seal U connected** and actual NETZ charging tested on 16 November. The operator supplied Lovelace, HA action, Fronius and restart/terminal screenshots and follow-up observations in the acceptance conversation. Screenshots were not committed as independent trace artifacts.

The tested build was v0.4.0-beta.24 at `6a4f033ceada732646fd58a6158f904f353f1632`.
The v0.4.0 release carries the same runtime.

The prerelease record includes the [candidate validation and retest specification](https://github.com/ekkehard-lutz/wallbox-manager/blob/6a4f033ceada732646fd58a6158f904f353f1632/docs/v0.4/release-validation-beta.24.md) and [implementation handoff](https://github.com/ekkehard-lutz/wallbox-manager/blob/6a4f033ceada732646fd58a6158f904f353f1632/docs/v0.4/beta.24-candidate.md).

These observations apply to the installed beta.24 build and the tested paths only. They do not imply a raw OCPP-command verification, precise continuous hardware trace, or verification of untested service/race paths.

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
- The FR-01 tests in this *no-vehicle section* confirm UI persistence, original timing and visible release retirement without an EV. Separate vehicle-connected follow-up tests appear below; neither set includes a raw OCPP trace.
- The card's retained last-session duration `3:57` does **not** validate F01. The previous beta.23 defect affected the duration of an actual session; physical session display was subsequently tested below, but HA sensor state and `unit_of_measurement` were not independently captured.
- NETZ battery SoC preview `29.8% → 30.0%` is **not** the Fronius reserve register value; no physical reserve ownership/restore behavior was accepted in this run.
- Existing PV control was **not retested**, by explicit operator decision, because the PV regulation code was not changed by beta.24 and earlier practical battery-backed PV charging had been tested. This is an accepted reuse of prior evidence, **not** new beta.24 regression evidence.

## Physical field acceptance — BYD Seal U connected (16 November 2026)

The following results were witnessed by the operator in the live system. Screenshots establish the displayed status at the captured points; continuity between screenshots, exact lockout timing, and absence of low-level command replay were **not independently recorded**. In particular, the reported station-side Stop while HA was offline is an operator confirmation without a raw Stationary trace.

| ID | Physical procedure and observed evidence | Result |
| --- | --- | --- |
| P01 · Initial charge | NETZ request **6.9 kW**, connector occupied; Stationary reported **3-phase / 10 A**, EV power approximately **6.42–6.45 kW**, release active and EV charging. Card's session energy increased normally. The BYD integration showed an unrelated "Stromwarnung: Problem", which the operator identified as an integration issue, not a vehicle electrical warning. | **PASS** for actual 3p/10 A NETZ operation. |
| F01 · Session duration | Car was already connected for approximately 1.5 h; card showed connected-session duration progressing **1:21 → 1:22 → 1:25** and later **1:59** after manual charging Stop. During subsequent connected sessions the timer continued **2:08 → 2:24** and energy accumulated. | **PASS** for operator-observed displayed duration during/after charging; exact HA sensor unit/metadata was **not** independently compared. Duration measures the connected session, not merely the period of positive EV power. |
| F02b · Reserve activation | Fronius minimum reserve was **20%** before charging; NETZ reserve setting **40%**, battery SOC **33.7%**. While positive EV charging was observed, actual Fronius minimum-reserve HA control read **33%**. The reserve calculation was capped by current SOC and the entity's integer-step quantization, matching the card preview. | **PASS** for actual temporary reserve acquisition, **20% → 33%**. |
| F02a · Stationary service restart | During actual NETZ charging at 3p/10 A, operator executed `sudo systemctl restart wallbox`. Card briefly reported an OCPP disconnection; charging and the **3p/10 A** operating point returned automatically, approximately **6.37 kW** observed. No manual Apply was needed. | **PASS** for automatic service-restart recovery at the observed point. |
| F02a · Raspberry Pi reboot | Operator rebooted Stationary host while charging. On reconnect the card briefly showed an unconfirmed point (`— → 3-phase 10 A`) and later confirmed **3-phase / 10 A** without manual action. This was explicitly rechecked in an additional Pi reboot after an initial observation had been made too soon; recovery occurred within approximately one minute according to operator estimate. | **PASS** for autonomous readback/reconfirmation; precise latency and low-level messages were not measured. |
| F02a · EVSE + Raspberry Pi power cycle | Operator powered both the EVSE controller and Pi down and back up via supply isolation. At startup the EVSE boot setting briefly allowed approximately **11 kW** (3p/16 A); the manager subsequently restored the approved **3p/10 A / 6.9 kW nominal** operating point automatically. | **PASS** for eventual recovery; **important caveat:** boot-amps startup temporarily exceeds the approved manager setpoint. This is a distinct station configuration/operational safety consideration, not proof that the requested limit was continuously met. |
| F02b · Reserve retention and manual Stop | During/after the Stationary restarts the actual Fronius control still showed **33%** at observed checkpoints. Upon operator pressing `Ladung beenden`, EV power became **0.00 kW**, Stationary showed **3p/0 A**, release OFF, and Fronius minimum reserve returned to its original **20%**. Session ended with **4.0 kWh** and **1:59** duration in the card. | **PASS** for observed reserve retention at checkpoints and exact original-value restoration. A short unobserved transient cannot be excluded. |
| FR-02 · Physical fixed nominal steps | While charging, changing request **6.9 → 4.1 → 3.7 → 4.8 kW** produced observed **3p/10 A (~6.39 kW)**, **3p/6 A (~4.01 kW)**, **1p/16 A (~3.53 kW)**, and **3p/7 A (~5.30 kW)** respectively. `Ladeparameter übernehmen` was used for the edit actions. | **PASS** for the observed electrical points and both phase directions. The actual EV-power measurement is not asserted to equal nominal requested kW. |
| FR-02 · Stationary lockouts | An immediate reenable after Stop returned `ReenableLockout`; phase changes showed temporary phase-switch lockout. The operator observed that both requested starts and return phase switching were automatically continued after the stationary lockout, without resubmitting the command. | **PASS** for observed bounded automatic continuation after lockout; exact lockout periods not recorded. |
| FR-01e · Unlimited NETZ across HA Core restart | During physical NETZ charging at **3p/7 A** and requested **4.8 kW**, operator restarted HA Core. UI recovered release ON, no duration cap, **3p/7 A**, EV power **5.35 → 5.31 kW**, session energy **4.7 → 4.8 kWh**, session duration **2:16 → 2:18**. No manual restart of charging needed. | **PASS** for visible active charging and retained session after HA restart; a strictly uninterrupted current trace was not captured. |
| FR-01f · Timed NETZ across HA Core restart | Operator set an actual charging window to **5 minutes** at requested **4.8 kW (3p/7 A)** and restarted HA Core while active. Card showed remaining run time **00:04** before and **00:02** after restart; EV charging recovered at about **5.33 kW**. Operator confirmed that the charge ended **exactly 5 minutes** after original approval. Actual Fronius reserve was reported restored after expiry. | **PASS** for operator-confirmed original deadline, active charging recovery and reserve restoration. |
| FR-01g · Station-side Stop while HA offline | Operator confirmed execution of the final scenario: HA Core stopped during approved NETZ charging, charging stopped directly at Stationary, HA restarted, and charging **did not restart automatically**. The operator also confirmed original Fronius reserve restoration. | **PASS (operator-reported)** for the visible no-restart behavior and reserve restoration; independent Stationary/OCPP command trace not captured. |

### Operational notes and remaining limitations

- **16 A on both 1p and 3p** remained the fixed station-side cap; no overlap exists to test phase preference in shared legal ranges. Stationary owns phase-switch timing and electrical interlocks.
- The brief **~11 kW boot-amps** behavior during complete EVSE/Pi power cycling is important even though WBM reconverged. Evaluate the station's independent start-on-power behavior against the site's intended fail-safe policy; do not reinterpret recovery as a continuous power limit.
- Physical EV power at 3p/7 A was observed around **5.3 kW** versus **4.8 kW nominal**. This is an observation only; no phase-voltage/current meter comparison was performed. Avoid claiming verified power accuracy.
- Fronius reserve at **33%** was directly observed during charge; the initial **20%** was directly observed after stopping. The later reserve preview of **36%** during tests was not separately confirmed by a Fronius slider screenshot; operator confirmed restoration after FR-01f and FR-01g.
- **External modification of the shared Fronius reserve while a WBM override is owned** (external precedence / compare-before-restore) remains **OPEN**. The historical exact F02 trigger cases and RR-01 journal limitation were not additionally traced.
- F03 Local UI and legacy button rejection were tested before physical charging, but the standalone `wallbox_manager.charge_action` service and authority-change races remain **OPEN**.
- Delayed NETZ schedules, unlimited approvals, expiry while HA was offline and absolute timings were tested **without** a car; timed and unlimited active charging were then tested **with** a car. This distinction matters for any exhaustive certification claim.
- PV regulation was **not retested**, by explicit operator decision because it was not changed in beta.24. This is a scope decision rather than fresh regression evidence.
- No raw OCPP trace, live transaction id comparison, exact low-level repeat-write audit, independently checked session sensor unit, or continuous current/voltage time series was collected.

## Acceptance summary

| Feature | Field outcome |
| --- | --- |
| T00 HACS / Core bootstrap | **PASS** |
| F01 duration display | **PASS** for displayed connected-session time; unit/metadata not independently inspected |
| F02a operating point recovery | **PASS** in service restart, Pi reboot (repeated), and EVSE+Pi power cycle, with temporary boot-amps caveat |
| F02b Fronius reserve | **PASS** on observed activation, retention checkpoints and restoration after manual/automatic/station-side Stops; external precedence **OPEN** |
| F03 Authority Local | **PASS** for UI, legacy button and explicit takeover; standalone service/races **OPEN** |
| FR-01 scheduling / HA recovery | **PASS** for the tested no-EV and with-EV scenarios, including station-side Stop during downtime (operator reported) |
| FR-02 fixed nearest and phase switching | **PASS** for 16 A caps, fixed nominal steps, removed Select, actual 3p↔1p and automatic lockout continuation |
| PV regression | **OUT OF SCOPE** by operator choice |

**Last known field state:** The operator reported successful completion of FR-01g and correct Fronius reserve restoration, with no unintended charging restart. The accepted build remains the `v0.4.0-beta.24` build tested in this report; this document-only update does not change the tagged release code.

## Prior PV and mobile evidence reused for the stable baseline

The 15 November 2026 operator report confirmed ordinary battery-equipped PV
charging had been repeatedly analyzed, optimized and used successfully
(FA-24). The operator elected to reuse this evidence. Individual dates and
a new FAST/BALANCE transition trace were unavailable. The evidence leaves
battery-free operation, precise economic STOP/restart thresholds,
transaction rollover, reconnect races and formal performance open for
targeted field investigation.

With the EV unplugged and manager release off, screenshots showed 44.5%
battery SoC and targets of 95% for PV_SURPLUS, 95% for PV_OPTIMUM on a
low-remaining-PV day, and 32% for PV_MAXIMUM (FA-23). Raw forecast and
reserve/hysteresis inputs were unavailable. The operator also confirmed
regular iPhone NETZ operation, with screenshot evidence of the layout and
reported successful power and timing edits (FA-21 and FA-22). These
observations concern the beta.23 installation used for that evidence.
