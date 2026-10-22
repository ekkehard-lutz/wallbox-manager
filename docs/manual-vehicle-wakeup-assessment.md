# Manual vehicle wake-up implementation assessment

22 October 2026. Baseline `fix/takeover-button-visibility`,
`4c974edb294d8b2d47f64b6bbb749dfced46c8cf`; remote head verified before edits.
Stationary counterpart base `codex/phase3-explicit-vehicle-wakeup`,
`a601998cc851339b0f8d5dc67e63baf005695ebb`, also verified. Both candidates use
`feature/ocpp-vehicle-wakeup`. Affected requirements: WBM-WAKE-01–08,
WBM-CTRL-01/05–08/10–12, V04-01/02/04 and WBM-ARCH-01–12.

## Implementation and retained boundaries

`button.py` adds one default-enabled station button on the existing device, with
English/German names in strings/translations. Shared button execution and keyed
subscriptions retain Take Control's original command key/error label. There is
no card control. `control.py` extends existing per-entry intent/task ownership,
readiness and authority-revision fencing with one exact DataTransfer action and
bounded response validation. No new state/observation store or scheduler exists.
No observation confirms wake-up: only the exact successful CP-cycle response does.
Every completed explicit press is new; concurrent presses share one owned call.

`ocpp.py` checks action as well as exact payload under the existing dispatch guard.
Inventory still authorizes only the original two SetVariables endpoints. Authority
changes also publish/supersede wake-up diagnostics. The existing request method
uses a task-local optional response deadline, consumed only by the corresponding
library response waiter; recursive waits retain the remaining budget. The general
10-second default, bootstrap reads and five-second permission readback stay intact.
One additional HA command-diagnostic subscription belongs to the new button and
is removed by existing entity teardown. Existing generation publication handles
availability. No polling loop, timer, optimistic observation, hardware command
framework or automatic wake-up is added.

Normative request/response and limitations:
[manual wake-up specification](specifications/manual-vehicle-wakeup.md).
The device button deliberately has no invented capability probe: unsupported
station implementations reject the explicit extension. Existing outbound OCPP
serialization may delay permission/bootstrap reads behind a long wake-up. A
response timeout cannot establish restoration or vehicle behavior; no retry follows.

## Source comparison

Inspected WBM `v0.3.2-beta.18`,
`4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`, files `control/commands.py` and
`protocols/ocpp/v21/adapter.py`. Its outcome classifications and transport boundary
are relevant concepts, but no wake-up DataTransfer path is available to restore.
The current small intent/adapter paths are extended instead.

Inspected actual openWB/core
`849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
`packages/control/chargepoint/chargepoint.py`, especially
`initiate_control_pilot_interruption`: the long hardware pulse runs outside the
control cycle through a named thread. Its automatic start/stuck detection,
retry timestamps and vehicle configuration are explicitly out of scope. Reuse
only the boundary concept; Stationary's Domain and ControlDispatch remain sole
owners of hardware execution/restoration. No openWB implementation is copied.

## Automated evidence

Focused tests use real HA registration/entity registries, real OCPP schemas and
local WebSockets. They cover stable device/identity/default visibility, state and
session independence, all bounded results, malformed or unconfirmed restoration,
Local/unknown/offline/unregistered/unsupported EVSE rejection, concurrent callers,
caller cancellation, exact action/data/type authorization, authority loss and
reacquisition, disconnect/reboot, no replay and independent entries. Deadline tests
check wake-up-only extension and retained remaining budget. Frontend coverage
asserts wake-up's exclusion from card markup/actions and retains takeover tests.

No merge, version bump, tag, release, installation, service restart, deployment
or live hardware contact occurred. Independent interactive review and subsequent
explicit deployment/release approval remain outstanding.

## Final local validation and footprint

- Full Python suite: **234 passed**, five existing dependency deprecation warnings.
- Full frontend suite: **16 passed**; JavaScript syntax check passed.
- Repository-wide `ruff check .`, `ruff format --check .` and `git diff --check`
  passed. Local WebSocket tests run with localhost networking enabled.
- Production Python delta against the exact base: **191 added / 44 removed,
  net +147 lines**, including comments/blank lines. Three strings/translation
  files add **9 lines**; combined production delta **200 added / 44 removed,
  net +156**. Production card JavaScript is identical to the verified base.
- Added `tests/test_vehicle_wakeup.py`; extended `tests/test_wallbox_card.cjs`.
  Documentation changes: README, specification index, active foundation extension
  link, `specifications/manual-vehicle-wakeup.md` and this assessment.

The original eight untracked investigation/log files remain untouched and are
excluded from the feature commit. Remote CI is reported in the task delivery;
local tests do not establish hardware acceptance.
