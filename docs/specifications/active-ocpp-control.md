# Active OCPP control foundation

Status: implementation scope explicitly requested on 21 October 2026. Successor
milestone to passive v0.4, subject to the independently approved
[WBM-ARCH-01–12](architecture-responsibility.md). This does not approve the PV V3
draft or authorize a merge, release, deployment or hardware command.

## Precedence and scope

For this successor only, WBM-CTRL-01–12 replace V04-03's blanket prohibition and
V04-06's disabled takeover/permission actions with takeover and permission control.
AUTO-WAKE-01–10 additionally approve the persistent station enable switch.
V04-07 remains binding for the **currently implemented active-control foundation**: profile editing is local configuration, with no execution. The separately approved [WBM-GRID-01–16](grid-profile.md) specify the *next NETZ implementation target*, not behavior already implemented.
V04-01/02/04/05/08/09/11/12 remain applicable. The historical approved
[passive milestone](passive-v0.4.md) is unchanged. Architecture wording that
“Passive v0.4 still sends no physical control” describes that milestone, not a
permanent prohibition for this explicitly authorized successor. Individually
approved permission independence (PV-OCPP-04), generation handling (PV-OCPP-06)
and station ownership remain applicable. No PV/economic algorithm is implemented.

## Requirements

| ID | Binding requirement |
| --- | --- |
| WBM-CTRL-01 | Each entry owns one station's intent and command outcomes. The existing State is the sole authority/permission observation store. The station Domain alone owns physical/electrical protection, CP, phase and current safety, interlocks and execution. |
| WBM-CTRL-02 | Only explicit user action initiates takeover: one station-scoped `SetVariables`, component exactly `WallboxController`, variable exactly `ControlAuthority`, `Actual=OCPP`. WBM never writes `Local`. Returning to Local is a station operation. |
| WBM-CTRL-03 | Permission is the independent station-scoped `WallboxController.ChargingEnabled`, `Actual=true/false`. Require current-generation observed Remote authority and a uniquely advertised Actual ReadWrite endpoint. No vehicle, transaction, profile, current or phase prerequisite. OFF is permission withdrawal, not economic zero-current pause. Retained positive current can become effective when enabled and a vehicle connects. |
| WBM-CTRL-04 | Require correct bound station, live registered connection and exact unique writable inventory endpoint. Inventory values do not establish actual authority or permission. Read-only/ambiguous/unknown endpoints never authorize writes. |
| WBM-CTRL-05 | Use the existing serialized OCPP call transport. At wire dispatch, validate current adapter/connection/boot, latest intent, authority revision for permission and the exact single permitted SetVariables item. All other physical operations, including arbitrary SetVariables, remain explicitly blocked. |
| WBM-CTRL-06 | Validate CALL identity through the OCPP transport and exactly one matching Actual result row. Accepted is acknowledgement only. Confirmation needs a valid canonical supported GetVariables observation or authoritative correctly scoped notification acquired after dispatch. Requested, acknowledged and observed values remain distinct. |
| WBM-CTRL-07 | Expose pending, accepted_unconfirmed, confirmed, rejected, unsupported, timeout, communication_failure and superseded outcomes plus reason and requested value. Preserve observed values during pending, rejected, failed and unknown readbacks. Accepted writes with unsuccessful readback remain accepted_unconfirmed, with a readback failure reason when applicable. A command outcome records that operation; a previously confirmed takeover does not assert continuing Remote ownership after a later Local observation or reconnect. Busy can be conveyed as protocol error/rejection; do not infer a physical state from it. |
| WBM-CTRL-08 | Fence responses/readbacks by intent identity and connection/boot generation; permission also fences authority transitions. Newer intent replaces older pending intent; an already sent opposite write may require the newer explicit corrective write even if old observations still equal the newer request. Obsolete queued writes never dispatch. |
| WBM-CTRL-09 | Coalesce identical concurrent requests without additional writes. For a current-generation accepted_unconfirmed takeover, each subsequent explicit Take Control action performs one supported authority GetVariables confirmation read, retaining the same intent and sending no SetVariables; concurrent recovery callers share the same read. Invalid/failed recovery reads preserve observations and the unconfirmed outcome. Permission duplicates retain their existing behavior. Skip already observed identical requests when there is no unresolved opposite write. Never retry automatically or replay on startup, restoration, registration, boot or reconnect. |
| WBM-CTRL-10 | Teardown owns, cancels and joins in-flight calls through the existing adapter task set. Caller cancellation does not release the serial response consumer or orphan ownership. A boot supersedes pending intent without replay. |
| WBM-CTRL-11 | Keep the original HA entity unique IDs, registry/device mappings and observed permission state. Publish only affected entity keys. Expose execution readiness/block reason and command diagnostics on existing controls; label the last command result separately from independently observed Local/Remote authority and current execution availability, including authority loss and reconnect. A historical confirmed result never replaces current authority; offline authority is unavailable, and superseded unconfirmed commands remain explicitly superseded. In the bound card, hide Take Control only while connected with available observed Remote authority; show it again on observed Local, including external return. Missing/unknown/unavailable authority or disconnection keeps it visible and disabled, independently of command history. ChargingEnabled remains visible and the HA Take Control entity/service/identity remain unchanged. Use existing reactive HA updates and hidden CSS only. No public per-read or command timestamp and no optimistic switch state. |
| WBM-CTRL-12 | Reuse bootstrap reads, canonical notification handling and the existing five-second permission readback. No additional periodic task, polling loop, observation TTL, safety manager, command scheduler or restored historical runtime. No operating-point/profile/current/phase/PV/grid-regulation commands in this implemented foundation; [WBM-GRID-01–16](grid-profile.md) separately define the NETZ successor. AUTO-WAKE-01–10 authorize only the additional AutoWakeUpEnabled boolean control, without WBM wake-up monitoring or timing. |

[AUTO-WAKE-01–10](automatic-vehicle-wakeup.md) extend these guarded controls with
AutoWakeUpEnabled. WBM supplies only a station-confirmed switch. The former manual
HA button/dispatch and delayed-detection proposal are superseded. Stationary keeps
its legacy DataTransfer compatibility; takeover/permission/card behavior remains.

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
