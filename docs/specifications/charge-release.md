# WBM charging release, NETZ action button, and hardware-enable separation

**Status: APPROVED REQUIREMENTS — coordinated feature implementation (24 October 2026).** Implementation and simulated verification are recorded in [the implementation report](../coordinated-control-redesign.md). Independent review, merge, release and hardware acceptance remain separate. This document is normative for the upcoming WBM UI/control redesign and supersedes the specifically conflicting historical requirements in `active-ocpp-control.md`, `grid-profile.md`, `automatic-vehicle-wakeup.md` and the PV V3 draft. It does not approve the PV algorithms themselves.

## Terminology and ownership

Three states must **never** be conflated:

1. **Hardware Wallbox enable/disable:** `WallboxController.ChargingEnabled.Actual`, physically implemented by Stationary's EVSE permission bit. It enables/disables the **whole wallbox**; it is not the WBM profile gate. Retain the HA integration/device-page switch and the observed Actual state. Remove its control from the Lovelace wallbox card.
2. **WBM Ladefreigabe (manager charge release):** a **per-connector, manager-owned boolean user intent**. This is not `ChargingEnabled`, not an OCPP variable and not vehicle charging evidence. False means WBM may not issue a positive current/phase operating point; when WBM has authority and a valid executable transaction, it commands **0 A** as a regular charging pause. True permits eligible profile current/phase requests, including a calculated **0 A for future PV profiles**. It must never turn hardware enable ON/OFF as a side effect.
3. **ControlAuthority:** who may specify the station's offered **current and phase**. Only explicit takeover obtains Remote/OCPP current/phase authority; Local remains responsible otherwise. Hardware `ChargingEnabled` shall be writable from WBM regardless of Local/Remote authority (while station is reachable), without claiming current/phase authority. Actual station feedback determines success.

Changing the last distinction requires a coordinated Stationary update: Stationary already has an authority-independent local `set_enabled()` path; its OCPP `set_ocpp_enabled()` currently enforces Remote authority. WBM currently rejects the command under Local as well. This is feasible with small, scoped changes on both sides; implemented on the coordinated feature branches.

## Normative WBM release contract (WBM-RELEASE-01–14)

| ID | Requirement |
| --- | --- |
| WBM-RELEASE-01 | Maintain one authoritative per-connector boolean `charge_release`, initially/after migration default **false** unless a future explicit, safe state migration decision preserves an operator-confirmed same-generation intent. Persist explicit intent centrally as appropriate; HA reload/reconnect must never silently initiate charging. Multiple cards, direct HA services and backend operations share the same state. |
| WBM-RELEASE-02 | A **real profile selection change** immediately sets `charge_release=false`, invalidates/supersedes old positive intent, outstanding approvals/dispatch/retry generations, and requests a normal 0 A operating point when Remote authority and a valid transaction permit it. Profile editing without a change is not a profile switch. Do not take Remote authority automatically. |
| WBM-RELEASE-03 | Turning release **OFF** during a running manager-controlled charge requests 0 A, not `ChargingEnabled=false`, connector Unavailable, or a fabricated transaction end. Preserve the hardware-enabled state. Subject the command to existing OCPP transaction identity, authority, command serialization, readback and Stationary re-enable/phase lockouts. Never claim confirmed zero before confirmation. |
| WBM-RELEASE-04 | If WBM lacks current/phase authority, registered connection, or an eligible transaction, keep its internal release false but **do not issue unauthorized OCPP current writes** or pretend the hardware is physically stopped. Present the blocked/deferred outcome. Losing authority or station connection invalidates positive dispatch intent; never auto-reactivate from stale release. |
| WBM-RELEASE-05 | Turning release **ON** requires an explicit user action from the current profile. It authorizes that profile's operation, not hardware `ChargingEnabled` and not an authority takeover. Enforce existing authority, station capability, transaction and safety delegation boundaries at dispatch. No station-hosted equivalent of `charge_release` is required. |
| WBM-RELEASE-06 | Keep `ChargingEnabled` as its **own** HA integration/device-page switch (ideally labeled `Wallbox enabled`/`Wallbox aktiviert` to prevent confusion), never the wallbox card's Ladefreigabe button. Its command must be authority-independent in both WBM and Stationary, but still require a live OCPP path, supported variable, valid scope, proper ACK/readback and generation fencing. Do not silently take control, change profile/current/phase, reset manager release, or invent an Actual value. |
| WBM-RELEASE-07 | A hardware-enabled action while Local authority is active may cause the Wallbox's own local current target to take effect. Display that this is a **hardware permission**, not a WBM-authorized profile start, and never infer Remote authority from it. |
| WBM-RELEASE-08 | Use existing station `SetChargingProfile` for zero and positive manager points, not a new permission OCPP extension. Continue distinguishing desired, ACKed, confirmed offered and measured power. Release false is a hard WBM dispatch gate against later positive profile retries, cached schedules or stale callbacks. |
| WBM-RELEASE-09 | Backend owns all button state, pending-draft equivalence, persistence, authorization and operation results. Lovelace reads canonical state and invokes services; it is not a separate safety or state engine. Disable unavailable actions with intelligible reasons. Prevent duplicates, late callbacks and stale revisions across card instances, profile changes, disconnect, restart and authority changes. |
| WBM-RELEASE-10 | Prefer one per-connector action interface/button in Lovelace; preserve useful HA entity identity and diagnostic history, but remove the obsolete standalone NETZ SET action from the active UI/control contract when replacing it. Plan safe entity-registry and stored-profile migration before deletion. |
| WBM-RELEASE-11 | Operational charging remains subject to separately observed hardware `ChargingEnabled=true`; internal release true cannot override disabled wallbox hardware. Keep `charging_allowed` vs `charging_active` vs `charge_release` distinct. |
| WBM-RELEASE-12 | Reconcile previous pending NETZ drafts and approved/active settings without silently approving them on upgrade, connection recovery, reload or profile switch. For a newly selected profile, default release OFF even if that profile has a saved positive setpoint. |
| WBM-RELEASE-13 | Preserve existing one-shot NETZ start-delay/duration semantics and battery reserve ownership. A user stop supersedes pending delayed starts/retries. A new explicit start creates the next eligible schedule; applying parameters during an ongoing charge must not incorrectly restart duration/start-delay. |
| WBM-RELEASE-14 | Automatic vehicle wake-up is **no longer part of WBM**. Remove the `auto_wakeup_enabled` HA switch, associated OCPP variable handling, startup subscriptions, translations, unique-entity exposure and related control paths in the future implementation. Stationary owns the optional local INI setting and the physical automatic wake-up algorithm. Preserve independent legacy manual DataTransfer only where separately specified; this change does not authorize arbitrary OCPP wake-up messages. |

## NETZ button state machine (WBM-NETZ-ACTION-01–13)

**One button replaces BOTH the Lovelace hardware-permission button and the separate SET button.** Its action always relates to the internal WBM charge-release intent. The old station hardware-enabled switch remains visible **only** on the HA device/integration page.

| ID | Requirement |
| --- | --- |
| WBM-NETZ-ACTION-01 | When NETZ `charge_release=false`, show **Ladung starten** (Start charging). Enable only if validated current draft settings resolve through the approved NETZ phase/current rules to a **positive executable current**. Otherwise grey it out. The button must not start a charge just because draft numeric power is nonzero if DOWN/NEAREST resolves it to 0 A. |
| WBM-NETZ-ACTION-02 | Start action validates and atomically approves the selected NETZ draft power, start-delay and duration using the existing durable approval contract, then sets internal release true and requests the applicable positive point **when authorized and eligible**. If persistence/validation fails, keep release false, do not send positive current and display the error. No separate SET press. |
| WBM-NETZ-ACTION-03 | While release=true and **no meaningful unapproved NETZ parameters** exist, show **Ladung beenden** (Stop charging). Press sets release false, cancels outstanding positive/retry intents, requests 0 A when permitted and shows actual vs requested until confirmed. (The earlier phrase `Ladung stoppen` denotes the same action; use consistent visible text `Ladung beenden`.) |
| WBM-NETZ-ACTION-04 | While release=true and a meaningful power or timing draft differs from the currently approved request, show **Ladeparameter übernehmen** (Apply charging parameters) **instead of** Stop charging. Press atomically approves the draft and updates the currently authorized desired point subject to lockouts. It **does not** set release false. After approval, button returns to **Ladung beenden**. |
| WBM-NETZ-ACTION-05 | While **Ladeparameter übernehmen** is shown, stopping by this same button requires **two intentional presses**: first apply the pending parameters, then press the restored **Ladung beenden**. Alternatively revert drafts to equivalence so **Ladung beenden** reappears. Do not add another stop button to bypass this intended workflow. |
| WBM-NETZ-ACTION-06 | The pending-change predicate must be **backend authoritative**, using existing approved-versus-draft target equivalence (`WBM-GRID-24/25`), including timing fields. Numerically different power that resolves to the same legal phase/current and no timing changes does not unnecessarily become pending SET. Direct HA Number/Text edits and multiple cards obey the same action state. |
| WBM-NETZ-ACTION-07 | Only active enabled/supported phase modes with `allowed_current_Np >= MinimumCurrent > 0` contribute to the power selector's lower/upper bounds. A configured cap `0 A` removes that phase mode entirely; do not use it to calculate 0 W endpoints or invalid maxima. |
| WBM-NETZ-ACTION-08 | Calculate **technical minimum selectable power** as the lowest positive `P_min(n)` across eligible modes, and **technical maximum selectable power** as the highest `P_max(n)` across them, using the existing consistent voltage basis, station minimum, station maximum/asymmetry maximum, current-step and per-mode WBM caps. Do not hardcode a 1p/3p pair, 6 A, 230 V, or thresholds; 2p applies only when actually supported. |
| WBM-NETZ-ACTION-09 | The NETZ power editor shall no longer allow values below this dynamic **positive** minimum or above the dynamic maximum. Clamp or reject invalid legacy/draft values predictably while preserving migration diagnostics; no silent positive current issuance. If there is **no eligible positive phase**, disable positive power choice and **Ladung starten**, and explain why. Explicit stop/0 A is controlled by release OFF rather than by a 0 W slider value. |
| WBM-NETZ-ACTION-10 | Continue the existing DOWN/NEAREST/UP **legal-current quantization** and overlap/phase-retention rules; never force a positive current solely because the selector displays a positive power. A currently selected value may still quantize to 0 A under applicable rules, leaving **Ladung starten** disabled. Show requested vs executable power transparently. |
| WBM-NETZ-ACTION-11 | On approval while running, re-evaluate latest draft and physical station caps/phase identity with the current generation. Preserve valid 1p/3p transition and phase/re-enable lockout retry behavior. An OCPP rejection or lockout must not report new parameters as applied. |
| WBM-NETZ-ACTION-12 | Offline/Local/no transaction: drafts may be configured without changing hardware. The start action must not perform an unauthorized profile command. A disabled button or explicit blocked result must distinguish bad power choice from unavailable control; do not silently take control. |
| WBM-NETZ-ACTION-13 | State changes and button text must be driven by the **manager release** and approved-vs-draft values, not exclusively by measured energy/current (which can remain 0 while release is true). Persist revisions and user intent so a UI reload never invents a running charge or silently starts one. |

### UI behavior examples

| WBM release | Draft vs approved | Legal positive point | Button text | Action |
| --- | --- | --- | --- | --- |
| OFF | any | no | **Ladung starten** (disabled) | none |
| OFF | any | yes | **Ladung starten** (when eligible) | approve draft + release ON/start |
| ON | equivalent / no pending change | n/a | **Ladung beenden** | release OFF + zero request |
| ON | meaningful pending change | n/a | **Ladeparameter übernehmen** | approve and apply; release stays ON |

## Future PV profile behavior (WBM-PV-RELEASE-01–07)

**Specification only. None of the PV profiles is implemented in the current active-control scope.** PV V3 remains a separate DRAFT and is not silently approved by this button design.

| ID | Requirement |
| --- | --- |
| WBM-PV-RELEASE-01 | The Lovelace button for PV_SURPLUS, PV_OPTIMUM and PV_MAXIMUM has only **Ladung starten** when release=false, and **Ladung beenden** when release=true. No **Ladeparameter übernehmen** or separate SET state in PV. |
| WBM-PV-RELEASE-02 | **Ladung starten remains enabled even when the PV calculation currently yields 0 W / 0 A.** The action grants manager-level authorization for a future positive PV target; it does not immediately force a minimum charging current. |
| WBM-PV-RELEASE-03 | While release=true, a zero PV target is a normal **0 A pause with release still true**, not an operator OFF. When PV later requests positive current, WBM may issue it without a second user press, subject to authority/transaction/hardware permission and the PV policy. |
| WBM-PV-RELEASE-04 | Pressing **Ladung beenden** sets release=false, sends 0 A where authorized, and blocks later positive automatic PV updates until the operator explicitly starts again. |
| WBM-PV-RELEASE-05 | Every actual profile switch sets release=false, even PV↔PV, PV→NETZ and NETZ→PV. Merely changing PV tuning inputs or a computed target does not reset release. |
| WBM-PV-RELEASE-06 | Preserve the separately approved PV SoC/economic-stop, lockout, battery-policy and OCPP transaction behavior unless directly overridden here. Economic 0 A, operator release OFF, and physical charging stopped remain distinct causes. |
| WBM-PV-RELEASE-07 | Do not implement PV regulators in the present requirements-only task. Future PV implementation must integrate the shared manager release gate at one canonical dispatch point rather than duplicating gates across profiles. |

## Migration, acceptance and exclusions

- The existing `button.grid_set` and backend approval contract must be retired/repurposed coherently with safe HA entity migration; do not leave an independent SET action that can bypass the new button.
- Remove Lovelace invocation of `switch.charging_enabled` while retaining its HA device/integration page presence, actual station state and readback. Consider consistent translations so a hardware switch cannot be confused with WBM Ladefreigabe.
- Existing grid scheduling provisions whose expiration says `ChargingEnabled=false` must be adapted: **operator/profile scheduling end sets manager release false and requests 0 A**, not hardware disable, unless the user separately chooses hardware disable.
- Explicitly remove `AutoWakeUpEnabled` from WBM control inventory, state publication and entity surfaces once the coordinated Stationary wire removal occurs; tolerate protocol migration deterministically rather than exposing a dead switch.
- Verify Local/Remote hardware enable in both configurations, station hardware bit Actual and notifications; verify Manager OFF while Remote uses 0 A, not bit 14; profile switching while positive, pending changes during running, 2-click stop behavior, no eligible modes, positive-min/max range, DOWN-to-zero, HA persistence, multiple cards, restart/reconnect and late ACK fencing.
- No new OCPP component/variable for manager release, no WBM-owned physical CP logic, no automatic authority takeover.
- The feature branches implement this contract; this does not claim deployment or integration into `develop`.
