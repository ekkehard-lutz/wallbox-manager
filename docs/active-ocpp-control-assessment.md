# Active OCPP foundation: source assessment and hardware boundary

## Inspected revisions (21 October 2026)

- WBM base `origin/develop`: `8c4c031e01f0d0294db537bef8b3cb39a5c528c1`.
- WBM historical `v0.3.2-beta.18`: `4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`.
- [Wallbox Stationary main](https://github.com/ekkehard-lutz/wallbox-stationary/tree/2baf0daa8c446dad7570e5daa5f0f1cba486e915):
  `2baf0daa8c446dad7570e5daa5f0f1cba486e915`, freshly cloned default branch.
- [openWB/core default branch](https://github.com/openWB/core/tree/849340ec8d91f4e7cca08a7664cf9ec0187fb496):
  `849340ec8d91f4e7cca08a7664cf9ec0187fb496`, freshly cloned for source inspection.

The existing adjacent Stationary checkout is on a separate vehicle-wakeup feature
branch; it was inspected for context, left untouched, and was not substituted for
current main. No live station was contacted.

## Historical reuse decisions

| beta.18 files inspected | Decision for the small successor |
| --- | --- |
| `control/commands.py` | Adapt explicit outcome classification and separation of permission/authority from operating points. Do not restore generic protocol-neutral operation frameworks or solver dependencies. |
| `control/runtime.py`, especially `_permission`, `_take_control` | Adapt intent identity, authority revision and late-result fencing. Reject installation-wide ownership, multi-target runtime and takeover followed by automatic connector OFF; this milestone requires independent actions. Reject profile gating of general permission. |
| `protocols/ocpp/v21/authority.py` | Reuse exact station endpoint and one matching Actual response concept. Reject writing observed authority UNKNOWN on pending takeover and inventory-as-actual observations: WBM-ARCH-08 and current bootstrap semantics take precedence. |
| `protocols/ocpp/v21/adapter.py`, permission operation and dispatch guard | Adapt a ContextVar guard evaluated inside `_send` after the existing OCPP library serial lock. Retain unique writable endpoint and exact response checks. Reject physical-proof/age gates and current/phase/profile preparation. |
| `button.py`, `switch.py` | Preserve existing entity roles, scope/identity construction and observed permission projection. Replace the passive disabled service methods with explicit actions and keyed diagnostics. Do not restore public acquisition deadlines/timestamps or broad runtime subscriptions. |

## Current Stationary contract

Inspected `ocpp_runtime/device_model.py`, `station.py`, `control_dispatch.py` and
`authority_notifications.py` at the pinned main revision above.

The Device Model advertises station-wide Actual ReadWrite authority and permission.
Authority uses Local/OCPP. Permission uses true/false and `_read_confirmed_enabled`
returns only a confirmed bool; unavailable backend values produce Rejected reads.
`_set_enabled` accepts unchanged state as well as changed state; its callback owns
hardware execution and can reject. `station.py` dispatches Get/SetVariables through
`ControlDispatch`, which transfers synchronous work to the owning Domain hub or a
worker. Domain authority/readiness/command locking stays on the station. The
hard-wired authority observer sends the Remote-to-Local edge, not an initial or
positive Remote snapshot. Therefore WBM reads authority after explicit takeover;
permission keeps its existing five-second external-change detection. No new
notification contract or station modification is assumed.

## openWB comparison and applicability

Exact files inspected under the pinned revision:

- `packages/control/chargepoint/chargepoint.py`: `is_charging_possible`, manual
  lock, vehicle handling, `update`, `remember_previous_values`.
- `packages/control/chargepoint/chargepoint_data.py`, `chargepoint_state.py`,
  `chargepoint_all.py`, `chargepoint_state_update.py`, `control_parameter.py`.
- `packages/control/process.py`: `process_algorithm_results`, `_update_state_cp`,
  `_start_charging`.
- `packages/helpermodules/command.py`: `on_message`, `CompleteCommandContext`;
  `setdata.py`: `process_chargepoint_topic`, validation of manual lock and targets.
- `packages/helpermodules/utils/_thread_handler.py` and
  `packages/modules/common/abstract_chargepoint.py`.

| Topic / actual source behavior | Applicability to WBM |
| --- | --- |
| Policy computes required current in chargepoint `update`; manual lock/permission-like policy is separate; module `set_current` performs hardware access. | Keep the conceptual boundaries, but WBM's general permission must also work without a vehicle. Do not copy openWB's combined charging-possible gate, hardware checks, CP or phase implementation. |
| `ChargepointData.get` holds measured charge/plug/current values; `.set` holds targets and previous targets. `Process` differentiates measured charging from current released/waiting. | Keep requested, protocol-accepted and observed state distinct; a target or acknowledgement is never physical observation. WBM already has the canonical State, so no second copied snapshot store. |
| User manual-lock topics are validated as bool; explicit commands are processed by `Command.on_message` and completed through `CompleteCommandContext`. | Validate typed/scoped actions at the WBM command boundary. MQTT retained commands, topic interfaces and completion events do not map to OCPP confirmations. |
| `CompleteCommandContext` serializes command completion; named `thread_handler` refuses an already-running thread. | Reuse WBM's existing OCPP serial lock and coalesce identical intent. Do not add named threads, a global coordinator or a second scheduler. Station rejections remain normal. |
| Previous target fields record changes; `Process` still schedules `_start_charging` for each chargepoint each cycle. | Do not claim openWB globally suppresses duplicate hardware writes. WBM skips unchanged confirmed actions and repeated pending/unconfirmed identical requests; no cyclic command emission. |
| State update copies data behind events, and command MQTT reconnect subscribes to the command tree; these inspected files do not establish OCPP connection/boot fencing or safe command replay semantics. | No suitable OCPP reconnect algorithm to copy. Retain WBM generation reset, supported bootstrap and no replay, with independent per-entry transports. |

## Separate user-controlled hardware acceptance

No hardware commands, installation, restart, release or deployment were performed.
After independent review and separately authorized installation with a HA backup:

1. Record installed WBM/Stationary revisions and existing entity registry IDs and
   statistics. Start with station Local. Reload/reconnect: no SetVariables should
   appear; authority/permission initialize separately and ordinary observations hold.
2. Press the existing Take Control button once. Capture the exact station-scoped
   SetVariables/Accepted/GetVariables sequence and observed Remote. Confirm there
   is no permission, current, phase or profile command accompanying takeover.
3. With no vehicle/transaction, explicitly enable permission. Confirm station
   Actual true and the existing switch ON; repeat ON and verify no extra write.
   A retained positive station current can later become effective; no automatic
   zero preparation is expected.
4. Explicitly disable permission. Confirm Actual false independently of retained
   current. Station physical behavior remains the station's acceptance responsibility.
5. Use the station's Local control. Verify the authority-loss notification and
   immediately blocked permission actions. Change permission externally and verify
   existing five-second readback reflects it without duplicate publications.
6. In a controlled simulator/network interruption exercise, inspect rejection,
   missing readback, pending/unconfirmed diagnostics and reconnect without replay.
   No ACK alone may set a physical switch value. Use two entries to check isolation.
7. Compare entity/history/statistics continuity and profile editing; profiles must
   still cause no active regulation. Confirm unsupported commands remain blocked.
8. Measure HA Core CPU during longer alternating enabled/disabled idle periods,
   then explicit command bursts. Retain the passive <=0.1 percentage-point average
   target as a separate live criterion; record durations, load and instrumentation.

Automated tests cannot establish hardware safety, installed firmware behavior,
complete initial telemetry pushes or the live CPU target. Station main has no
positive-authority notification guarantee; authority changes other than reported
loss need supported readback. No additional authority polling loop was introduced.

## Implementation review and footprint

Production delta against the pinned develop baseline: **391 lines added, 29
removed, net +362** (Python and card JavaScript, including comments/blank lines).
The only new production module is `control.py` (273 lines). No unused future
operating-point or wake-up framework is included.

Reused: `State.observe/value/watch/publish/reset`, entity identity construction,
`Listener.accept`, `Adapter.request/spawn/close/live/read`, inventory bootstrap,
notification parsing, OCPP library serialization/schema validation/CALL matching,
and `poll_permission`. Simplified: disabled HA service methods become two explicit
request paths; `read` takes an optional intent-validity predicate and reports
whether its response was valid. Removed: blanket passive physical-command ban
(replaced by exact wire authorization), passive service errors and hard-disabled
card actions. An unnecessary authority subscription on Take Control and a duplicate
permission publication during authority loss were removed during implementation.
No existing station safety or telemetry/session code was restored or duplicated.

New state is bounded per entry:

- `Control.intents`: at most the latest authority and permission intent, including
  desired wire value and status/reason; identity itself is the intent-generation
  fence, avoiding a redundant numeric counter or outcome store.
- Each intent retains its owning adapter/connection/boot and permission authority
  revision to fence queued work and late results; its private `dispatched_at`
  prevents an older observation from confirming a write. None is a public timestamp.
- The intent task reference coalesces callers and keeps ownership through caller
  cancellation. Superseded in-flight calls settle through existing transport tasks.
- `Control.authority_revision` fences loss followed by reacquisition even when the
  observed authority has returned to Remote by the time a queued write can run.
- `Adapter.writable`: at most two unique verified endpoint names, cleared on boot
  and recreated on connection. This is capability authorization, not observed state.
- One task-local ContextVar carries the exact wire guard through schema validation
  and library lock waits; it grants no general SetVariables capability.

Tasks/listeners: one short-lived owner task per explicit new user command, using
the existing adapter task set and existing request-consumer tasks. Identical
concurrent callers share it. Two new keyed HA subscriptions (one per control) expose
command/readiness diagnostics and are removed on entity teardown. No periodic
command task, expiry timer, second queue, regulator or additional polling loop.
Normal metering does not enter control reconciliation.

## Automated validation and performance assessment

Final local checks: **194 Python tests passed**, **10 frontend tests passed**,
`ruff check .`, `ruff format --check .`, JavaScript syntax and `git diff --check`
passed. Five dependency deprecation warnings are unrelated to this change. The
36 added Python cases include actual OCPP WebSocket/schema exchanges and narrow
fault injection; the original 158 Python cases remain green.

For comparison, the existing passive benchmark was also run against a pristine
`git archive` of develop in a temporary directory, using the same Python/HA runtime:

| Workload | Develop | Active foundation |
| --- | --- | --- |
| 2,000 identical observations | 0 HA writes / 0 commands | 0 / 0 |
| 2,000 normalized unchanged voltage reports | 0 HA writes / 0 commands | 0 / 0 |
| Change-only idle / other entry activity | 0 HA writes / 0 commands | 0 / 0 |
| Initial sync | 39 writes / 39 state changes | 39 / 39 |
| Disconnect/reconnect lifecycle | 75 writes / 52 state changes | 77 / 52 |

The two additional lifecycle-only writes are bounded publication of the new
command-diagnostic subscriptions on generation reset; steady observation traffic
has no additional HA publication. The unchanged phase benchmark reports 2,000
identical reports, zero writes and zero expiry timers. Exact-energy changes remain
published without quantization loss. Five-second permission polling remains one
task and repeated same-value reads have no public timestamp churn.

These are event-count/performance-regression observations, not live CPU acceptance.
No claim is made that unit tests establish the <=0.1 percentage-point live target.

## Delivery and retained workspace files

Changes are confined to `feature/active-ocpp-control-foundation`. Changed files:
`AGENTS.md`, `README.md`; specifications `README.md`, `decision-history.md`,
`active-ocpp-control.md`; this assessment; production `control.py`, `ocpp.py`,
`button.py`, `switch.py`, `www/wallbox-manager-card.js`; tests
`test_active_control.py`, `test_protocol.py`, `test_wallbox_card.cjs`.

The initial workspace already contained eight untracked investigation/log files:
`docs/ocpp-meter-values-interval-investigation.md`, `docs/pv-decision-logic-audit.md`,
`docs/pv7-beta14-investigation.md`, four `docs/validation/beta9-*` files and
`pv2.log`. They remain untouched and excluded from commits. A completely empty
`git status` would require moving/removing user files; this task leaves them intact.
Tracked implementation changes are committed and pushed. See the final delivery
message for the exact head SHA and remote CI state; merge/release/deploy are separate.
