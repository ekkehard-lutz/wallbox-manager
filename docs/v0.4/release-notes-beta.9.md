# v0.4.0-beta.9

This prerelease integrates the independently reviewed automatic vehicle wake-up
feature. WBM retains the complete reviewed history through non-fast-forward merge
`e4745782ea591a9a6a1a694b9ccc628ddf89579a`, from develop `542bf9628af646bc1b8e766a73859cbbca9575a0`,
including feature `20e804d5db72e81cfdb60eeab26aafe963ea07ef`.
The coordinated Stationary develop merge is `d5bb336e3d53165c76ecf8798239b31a9c75f3aa`,
retaining feature `f1f59510ab78eae3759eacce0793a4fbef0d860b` from
`f423b59802fe411b80372d6c0c7befc57219b312`.
Publication installs neither repository.

## Persistent automatic wake-up switch

- Adds **Automatic vehicle wake-up / Automatisches Aufwecken** on the existing
  Home Assistant device/integration page, outside the custom Lovelace card.
  Removes the manual **Wake up vehicle / Fahrzeug aufwecken** HA button and its
  WBM command path. Stationary retains the legacy manual DataTransfer extension
  for compatibility.
- The switch reads and changes the station-scoped persistent OCPP 2.1 Device Model
  Actual boolean `WallboxController.AutoWakeUpEnabled`. Its new stable switch ID
  belongs to the existing wallbox device. Only confirmed station observations set
  state; an acknowledgement alone does not. Control requires a connected,
  registered station, Remote authority and the uniquely advertised writable
  endpoint. Unsupported/unknown settings remain unavailable.
- OFF is the default **only when no persisted value exists at first commissioning**.
  Stationary durably saves every confirmed ON/OFF change before acknowledging it;
  persistence failure rejects SetVariables. The last confirmed setting survives
  service restarts, Raspberry Pi reboots, Home Assistant restarts, OCPP reconnects
  and Local/Remote changes. WBM reads the actual station setting on bootstrap and
  reconnect, never overwriting it with a local default.

## Station-owned detection and CP operation

With the matching Stationary implementation separately installed, enabled and
operating under registered Remote authority, the existing EVSE service detects a
genuine actual configured-current transition from 0 A to at least the configured
minimum (normally 6 A), in the same physically connected vehicle session.

Stationary observes **EVSE register 1002**: value **2 (CP B)** means connected
without a charging request; **3 (C)** or **4 (ventilation)** cancels detection.
If CP B persists for the configured monotonic delay, Stationary revalidates fresh
direct EVSE evidence, current, authority, enable state, session, command ownership,
phase/fault conditions and unambiguous GPIO feedback before one CP interruption.
Output register 1006 is not an independent charging-request signal; recognized
value 3 does not disqualify CP B.

Startup configuration remains local to Stationary:

```ini
[wallbox]
auto_wakeup_delay = 30
wakeup_duration = 5
```

The default detection delay is **30 seconds**, configurable as an integer 1–3600.
The independent CP hold defaults to **5 seconds**, range 1–300. These settings do
not replace or reset the persisted enable switch.

There is **one attempt per qualifying resume episode**, including a fully charged
or nonresponsive vehicle. Busy, rejected, failed or completed attempts do not retry
without a later genuine 0 A-to-positive transition. OFF, zero current, C/ventilation,
disconnection, authority loss, shutdown, phase work or invalid evidence cancels
pending detection. Enabling, restoring ON at startup or reconnecting never
retroactively arms an existing positive-current episode or replays an attempt.

There is **no SDM630 wake-up dependency**: no mandatory meter sample, current/power
threshold or extra meter polling. Stationary retains ownership of physical CP
safety, exclusive command serialization, feedback, bounded restoration and cleanup.
WBM adds no wake-up timer, vehicle-monitoring loop, retry or polling service.
A confirmed CP restoration establishes only the CP operation, **not a successfully
awakened vehicle or charging**.

## Preserved behavior and specification status

Take Control and ChargingEnabled behavior, existing entity identities, authority
policy, permission readback, sessions, transactions and energy/statistics remain
unchanged. The existing card still hides Take Control only for connected, available
observed Remote authority; returning to Local shows it again. ChargingEnabled stays
visible. Wake-up does not issue a current, contactor, phase or permission command.
NETZ/PV regulation and WBM current/phase/profile execution remain outside this release.

Both repositories' normative **AUTO-WAKE-01–10** describe the approved feature and
its persistent setting. **WBM-WAKE-FUTURE-01 is superseded**, with no outstanding
automatic-wake-up feature placeholder. Optional means operator-selectable, not a
pending implementation approval. The broader PV V3 draft remains unapproved.

See [approved requirements](../specifications/automatic-vehicle-wakeup.md) and
[implementation assessment](../automatic-vehicle-wakeup-assessment.md). Earlier
beta.8 release notes and feature assessments describe their historical milestone.

## Validation and acceptance boundary

Release gates: complete Python/frontend suites, Ruff lint and formatting,
JavaScript syntax, Python compilation, manifest/version and English/German
translation consistency, exact tracked-source/HACS package-content and Git diff
checks. CI must pass on the exact final develop commit and annotated tag before
publication. No executable code changes accompany release preparation.

Local results: WBM **217 Python tests and 17 frontend tests passed**, with five
existing dependency deprecation warnings; lint/format, syntax, compilation,
translations, manifest and package-content checks pass. Stationary **723 tests and
610 subtests passed**. Its existing lint baseline changes from 143 to 142 findings,
with no new findings and the same 77 files needing formatting.

Sleeping-vehicle behavior is accepted through fake EVSE/GPIO and monotonic-clock
simulation because no suitable sleeping EV is available. **Physical CP restoration
and installed hardware integration acceptance remain outstanding**. Simulation
and source review do not prove actual vehicle response or live HA CPU acceptance.
Unconfirmed CP restoration requires operator attention. Existing bootstrap telemetry
limitations remain.

## Publication and deployment boundary

Manifest version: **0.4.0-beta.9**. Dependencies and config-entry/storage versions
are unchanged. HACS consumes the annotated tag's tracked GitHub source archive;
no separate ZIP asset is required. Local investigation files are excluded.

The release is **Prerelease: true**, **Latest: false**, **Draft: false**.
The operator will separately deploy the approved Stationary commit and install
WBM beta.9 through HACS. This task performs no wallbox checkout/pull, installation,
Home Assistant change/restart, production service restart or live OCPP command.
Neither repository's `main` is modified; Stationary receives no release.
