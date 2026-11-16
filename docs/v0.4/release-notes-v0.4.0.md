# Wallbox Manager v0.4.0

Wallbox Manager v0.4.0 is the stable successor to v0.3.1. It packages the
implementation accepted in v0.4.0-beta.24 with stable version metadata.
[Stable release validation](release-validation-v0.4.0.md)

## Changes since v0.3.1

- **OCPP 2.1 and Stationary.** The Home Assistant integration handles Stationary
  capability discovery, state and transaction events, and scoped control
  proposals over WebSocket. Stationary owns electrical protection, current and
  phase limits, interlocks and physical phase switching. Deploy the OCPP
  listener on a trusted private LAN with authenticated VPN access as needed.
  [Architecture and network boundary](../specifications/architecture-responsibility.md)
- **Operator-controlled authority and charging authorization.** Take Control
  requests Remote authority explicitly. Hardware permission and WBM profile
  release are independent. Start or Apply requires the applicable permission
  and authority, and Stop can retire an active release while dispatch is
  unavailable. Local authority permits NETZ draft editing.
  [Charge release requirements](../specifications/charge-release.md)
- **NETZ fixed-power charging and scheduling.** NETZ chooses the nearest
  legal nominal operating point using 230 V per phase, observed station
  capabilities and configured current caps. It retains the confirmed phase
  within its legal range. A 0 A cap excludes that phase mode. Start and Apply
  approve fixed power and optional delay and duration. The legacy configurable
  approximation Select has been retired.
  [NETZ profile](../specifications/grid-profile.md)
- **Recovery and lockouts.** Home Assistant Core restart can restore an
  eligible approved NETZ schedule from its original absolute timestamps
  after Stationary evidence returns. WBM can reconcile elapsed deadlines
  after it reconnects. Deadline enforcement by WBM requires Home Assistant
  to be running. Stationary reconnection rechecks the requested operating
  point and reconciles it as needed. Recognized Stationary lockouts can
  defer eligible starts and phase changes until automatic continuation.
  [Timing requirements](../specifications/operational-display-and-timing.md)
- **Fronius battery reserve.** An authorized NETZ session can temporarily
  raise the configured writable reserve, limited by observed battery SoC and
  the control's quantization. An owned setting survives incomplete point
  confirmation and can be restored when the release retires. The
  implementation gives external reserve changes precedence.
  [NETZ battery reserve](../specifications/grid-profile.md)
- **PV profiles.** PV_SURPLUS uses measured surplus or a configured battery
  target. PV_OPTIMUM plans a battery target from forecast energy, household
  demand, capacity and reserve. PV_MAXIMUM uses the lowest valid target
  allowed by reserve and hysteresis. Battery profiles share SoC hysteresis,
  FAST/BALANCE regulation and technical operating points. PV planning can
  continue with release off, and a computed zero can pause charging while
  preserving release. The existing economic import budget and per-transaction
  accounting remain part of the PV policy.
  [Current requirements](../specifications/v0.4-current-requirements.md)
- **Session metering and Lovelace.** The entry-scoped card shows session
  energy and elapsed connected-session duration, requested and confirmed
  current and phase, warnings, NETZ delay and duration, and the relevant
  battery reserve or PV target. It formats duration using the Home Assistant
  entity's reported unit and presents actions from the current backend state.
  [Integration and card setup](../../README.md#lovelace-and-home-assistant-entities)

## Field evidence and boundaries

The operator accepted the beta.24 implementation with Home Assistant Core
2026.10.3, HAOS 18.4, Wallbox Stationary and a BYD Seal U. Both station-side
phase modes were capped at 16 A. The tested scenarios passed for connected
session duration (F01), operating-point recovery after Stationary restarts
(F02a), Fronius reserve acquisition, retention and restoration (F02b),
Local authority UI and legacy button restriction (F03), Home Assistant
restart and NETZ scheduling (FR-01), and fixed steps, physical phase
switching and lockout continuation (FR-02).
[Beta.24 field acceptance](field-acceptance.md)

During a complete EVSE and Raspberry Pi power cycle, the EVSE boot current
temporarily allowed approximately 11 kW before WBM restored the approved
operating point. The physical sequence did not separately exercise external
edits to an owned Fronius reserve, standalone `wallbox_manager.charge_action`
rejection in Local or concurrent authority races. The 16 A caps yield
non-overlapping legal ranges. The operator reused prior PV evidence because
beta.24 left PV regulation unchanged. Evidence includes operator observations
and snapshots, with no complete raw OCPP command trace.
[Field observations and limitations](field-acceptance.md#operational-notes-and-remaining-limitations)

For an installation with unresolved journal loss or damage, review RR-01
before upgrading. Incomplete session history can limit replay protection.
[Session journal recovery policy](../specifications/v0.4-current-requirements.md#persistence-and-restart)

## HACS installation and rollback

1. Create a complete, restorable Home Assistant backup. Record the current
   integration and Stationary versions, entry and entity IDs, and preserve
   Wallbox Manager storage and any quarantine evidence.
2. Open Wallbox Manager in HACS. For a first install, add
   `ekkehard-lutz/wallbox-manager` as a **custom repository** of type
   **Integration** if needed. Select **v0.4.0** and download it. HACS uses the
   tagged GitHub source archive. Restart Home Assistant Core after installation,
   then reload the Lovelace frontend in the browser to pick up the card.
   [HACS custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/)
3. With charging idle for the initial check, verify the installed manifest
   reports **0.4.0**, Stationary reconnects and the expected entities and card
   are available. The obsolete NETZ approximation Select has been retired.
   Check observed authority and manager release before an operator-authorized
   charging session.
4. To roll back, restore the complete pre-upgrade backup. If HACS still
   offers the previous version, it can reinstall the package followed by a
   Home Assistant Core restart. Use the backup to recover prior storage and
   registry state.
