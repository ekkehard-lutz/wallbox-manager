# Automatic vehicle wake-up implementation assessment

22 October 2026. AUTO-WAKE-01–10 and the subsequent persistence correction are
approved for implementation. Normative specifications were updated before their
corresponding production changes. Implementation, independent review, integration,
release and deployment remain separate; this branch authorizes none of the latter.

## Ownership and evidence

Stationary owns the existing EVSE-service observation cadence, monotonic delay,
same-session episode identity and shared serialized CP interruption/restoration.
Only a confirmed actual 0 A-to-positive transition under enabled, registered Remote
authority can arm. CP B must remain observed until expiry; C/ventilation, zero,
disconnect, OFF, authority loss, phase work, invalid evidence and shutdown cancel.
Busy/rejected/failed/completed attempts consume the episode, with no retry until a
later genuine zero-to-positive transition. Full batteries intentionally receive at
most one attempt. ON, restored ON and reconnect never arm an existing episode.

`auto_wakeup_delay` defaults 30 seconds, strict integer 1–3600. Independent
`wakeup_duration` defaults 5 seconds, 1–300. A direct EVSE read must complete within
two seconds; gaps over five seconds in ordinary observations cancel detection.
These local execution limits introduce no WBM observation TTL or extra polling.
Acquisition contexts prevent a zero sample begun before enabling/reconnection from
arming afterward. Confirmed physical connection edges provide session generations.

Shared preflight requires fresh direct 1002 = 2, appropriate configured 1000
(zero for legacy manual, at least minimum for automatic), emergency-free evidence,
no phase/command conflict and active/unblocked GPIO. Recognized 1006 values 1/2/3
are accepted; it is output status, not an independent vehicle-request signal.
SDM630 is neither read nor required by wake-up. Phase-switch meter checks remain.
The existing owned service executes automatic pulses with cooperative waits;
no second poller, timer thread or scheduler is introduced. EVSE-service sampling
pauses during its bounded pulse; observation fences preserve connection/session
identity. OFF, disconnect, authority loss and shutdown can cancel the hold and
await confirmed release. No current, permission, contactor or phase command is
introduced by wake-up. Diagnostics log state changes, without per-poll timestamps.

## Persistent station setting

`WallboxController.AutoWakeUpEnabled` Actual is persistent boolean ReadWrite.
Missing file/key alone yields first-commissioning false. The EVSE service restores
its existing station JSON and installs a callback to its atomic replacement path:
flush, file fsync, replace, directory fsync. Every accepted setting write completes
that path before publishing the new confirmed value. Failed persistence rejects
SetVariables and retains the last confirmed in-memory value. Corrupt/unreadable or
wrongly typed saved configuration fails restoration rather than inventing OFF.
Before restoration, reads are unknown and no initial false notification is sent.

A separate fail-fast configuration lock serializes durable setting writes while
allowing OFF during a physical pulse. The state lock is released before acquiring
the existing persisted-data lock, avoiding inversion with the EVSE service.
Local/Remote, service/Pi/HA restart and reconnect preserve the flag; only ephemeral
detection state is discarded. GetVariables/inventory and the existing initial/
change-only notification loop expose the actual setting. WBM bootstrap/reconnect
only reads it; no local default write or automatic command replay exists.

## WBM UI and compatibility

The station-bound switch uses a new stable unique ID and English/German names on
the existing HA device page. Only observations set state; an ACK alone does not.
Unknown/disconnected/non-Remote/unsupported endpoints disable control through the
existing authorization, intent, dispatch and connection/boot fences. Authority
loss supersedes pending confirmation. Registry cleanup removes only this entry's
exact old manual button identity, preserving foreign entries.

WBM's manual button, translation and DataTransfer execution/long-timeout path are
removed. Arbitrary DataTransfer and SetVariables remain blocked. Stationary keeps
the legacy manual 0 A extension using the shared physical operation. Take Control,
ChargingEnabled and session/transaction/energy handling remain intact. Lovelace
adds no wake-up control and retains its existing Take Control visibility fix.

## Source comparison

Inspected actual openWB core revision
`849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
`packages/control/chargepoint/chargepoint.py`,
`initiate_control_pilot_interruption` (lines 278–321): zero-to-positive detection,
phase exclusion, hardware delegation and configurable repeated stuck-vehicle
retries. The approved Stationary contract uses direct CP B and one attempt, so
openWB's repeated retry policy and thread architecture were deliberately not copied.

Inspected WBM `v0.3.2-beta.18`, revision
`4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`,
`custom_components/wallbox_manager/control/commands.py`: explicit command outcomes
and separation from physical observation remain relevant. No old runtime or
regulator was restored.

## Validation boundary

Fake EVSE/GPIO and monotonic clocks establish the sleeping-vehicle acceptance
behavior; no suitable sleeping EV is available. They cannot establish real CP
relay restoration or vehicle response. Confirmed CP restoration does not prove
successful wake-up or charging. Physical restoration and integration testing need
a separate authorized hardware task. No hardware, service, Home Assistant system,
HACS installation or release is modified by this work.

WBM base: develop `542bf9628af646bc1b8e766a73859cbbca9575a0`.
Branch: `feature/automatic-vehicle-wakeup`; manifest remains `0.4.0-beta.8`.
This feature is unreleased. The previous manual assessment and beta.8 release notes
are historical descriptions of the released behavior.

Verification: complete Python suite **217 passed** (five existing dependency
warnings), frontend **17 passed**, full Ruff lint and format clean, JavaScript
syntax and Git diff whitespace clean. Regression coverage includes confirmed-state
readback versus ACK, rejected persistence writes, bootstrap/reconnect/HA reload of
ON without writes, authority changes, stale pending requests, entry isolation,
registry cleanup and exclusion of both wake-up controls from Lovelace.
