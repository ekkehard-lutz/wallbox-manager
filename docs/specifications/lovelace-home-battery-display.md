# Lovelace card: home-battery status and simplified PV controls

**Decision:** 25 October 2026. **Status: APPROVED UI/presentation requirements only.**
**IDs:** WBM-HS-UI-01–15. This approval does **not** approve the overall
[PV regulation V3](pv-regulation.md) DRAFT or authorize implementing PV control
algorithms. It is a separately approved Lovelace presentation change.
Documentation first; implementation, review, merge, release and deployment
are separate stages.

## Operator-facing layout

| ID | Requirement |
| --- | --- |
| **WBM-HS-UI-01** | In the existing Lovelace wallbox card add a read-only **Hausspeicher** (English: **Home battery**) status block in the right-hand column, beneath the live **Ladezustand / Leistung** column, horizontally aligned with the existing lower-left **Wallboxparameter** block and its `x-phasig yA` value. Preserve the existing wallbox-parameter display, including its applicable actual → requested representation. On narrow cards, stack the blocks legibly; do not require horizontal scrolling. |
| **WBM-HS-UI-02** | Under **Hausspeicher**, show **Ladezustand** (English: **State of charge**) and the compact value `69,0 % → 40,0 %` (German) / `69.0 % → 40.0 %` (English): actual home-battery SoC on the left; the **currently applicable profile-dependent target/reference** on the right. The arrow indicates a comparison, **not** charging direction, a promised battery action or a battery-control command. Display-only; never editable in this block. |
| **WBM-HS-UI-03** | Actual SoC comes from the already configured HA battery-SoC mapping (`soc_speicher_aktuell` in the existing NETZ/storage mapping terminology), not the EV SoC, a configured desired reserve or inferred grid power. If unavailable, show `—` for the missing side rather than assuming zero; a valid 0 % is a real zero. Valid HA values remain held according to WBM-ARCH-05/09, not expired by an invented frontend TTL. |
| **WBM-HS-UI-04** | **PV_SURPLUS with storage:** right side is the profile's applicable fixed target `T` from the existing `soll_soc_speicher` setting under PV-SOC-01/03. Show the effective validated target, not a pending, invalid or fabricated value. Recheck the full-precision observed reserve against PV-SOC-01 (`R + H <= T <= 100 - H`); if an external reserve change invalidates a saved target or reserve is unavailable, show `—` without modifying or clamping that setting. Do not introduce a second target parameter. |
| **WBM-HS-UI-05** | **PV_OPTIMUM:** right side is the **current effective dynamic target `T`** provided by the canonical PV planner according to PV-PLAN-02/03. This includes the upper target in `BEFORE_SURPLUS`, the calculated target in `DYNAMIC`, and the upper target in latched `FINISHED`. The canonical planner maintains this target while the selected PV profile has charging release OFF, so the operator can inspect it before Start; no OCPP connection, authority or transaction is required solely for T. Never implement/reproduce planning, forecasts, time arithmetic, midnight resets or SoC smoothing in JavaScript merely to populate the card. |
| **WBM-HS-UI-06** | **PV_MAXIMUM:** right side is the applicable target `T = R + H` from PV-SOC-01/03, where `R` is the valid reported minimum home-battery reserve and `H` is the existing `soc_hysterese` setting. Prefer the canonical backend's effective target projection over an independent card-side calculation. Preserve existing validation/bounds and reserve semantics. |
| **WBM-HS-UI-07** | **NETZ:** the right side is **the presently observed/confirmed writable home-battery minimum reserve** (`min_soc_speicher` mapped HA Number), including any currently applied temporary NETZ increase, **not** the user-requested NETZ `min_soc`, the original pre-override reserve or a PV target. Follow externally changed actual values as observed. This readout does not itself request, raise or restore reserve. |
| **WBM-HS-UI-08** | The display must not introduce a battery dependency for NETZ or for **battery-free PV_SURPLUS** (PV-ARCH-02/03). With no configured/usable home battery, no available actual reserve, or no legitimately computed PV target, show a clear unavailable value (`—` or contextually `kein Hausspeicher` / `No home battery`) rather than calculating a fictional target or disabling a charging profile. An OCPP disconnect alone does not invalidate otherwise valid HA battery observations. |
| **WBM-HS-UI-09** | Treat left- and right-hand data validity independently; do not turn missing target into `0 %`, missing SoC into `0 %`, or a queued setting into confirmed hardware reserve. Display **every available percentage in this block with exactly one locale-aware decimal place**, using the HA frontend language, including trailing zeros and valid zero (`0,0 %` / `0.0 %`). For example, `69,3 % → 40,0 %` in German and `69.3 % → 40.0 %` in English. This applies to actual SoC and all four profiles’ target/reference values, including future effective targets. Round only at presentation; preserve full-precision observations, settings, validation and backend calculations. Missing sides remain `—`. Formatting introduces no backend updates or polling. Clear accessible names/tooltip or equivalent must distinguish **Ziel-SoC** in PV from **aktuelle Mindestreserve** in NETZ, while retaining the compact common arrow layout. |
| **WBM-HS-UI-10** | Use existing event-fed HA observations and canonical WBM state, not frontend polling, a second battery planner/regulator or periodic OCPP commands. If the effective PV target is not yet exposed, a minimal **read-only backend projection** may be added when legitimately available. Until then show `—` for that target; **do not implement the unapproved PV regulator as a dependency of this display work**. Avoid high-frequency HA state/Recorder publication of unchanged values. |

## Simplification of profile editors (presentation only)

| ID | Requirement |
| --- | --- |
| **WBM-HS-UI-11** | Remove these five inputs **only from the custom Lovelace card**: `optimum_lower_soc` (**Unterer Ziel-SoC**), `optimum_upper_soc` (**Oberer Ziel-SoC**), `optimum_max_discharge_w` (**Maximale Speicher-Entladeleistung**), `estimated_daily_house_consumption_kwh` (**Täglicher Hausverbrauch**), and `soll_soc_speicher` (**Speicher-Ziel-SoC**). This includes the former PV_SURPLUS target editor and PV_OPTIMUM/PV-profile tuning editors. No empty placeholders or hidden interactive controls should remain in the rendered card. |
| **WBM-HS-UI-12** | Retain the five existing HA Number entities, unique IDs, entity/service APIs, persisted settings, data validation, defaults and all backend consumers. Values remain editable in Home Assistant's normal entity/integration configuration views. **No admin/user roles, technical permission separation, access restrictions or security mechanism** are introduced. The absence of a Lovelace editor is purely a usability choice. |
| **WBM-HS-UI-13** | Preserve existing Lovelace control semantics for charging profile selection, NETZ requested-power selector and time editors, the unified Start/Apply/Stop action, Remote takeover, the live status/session/energy displays, **Wallboxparameter**, warnings and responsive layout. Existing NETZ reserve/approximation editor removal per WBM-GRID-40 remains valid; this decision must not accidentally reintroduce them. |
| **WBM-HS-UI-14** | Removing these controls must not change the existing SoC state machine (`L/M/U`), PV planning/STOP/restart economics, battery reserve override and restoration (WBM-GRID-15), OCPP lifecycle/authority, manager charge release, charging capabilities or stationary physical safety. No configuration migration, entity deletion or new backend edit policy is permitted merely because an editor disappeared. |

## Acceptance and source/target distinction

**WBM-HS-UI-15:** Cover at least the following deterministic scenarios in card/backend tests:

1. Side-by-side layout at ordinary desktop widths and legible stacking on narrow mobile cards; existing **Wallboxparameter** (including requested vs applied) remains intact.
2. Battery SoC 69 %, reference 40 % → `69,0 % → 40,0 %` (German) / `69.0 % → 40.0 %` (English); integer and fractional values, trailing zeros, valid zero, rounding (69.25 → 69.3), both frontend languages and all four profiles use exactly one decimal; independent missing/unknown/unavailable values display `—` without warning fabrication.
3. PV_SURPLUS with mapped battery shows effective fixed target. A valid T=40 with H=2 and R=20 remains 40.0 after R rises to 38, becomes `—` when external R exceeds 38, and becomes available again when R returns to range; validation uses unrounded values. Missing/invalid reserve or target shows `—` independently of SoC. Battery-free PV_SURPLUS stays usable and has no invented SoC.
4. PV_OPTIMUM shows backend-reported effective target through `BEFORE_SURPLUS`, `DYNAMIC` and `FINISHED`, including local-day transitions and forecast changes **without** duplicating the planner in the card. If the planner is not implemented/has no valid target, show `—`; do not pretend the algorithm ran.
5. PV_MAXIMUM displays backend effective `R + H`; changing real reserve or hysteresis is reflected through valid canonical observation.
6. NETZ with originally 20 %, requested `min_soc` 40 % and currently **actually set** minimum reserve 40 % displays 40 %. If the writable HA reserve instead reports 65 %, display **65 %**, not the requested 40 %; if an override is merely pending/unconfirmed, display the actual observed value. Check lifecycle and external edits with WBM-GRID-15.
7. All five input controls are absent from the card for every profile; the corresponding HA entities/services continue to work and settings survive card refresh, HA reload and profile changes.
8. Multiple configured wallbox cards use their own mappings/state and update consistently; language switching and offline OCPP do not lead to invented battery invalidation. No new polling loop, duplicate target calculations, control effects or additional Recorder churn.

### Compatibility / conflict review (25 October 2026)

| Existing requirement | Result |
| --- | --- |
| PV-SOC-01/03, PV-PLAN-02/03 | **No algorithm change.** Their `T`, `R`, `H`, ordered optimum limits and planner phases remain authoritative. “User-defined” means configurable via existing HA entities, **not necessarily through Lovelace**. |
| PV-ARCH-02/03 | Battery-free PV_SURPLUS and NETZ independence are **preserved**; no new mandatory PV/BYD inputs. |
| WBM-GRID-15 | Distinguish requested `min_soc` from writable **actual `min_soc_speicher`** and original reserve; display does not assume an override command succeeded. |
| WBM-GRID-40 | Existing removal of NETZ reserve and approximation editors is consistent with this further PV-card decluttering. |
| WBM-ARCH-01–12, PV-ARCH-05/06, WBM-DISP-01–03 | Backend owns regulation and source state; card is a projection, with no second loop, implied safety gate or changed session-energy/live-power precision. |
| WBM-RELEASE-01–14, WBM-PV-RELEASE-01–07, WBM-TIME-01–24 | Unified button, manager release and NETZ timing are unchanged. |

**Implementation boundary:** Current `develop` card contains the five editors.
The consolidated PV regulation V3 remains a **DRAFT** and the active-control
specification explicitly defers PV regulator implementation. An effective
PV_OPTIMUM/PV_MAXIMUM target must not be invented just to make the new card
show a number. The separate implementation task must report exactly which
values are currently available and which cannot yet be displayed truthfully.
