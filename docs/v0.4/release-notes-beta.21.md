# Wallbox Manager v0.4.0-beta.21

Beta.21 publishes the independently reviewed safe-action/state/preview feature
and status-power follow-up unchanged. Approved final feature:
`a024c8ebfa6ae76a9e5d3ac993bdb75baafe8414`, including predecessor
`e61a16d4c4c3fe671a2ccb2086c48c0b76979219`.
Manifest: `0.4.0-beta.21`; annotated tag: `v0.4.0-beta.21`.
HACS uses the standard tagged GitHub source archive; no binary asset is required.

## Explicit safe charging actions

The single card button keeps Start / Stop / Apply, but now sends explicit intent
through the connector entity service `wallbox_manager.charge_action`.
Backend validation uses expected profile, runtime incarnation and action revision,
with persistence and lifecycle fencing. A stale Start cannot become Stop, a stale
Stop cannot become Start, and Apply cannot silently approve a superseded draft
or schedule. Concurrent cards and delayed callbacks share authoritative state.
Stop supersedes a slow Start/Apply without waiting behind its execution queue.

The existing `button.charge_action` entity identity remains. **Generic
`button.press` retains legacy state-dependent toggle semantics.** Integrations
and automations needing direction-safe behavior must use the new explicit service
with current button attributes. Required data: target `entity_id`, `action`
(`start`, `stop`, `apply`), expected `profile`, `revision` from
`charge_action_revision`, and `incarnation` from `runtime_incarnation`.
See the [service architecture and compatibility contract](../charge-action-state-reserve-preview-review.md#explicit-entity-service-api-and-compatibility).

## Preparatory PV release and prompt publication

PV_SURPLUS, PV_OPTIMUM and PV_MAXIMUM may be authorized before a vehicle is
attached or a transaction exists. This is manager intent, not physical charging
or an authority grant. Local/offline/hardware-disabled restrictions and every
transaction-bound electrical dispatch check remain in force. Later connection
uses the ordinary clocked PV policy; no extra regulator pass is introduced.

Accepted, durably persisted PV release is acknowledged as success independently
of an older rejected/communication-failure/unsupported electrical result. Start
need not issue or await an immediate OCPP point or positive current. Actual NETZ
execution failures, validation/storage failures and superseded contexts retain
their existing errors. Requested, accepted, applied and measured power remain
separate.

Release state publishes promptly even when the PV controller correctly remains
at SoC STOP / 0 A. The card therefore shows Stop after accepted Start rather than
waiting for current, forecast or incidental Grid changes. A computed zero-current
pause does not revoke manager authorization.

## Canonical target and planned NETZ reserve

The card's PV target follows the latest canonical backend value through STOP,
release OFF, unplugged operation and valid passive planning. The existing normal
**PV Target SoC / PV Ziel-SoC** sensor retains its unique identity, full precision,
availability and change-driven Recorder behavior. There is no JavaScript planner.
Unchanged cycles do not produce redundant target, Select or action states.

NETZ's home-battery pair now displays **actual SoC → planned effective reserve**,
including before charging starts. It reuses the physical reserve calculation,
Number step/range behavior and durable original-reserve ownership. For original
20%, configured reserve 40% and SoC 65%, the pair is `65.0 % → 40.0 %` even while
the observed Fronius reserve remains 20%. Observed reserve is separately identified
in tooltip and accessibility text. Invalid/pending/recovering ownership inputs
show unavailable preview rather than an invented target.

Preview is read-only: it does not write Fronius, create/modify its journal, take
ownership, start charging or affect OCPP. Actual override still requires existing
confirmed active NETZ charging prerequisites; readback and restoration are intact.
Battery observations do not become NETZ electrical prerequisites.

## Approved Lovelace status layout and power

The existing reviewed implementation is published without additional CSS,
typography or layout changes:

- **Wallbox Status / Wallbox status** upper left: connection, session energy,
  duration; upper right: charging state, **EV Leistung / EV power**, wallbox parameters.
- Lower left: **Hausspeicher / Home battery**, small **Ladung Ist → Soll /
  Charge actual → target**, then the SoC/target pair.
- Lower right: PV storage charging/discharging power, or NETZ grid import/export
  power, from the existing mapped HA entities. Messages remain below.

Charging and grid export use semantic green; discharging and grid import use
semantic red. Both-zero, unavailable/invalid or simultaneously positive sources
show neutral `—`, with an explanatory accessible tooltip and no guessed net flow.
Power uses **kW with exactly two locale-aware decimal places**, as explicitly
approved. Battery percentages retain one decimal. Direction is also expressed in
German/English text; color is not the only cue. Narrow layouts stack the lower
blocks without horizontal overflow.

Only source entity mappings are projected on the existing profile Select. Power
updates render directly from HA state, without a new public power sensor, polling,
periodic frontend timer, control loop or WBM Recorder stream.

## Unchanged control and compatibility

FAST/BALANCE algorithms, PV_OPTIMUM formula, SoC hysteresis/comparisons, economic
STOP/restart, seven tuning defaults, regulation cadence, point solver, physical
phase/current limits, Stationary protocol and hardware protection remain unchanged
from beta.20. NETZ timing, durable reserve ownership/restoration, existing battery
mappings, HA entity identities, settings, dependencies and persistence schemas
are preserved. No Stationary/Fronius update or live setting change accompanies
this release. Software success does not establish physical acceptance.

## HACS upgrade and rollback

These are instructions for a **separately authorized operator action**; publication
performs none of them. For an authorized upgrade from beta.20, select prerelease
**v0.4.0-beta.21** through the normal HACS process. Keep manager release OFF, take
the normal full pre-upgrade HA backup, retain settings/source mappings/journals,
and verify the installed manifest after the operator-controlled restart.

**The existing safety policy remains: an HA integration reload/restart does not
silently resume previously authorized charging. Manager release resets OFF; the
operator must explicitly authorize it again if charging is desired.**

Existing cards retain their resource path and entity identities. The operator
should verify each browser loads the new resource rather than an older cached
card; frontend resource/cache handling is outside GitHub publication. Safe
external automations should migrate from ambiguous `button.press` to the explicit
service. Missing power mappings show neutral display and do not disable NETZ.
No persistence migration or new mandatory measurement mapping is introduced.

A separately controlled package-only rollback to beta.20 restores the former card,
button interpretation and observed-only reserve display. It also removes the new
explicit service: automations using it must be adapted before rollback. Do not
assume the direction-safe action and target-publication corrections remain in the older package. Preserve
Recorder history, registry identities, profile settings, reserve journal and
PV economic STOP/Wh provenance; never delete journals or replay saved positive
intent to make a downgrade appear successful. Earlier V3 downgrades retain the
[beta.17 migration precautions](release-notes-beta.17.md#hacs-upgrade-and-rollback).
[Beta.20 notes](release-notes-beta.20.md) remain an immutable historical reference.

Installation, HA/HACS operations, integration reload/restart, profile/release/
authority changes, OCPP hardware commands and Stationary/Fronius/inverter/battery
operations are not part of publication. The operator installs and validates
hardware separately.

## Evidence

- [Release validation](release-validation-beta.21.md)
- [Safe-action, publication and reserve-preview implementation](../charge-action-state-reserve-preview-review.md)
- [PV Start acknowledgement and status-power follow-up](../charge-action-status-power-followup-review.md)
- [Charge-release and safe-action specification](../specifications/charge-release.md)
- [Home-battery, layout and power specification](../specifications/lovelace-home-battery-display.md)
- [PV target sensor specification](../specifications/pv-target-soc-sensor.md)
- [Specification index and publication approval](../specifications/README.md)
