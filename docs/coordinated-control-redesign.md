# Coordinated control redesign implementation — 24 October 2026

## Contract and implementation

Implements WBM-RELEASE-01–14, WBM-NETZ-ACTION-01–13, the shared foundation of
WBM-PV-RELEASE-01–07, WB-LOCAL-WAKE-01–10 and WB-ENABLE-01–06. WBM-ARCH-01–12
remain the overarching responsibility boundary. PV V3 algorithms and economics
remain deferred and unchanged.

Stationary configures automatic wake-up exclusively at startup:

```ini
[wallbox]
auto_wakeup_enabled = false
cp_keep_awake = false
auto_wakeup_delay = 30
wakeup_duration = 5
```

Only case-insensitive literal true/false is accepted for the two booleans. Delay
remains integer 1–3600 seconds; duration remains integer 1–300 seconds. Missing
enable defaults false. Existing persisted `AutoWakeUpEnabled` data is ignored;
it cannot override the INI. The remote setter, persistence callback, Device Model
variable, reads/writes and notification wiring are removed. There is no replacement
OCPP variable. The existing EVSE observation service drives genuine same-session
0 A-to-positive CP-B episodes under either Local or Remote, without registration.
The Domain retains bounded GPIO confirmation, single-attempt cancellation, exclusive
phase-switch ownership and keep-awake positive-handoff suppression. Keep-awake's
GPIO-only timing worker, Modbus independence and accepted unplug latency remain.
Legacy manual `WakeUpVehicle` DataTransfer remains supported only by Stationary.

Hardware `ChargingEnabled` remains EVSE register 2005 bit 14. Both authority modes
use the same Domain permission implementation, command serialization, readiness
checks and confirmed readback. WBM retains the original hardware switch identity
on the device page, labeled Wallbox enabled / Wallbox aktiviert. Its OCPP intent
requires a supported unique writable endpoint and connection/boot fencing, but
not Remote authority or a transaction. Authority changes do not supersede a
hardware-permission intent. Permission writes never select a profile, alter release,
or start/stop a WBM current/phase request. In Local, enabling hardware can expose
the station's independently retained local positive current target.

WBM stores per-connector `charge_release` in its existing profile Settings document.
Initialization/reload resets it false and discards old runtime schedules, retaining
drafts, approvals, consumed/next timing and unrelated stored fields. Explicit Start
atomically persists approved power/timing, release and one-shot schedule. Apply
preserves release and existing absolute timing deadlines; OFF and actual profile
changes fence old intents before asynchronous storage, and request zero only with
Remote authority, registration and a valid transaction. Connection/authority loss
invalidates release; reconnect never silently authorizes positive replay. The
[post-review correction](release-off-recovery-correction.md) reconciles a confirmed
retained positive offer to 0 A while release is OFF, including after reload or
explicit takeover; confirmed zero does not cause another write. Losing
WBM release is not evidence that local charging physically stopped.

The existing Grid epoch, transaction and adapter-generation guards fence every
SetChargingProfile at the wire boundary. A further canonical release gate rejects
all stale positive points. An authorized zero-current pause does not depend on
positive-current capability diagnostics. No CP logic, safety loop, extra control polling, cache,
state store or PV regulation loop is added. Supported readback still distinguishes
requested, ACKed and physically confirmed offers. Recognized lockouts retain their
reported remaining-seconds + 5 / fallback 60-second retries. Actual reserve
ownership remains journaled separately; confirmed zero after release OFF restores
WBM-owned reserve without issuing a hardware-permission write. An existing journal
can reconcile still-running physical charging after reload without granting new
manager dispatch authorization.

## One action and dynamic range

The connector `charge_action` button is backend authoritative. Lovelace invokes
only this action for charging; Take Control remains a separate authority action.

| Release | Meaningful NETZ drafts | Action (German / English) |
| --- | --- | --- |
| OFF | any | Ladung starten / Start charging; requires a positive executable NETZ point |
| ON | no | Ladung beenden / Stop charging; release OFF and zero-current proposal |
| ON | yes | Ladeparameter übernehmen / Apply charging parameters; stays ON |

Apply intentionally takes the first press; Stop requires the second press, or
reverting drafts to equivalent approved settings. Draft edits alone do not dispatch
unapproved points. Existing durable legal-point equivalence, direct entity edits
and multiple-card semantics are preserved. Storage errors leave approval/release
unchanged and are surfaced; protocol rejection/unconfirmed readback is not reported
as applied. Concurrent action callers share one in-flight task.

The power editor uses the solver's exact positive envelopes: lowest eligible
technical minimum and highest step-quantized maximum, considering advertised phase
modes, min/max/asymmetry/step, per-mode caps and the existing consistent voltage basis
(complete valid phase voltages, otherwise documented nominal 230 V). A zero mode cap
excludes the mode. Hypothetical 2p is included only when advertised. Unknown/no
eligible capabilities disable the editor/action; legacy out-of-range drafts remain
visible for correction and cannot start. With unknown capabilities only draft
storage is possible; it grants no dispatch authorization. Positive selected power
can still resolve to zero in a gap under DOWN/NEAREST and cannot start NETZ.

PV has the shared Start/Stop release foundation, permits release at a zero target,
and never exposes an Apply state. No PV regulator is implemented. Future computed
zero pauses must leave release ON, and future positive proposals must use the same
canonical dispatch gate. Every actual PV/NETZ profile switch resets release.

## Migration and compatibility

Only exact integration-owned obsolete registry identities for automatic wake-up
and connector NETZ SET are removed, with config-entry ownership checks. Foreign
entries and profile data remain. The manual HA button remains retired. The new
connector action replaces SET; automations invoking the old SET or wake-up switch
must migrate. Hardware switch and Take Control identities remain. Its new label
clarifies hardware permission, not manager release. No manifest version bump.

Old stations advertising AutoWakeUpEnabled are deterministically ignored by WBM.
Old WBM paired with new Stationary loses that switch; unsupported operations must
not be interpreted as observed false. An old Stationary still rejects hardware
permission under Local, so matching firmware is required for the complete contract.
Manager release is entirely internal, with no wire representation.

## Source comparison and limits

Examined WBM tag `v0.3.2-beta.18` at commit
`87963014c9af73416c438807f523a3da2a98bd22`: `docs/grid-profile.md`,
`custom_components/wallbox_manager/grid_timing.py`, `battery.py` and the persistence/
power-limit sections of `profiles.py`, for historical durable scheduling,
zero-current versus permission, technical envelopes and battery-reserve ownership.
Its runtime, measured-phase optimizer, coordinator and percentage hysteresis were
not restored. Inspected actual openWB/core revision
`849340ec8d91f4e7cca08a7664cf9ec0187fb496`, file
`packages/control/chargepoint/chargepoint.py`, especially `_is_manual_lock_inactive`
and `is_charging_possible`: a policy lock gates charging separately from measured
plug state. RFID/disable-after-unplug can change that lock. This reinforces the
intent/observation distinction but is not an OCPP confirmation or replay protocol;
no openWB control loop or physical implementation was copied.

The combined simulated OCPP test uses the actual Stationary Domain and DeviceModel
against the WBM adapter, with mock hardware only. It is skipped when no companion
checkout exists; set WB_SOURCE_ROOT to select a checkout. Both repositories also
retain their independent complete protocol/session/measurement suites. Automated
validation does not establish live vehicle response or relay endurance.

## Proposed deployment and rollback — requires separate authorization

1. Independent review of both pushed branches; authorize merge/release separately.
2. Before any authorized installation, back up HA integration/profile/entity-registry
   storage and Stationary code/configuration/persisted state. Record installed revisions.
3. Upgrade Stationary first, deliberately configure the startup INI (default remains
   OFF), then perform the separately authorized service restart and validate readback
   and Local/Remote permission without assuming the old remote wake-up flag migrated.
4. Upgrade WBM and refresh the Lovelace resource. A separately authorized HA reload
   starts with release OFF. Check exact obsolete-entity migration, device hardware
   switch, explicit Take Control, NETZ Start/Apply/Stop, lockouts and reserve restoration.
5. Conduct supervised physical/vehicle acceptance only under separate authorization.
6. On rollback, stop manager release and verify the actual offered point while
   authorized and connected, reconcile/restore any WBM-owned reserve, then restore
   the matched prior code pair and HA backup as necessary. Registry deletion can
   require rediscovery/recreation of old entities. Old WBM does not understand release;
   its hardware-permission behavior can resume retained positive targets. Stationary
   rollback can restore the old persisted remote wake-up value: review it explicitly.

No production service, physical command, merge, release, tag or deployment is part
of this development task. Default-off local wake-up migration is deliberate.

## Validation results and remaining findings

Commands were run against the specified starting commits in isolated baseline
archives, then against the feature implementations. Local simulated OCPP sockets
required execution outside the filesystem/network sandbox; no hardware was used.

| Check | Starting baseline | Coordinated feature result |
| --- | --- | --- |
| WBM full pytest | 363 passed, 5 dependency warnings | 371 passed, same 5 dependency warnings |
| WBM frontend/card tests | 27 passed | 27 passed (German/English, multi-card, draft revisions, actions, failures) |
| WBM Ruff lint / format | PASS / PASS | PASS / PASS |
| WBM JavaScript syntax | established repository check | PASS |
| Manifest / translations / migration | existing schema and identity suite | PASS; manifest/version unchanged; EN/DE key parity, obsolete roles absent, exact registry ownership tested |
| Combined actual WB Domain/DeviceModel ↔ WBM OCPP | no combined test for this contract | PASS; included in WBM suite, companion checkout present |
| Stationary full maintained pytest | 818 passed + 607 subtests | 817 passed + 607 subtests |
| Stationary Ruff lint | 141 findings | 141 inherited findings; no newly introduced findings after normalized path comparison |
| Stationary Ruff formatting | 77 files require formatting | Same 77 files require formatting; rewritten wake-up test formatted |
| Stationary Python compilation | established production-source compilation | PASS |
| Both Git whitespace/diff checks | baseline clean | PASS |

Stationary lint/format commands therefore **do not pass globally**. No broad
unrelated cleanup was mixed into this change. Ruff's invalid-module-name messages
include the checkout basename; compare those as path-dependent baseline findings.
One reconnect regression exposed an existing assumption that independent workers
always notify in the same order. The assertion now compares each variable/value
and its newer timestamp by name, preserving complete-baseline verification without
imposing worker ordering. Removed obsolete remote-setting tests were replaced by
local strict-INI/default/independence, authority and variable-absence coverage.

Publication verification retains zero HA writes for 2,000 normalized unchanged
voltage reports and for identical ordinary reports. A real normalized voltage
boundary now intentionally changes **only** its voltage sensor and the dependent
technical power editor (two state changes); no other settings/control entities,
command or polling loop are introduced. Live CPU and Recorder acceptance remain
separate operational measurements.

Remaining operational limitations: simulated hardware does not establish physical
vehicle wake-up or GPIO/relay endurance; default-off migration needs explicit INI
selection; Local manager OFF cannot stop local charging; mixed old/new versions
have the compatibility limitations above. The optional combined test requires the
companion source checkout. No merge, release, tag, installation, HA reload,
production restart, deployment or physical hardware command occurred.

## Exact changed-file inventory

### Wallbox Manager

- `README.md`
- `custom_components/wallbox_manager/button.py`
- `custom_components/wallbox_manager/control.py`
- `custom_components/wallbox_manager/grid.py`
- `custom_components/wallbox_manager/grid_battery.py`
- `custom_components/wallbox_manager/grid_point.py`
- `custom_components/wallbox_manager/number.py`
- `custom_components/wallbox_manager/ocpp.py`
- `custom_components/wallbox_manager/settings.py`
- `custom_components/wallbox_manager/strings.json`
- `custom_components/wallbox_manager/switch.py`
- `custom_components/wallbox_manager/translations/de.json`
- `custom_components/wallbox_manager/translations/en.json`
- `custom_components/wallbox_manager/www/wallbox-manager-card.js`
- `docs/coordinated-control-redesign.md`
- `docs/specifications/README.md`
- `docs/specifications/active-ocpp-control.md`
- `docs/specifications/architecture-responsibility.md`
- `docs/specifications/automatic-vehicle-wakeup.md`
- `docs/specifications/charge-release.md`
- `docs/specifications/grid-profile.md`
- `docs/specifications/pv-regulation.md`
- `tests/test_active_control.py`
- `tests/test_charge_release.py`
- `tests/test_grid_profile.py`
- `tests/test_grid_set.py`
- `tests/test_grid_set_reevaluation.py`
- `tests/test_passive_availability.py`
- `tests/test_passive_benchmark.py`
- `tests/test_stationary_control_contract.py`
- `tests/test_vehicle_wakeup.py`
- `tests/test_wallbox_card.cjs`

### Wallbox Stationary

- `README.md`
- `config/model.py`
- `config/store.py`
- `docs/coordinated-control-redesign.md`
- `docs/ocpp.md`
- `docs/specifications/README.md`
- `docs/specifications/automatic-vehicle-wakeup.md`
- `docs/specifications/cp-keep-awake.md`
- `docs/specifications/local-auto-wakeup.md`
- `docs/specifications/requirements.md`
- `docs/wallbox-domain-api.md`
- `domain/wallbox.py`
- `ocpp_runtime/device_model.py`
- `ocpp_runtime/runtime.py`
- `ocpp_runtime/station.py`
- `services/evse_service.py`
- `tests/test_automatic_wakeup.py`
- `tests/test_cp_keep_awake.py`
- `tests/test_ocpp_capabilities.py`
- `tests/test_ocpp_delta_state.py`
- `tests/test_ocpp_device_model.py`
- `tests/test_ocpp_enabled.py`
- `tests/test_ocpp_vehicle_wakeup.py`
- `wallbox.py`
