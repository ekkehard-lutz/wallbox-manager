# Active OCPP control foundation

WBM-CTRL-01–12 originate in the explicit 21 October 2026 active-control
decision. [WBM-ARCH-01–12](architecture-responsibility.md) govern their use.
The takeover and permission interface is implemented. Current/phase execution
belongs to [NETZ](grid-profile.md) and the individually approved
[PV clauses](pv-regulation.md). The 24 October
[WBM-RELEASE decision](charge-release.md) supersedes the former Remote-only
permission and AUTO-WAKE-01–10 WBM control clauses. PV-OCPP-04 and
PV-OCPP-06 retain the applicable permission and initialization boundaries.
V04-03, V04-06 and V04-07 describe the earlier
[passive milestone](passive-v0.4.md), whose V04-01 binding remains in force.

## Requirements

| ID | Binding requirement |
| --- | --- |
| WBM-CTRL-01 | Each entry owns one station's intent and command outcomes. The existing State is the sole authority/permission observation store. The station Domain alone owns physical/electrical protection, CP, phase and current safety, interlocks and execution. |
| WBM-CTRL-02 | Only explicit user action initiates takeover: one station-scoped `SetVariables`, component exactly `WallboxController`, variable exactly `ControlAuthority`, `Actual=OCPP`. WBM never writes `Local`. Returning to Local is a station operation. |
| WBM-CTRL-03 | Permission is the independent station-scoped `WallboxController.ChargingEnabled`, `Actual=true/false`. Require a uniquely advertised Actual ReadWrite endpoint in the registered generation. Permission is writable under either Local or Remote under WBM-RELEASE-06. No vehicle, transaction, profile, current or phase prerequisite. OFF is permission withdrawal, not economic zero-current pause. Retained positive current can become effective when enabled and a vehicle connects. |
| WBM-CTRL-04 | Require correct bound station, live registered connection and exact unique writable inventory endpoint. Inventory values do not establish actual authority or permission. Read-only/ambiguous/unknown endpoints never authorize writes. |
| WBM-CTRL-05 | Use the existing serialized OCPP call transport. At wire dispatch, validate current adapter/connection/boot, latest intent and the exact single permitted SetVariables item. Arbitrary writes remain blocked. Scoped operating-point proposals follow the separate profile contract. |
| WBM-CTRL-06 | Validate CALL identity through the OCPP transport and exactly one matching Actual result row. Accepted is acknowledgement only. Confirmation needs a valid canonical supported GetVariables observation or authoritative correctly scoped notification acquired after dispatch. Requested, acknowledged and observed values remain distinct. |
| WBM-CTRL-07 | Expose pending, accepted_unconfirmed, confirmed, rejected, unsupported, timeout, communication_failure and superseded outcomes plus reason and requested value. Preserve observed values during pending, rejected, failed and unknown readbacks. Accepted writes with unsuccessful readback remain accepted_unconfirmed, with a readback failure reason when applicable. A command outcome records that operation; a previously confirmed takeover does not assert continuing Remote ownership after a later Local observation or reconnect. Busy can be conveyed as protocol error/rejection; do not infer a physical state from it. |
| WBM-CTRL-08 | Fence responses/readbacks by intent identity and connection/boot generation. Newer intent replaces older pending intent; an already sent opposite write may require the newer explicit corrective write even if old observations still equal the newer request. Obsolete queued writes never dispatch. |
| WBM-CTRL-09 | Coalesce identical concurrent requests without additional writes. For a current-generation accepted_unconfirmed takeover, each subsequent explicit Take Control action performs one supported authority GetVariables confirmation read, retaining the same intent and sending no SetVariables; concurrent recovery callers share the same read. Invalid/failed recovery reads preserve observations and the unconfirmed outcome. Permission duplicates retain their existing behavior. Skip already observed identical requests when there is no unresolved opposite write. Never retry automatically or replay on startup, restoration, registration, boot or reconnect. |
| WBM-CTRL-10 | Teardown owns, cancels and joins in-flight calls through the existing adapter task set. Caller cancellation does not release the serial response consumer or orphan ownership. A boot supersedes pending intent without replay. |
| WBM-CTRL-11 | Keep the original HA entity unique IDs, registry/device mappings and observed permission state. Publish only affected entity keys. Expose execution readiness/block reason and command diagnostics on existing controls; label the last command result separately from independently observed Local/Remote authority and current execution availability, including authority loss and reconnect. A historical confirmed result never replaces current authority; offline authority is unavailable, and superseded unconfirmed commands remain explicitly superseded. In the bound card, hide Take Control only while connected with available observed Remote authority; show it again on observed Local, including external return. Missing/unknown/unavailable authority or disconnection keeps it visible and disabled, independently of command history. ChargingEnabled remains visible and the HA Take Control entity/service/identity remain unchanged. Use existing reactive HA updates and hidden CSS only. No public per-read or command timestamp and no optimistic switch state. |
| WBM-CTRL-12 | Reuse bootstrap reads, canonical notification handling and the existing five-second permission readback. No additional polling loop, observation TTL or safety manager. The original passive operating-point restriction and AutoWakeUpEnabled exception were superseded by the active profile and WBM-RELEASE-14 decisions. |

See the [current requirements baseline](v0.4-current-requirements.md) for supported HA controls.

## Acceptance and verification boundaries

`tests/test_active_control.py` uses real OCPP 2.1 schemas, frames and local
WebSockets, with a controllable station simulator. Cover successful, rejected,
unsupported, unconfirmed, timeout and obsolete outcomes; scope/response mismatch;
duplicate and opposing intents; failed initial authority readback and explicit readback-only recovery (including unchanged/invalid reads and concurrent callers); authority loss; reboot/disconnect; independent
entries; physical-command rejection and publication stability. Existing suites
retain observation, initialization, telemetry, session, migration, registry and
passive-performance coverage. Frontend tests cover exact scoped service actions,
blocked actions, failure/pending display and independently observed permission; reactive Local → Remote → Local takeover visibility, missing/unknown/unavailable/offline authority, command-history independence and entry isolation.

Automated acceptance does not establish physical switching success, station
firmware deployment, full bootstrap push coverage or live HA CPU acceptance.
Follow the separate [source assessment and hardware procedure](../active-ocpp-control-assessment.md).

## Approved PV phase-lockout interpretation — 7 November 2026

[PV-LOCK-01–08](pv-operating-point-response.md#approved-pv-phase-lockout-continuation--7-november-2026)
apply only to the explicit atomic Stationary `Rejected / PhaseSwitchLockout`
response. Prior positive confirmation remains usable only in its valid
scope and with consistent physical phase evidence. Other responses follow
the ordinary outcome and uncertainty paths. Stationary owns all physical
CP, relay and phase operations under WBM-ARCH-01–12.
