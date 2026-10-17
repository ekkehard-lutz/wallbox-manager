# Passive v0.4 rebuild: architecture, compatibility and migration review

Branch: `rewrite/passive-v0.4`. Base/rollback: immutable `v0.3.2-beta.18`,
`87963014c9af73416c438807f523a3da2a98bd22`. Manifest deliberately unchanged.
This is an independent passive implementation. No legacy control runtime is
loaded, disabled or wrapped. No release, merge, deployment or hardware operation.

## Architecture and removed subsystems

Data path: OCPP2.1 adapter → one entry-owned dictionary of canonical observations
→ entities subscribed to that exact `(family, scope, role)` key. Each observation
contains value/unit/source/acquisition/receipt/generation/status and optional
explicit evidence deadline. There are no selector functions, per-publication
projections, secondary telemetry snapshots or general notification service.
Session power references a canonical channel rather than copying live power.
Session energy is necessary transaction bookkeeping, distinct from lifetime energy.

A connection/boot invalidates observations and publishes affected physical and
session entities. Routine acquisition only publishes its own key, plus an initial
completeness diagnostic when a previously missing bootstrap field first arrives.
Settings subscribe only to explicit local edits; they never receive metering.
Physical device metadata updates only on station identification. Ordinary values
have no timer, age scan, smoothing, policy evaluation or expiry. Per-instance
safety evidence has at most two one-shot entity timers (permission/physical phase),
and a duration refresh exists only during a confirmed active transaction. There
is one five-second permission query loop and the WebSocket library's existing
20-second Ping/Pong; no second heartbeat loop. There is no HA input polling or
regulation subscription; input entity mappings are stored only.

Deleted rather than retained: global ownership/active-wallbox switching; control
runtime, solver and command orchestration; OCPP authority/permission write paths;
PV regulators/SoC policy execution/forecast planner; economic accounting and
restart protections; battery actuation/recovery; charging clocks/debounce tasks;
legacy immutable snapshot model/selector chains; multi-version adapter hierarchy;
full legacy session ledger and broad publication/expiry optimizations. Approved
requirements for their later replacement remain in `docs/specifications`.

## Remaining production modules

| Module | Necessary responsibility |
| --- | --- |
| `__init__.py` | Per-entry setup/unload, existing HA stores, physical device registration |
| `const.py` | Public domain, listener defaults, platform list |
| `config_flow.py` | Explicit station/EVSE/connector binding, nonconflicting port, saved source mappings; no subentry flow |
| `migration.py` | Resolve one legacy target before writes, archive subentry data, move registry associations safely |
| `store.py` | Sole canonical observation store and exact-key entity callbacks; generation/source fencing |
| `meter.py` | Small OCPP2.1 timestamp/phase/unit normalization, boundary-energy separation |
| `ocpp.py` | Single socket/listener, OCPP handlers, bounded inventory/readback bootstrap, permitted permission query |
| `session.py` | Observed transaction identity/start/end, noninvented session delta, closed-ID tombstones, separate passive persistence |
| `settings.py` | Pure settings reads, edit validation, serialized lossless profile storage writes |
| `entity.py` | Stable public IDs/device joins, exact-key attachment and evidence deadline cleanup |
| `sensor.py` | Measurement/status/diagnostic/session HA projections; active duration refresh |
| `binary_sensor.py` | Connection and genuine session activity |
| `number.py` | Existing numeric preferences and exact manual-current restoration |
| `select.py` | Existing local profile/approximation selections |
| `text.py` | Optional durations, stored without scheduling |
| `switch.py` | Actual read-only permission under the existing switch identity; actions explicitly fail |
| `button.py` | Existing takeover identity, unavailable and explicitly rejected |
| `frontend.py` | Existing bundled static-card route and Lovelace resource identity |

No other Python modules remain in production. Source sizes, including blank and
comment lines, are recorded in [footprint.json](footprint.json). Against beta.18:

| Source | beta.18 | Passive rewrite |
| --- | ---: | ---: |
| Production Python modules | 77 | 18 |
| Production Python lines | 18,097 | 2,375 |
| JavaScript card lines | 441 | 350 |
| Python test lines (including fixture) | 32,129 | 1,286 |
| JavaScript test lines | 871 | 117 |

The footprint is the entire remaining source, not net Git additions. Production
Python is approximately 87% smaller. The exact final counts are machine-readable;
minor report formatting does not count as production. No speculative regulator
module, execution interface or placeholder ownership class was introduced.

## Narrow reuse and origins

No entire legacy Python module or legacy test module was retained. New Python
and JavaScript tests were written for the passive contract. Selectively reused
behavior/short expressions from the beta.18 tag:

- `entity.py:station_identifier/observation_unique_id`,
  `control_entity.py:control_id`, `session_entity.py:SessionEntity.__init__`,
  `capability_entity.py:CapabilitySensor.__init__`: exact compact JSON/public
  identifiers and device tuple. Essential for registry and statistics continuity;
  no legacy model types/dependencies carried along.
- `protocols/ocpp/common/metering.py:reported_time`, `quantity_for`,
  `normalized_value`: timestamp rejection, recognized phase/measurand/unit/context
  rules and conflict invalidation extracted/reexpressed in `meter.py`. OCPP1.6
  branches, legacy core types and metering expiry removed. Internal meter energy
  is directly kWh; HA output/unit/state_class remains identical.
- `grid_timing.py:duration_seconds/duration_text`: just optional hh:mm ↔ seconds
  validation/formatting. No scheduling or charging class retained.
- Card: retained its CSS/layout, localized number parsing, power stepping,
  press/hold and Number/Text edit interactions, options rendering and custom card
  registration. Rewrote instance discovery, live presentation and passive actions;
  removed wallbox picker, switching, ownership errors, command/status policies,
  frontend periodic expiry and armed charging countdown.
- Metadata/translation keys, resource URL and packaging identity preserved.
  `frontend.py` is a small rewritten resource lifecycle using the same public HA
  interfaces, not the old runtime; third-party MIT notice remains in the package.

## Public interface inventory and intentional exceptions

The [baseline inventory](compatibility-inventory.md) was written before deleting
production code. [Literal public attribute inventory](beta18-public-attributes.json)
adds source paths/methods for audit. Old source is permanently available at the
tag; the repository does not contain a compatibility copy of the old architecture.

Preserved: scope-specific station/EVSE/connector observation unique IDs; original
control/profile/session/capability IDs; physical device identifier; translation
keys; registry-assigned entity IDs/device associations; cumulative imported kWh,
energy/total_increasing metadata; W/power/measurement and A/V measurements;
source/acquisition/receipt provenance; actual reported enums, permission and
authority; profile values and duration fields. Energy counter values are neither
reset nor synthesized. Transaction boundary energy never creates lifetime energy.

Intentional exceptions, explicitly required or deferred:

- Global `active_wallbox` is no longer instantiated. Its existing registry entry
  is not deleted by this code; it may remain unavailable after migration.
- Existing takeover button is unavailable; permission switch remains an observed
  switch, but ON/OFF services raise HomeAssistantError. Both card buttons are
  visible, disabled, and have no action callback. Permission unknown is unknown,
  not false. Backend old actions are never acknowledged as successful.
- Configuration entities keep local preferences and storage validation. Dynamic
  solver/capability-derived input maxima and command-result/confirmed-point,
  SoC-regulator, PV-day, economic, battery-control and ownership presentation are
  unavailable/deferred. Retained execution attributes explicitly say passive_only,
  false readiness or null; additional old policy-result attributes listed in the
  baseline JSON are not calculated/published. No retained-current or economic
  protection effectiveness is claimed. Raw capability diagnostic sensors use
  actual inventory; saved capability fallbacks stay stored but are not presented
  as observed station evidence.
- Newly added `physical_phases` station diagnostic reports explicit phase readback
  with its evidence deadline. It does not replace any existing measurement ID.
- Latest current-generation reported connector/charging status persists through
  silence and CP pauses, as approved by PV-OCPP-05. The old CP observability
  ageing/grey-overlay state machine is removed. No synthetic Idle/absence is
  inferred from permission, missing power or a missing transaction.
- Only OCPP2.1 is implemented in this first milestone. Prior 1.6/2.0.1 adapters are
  deliberately removed; no generic compatibility/readback claim is made.
- Capability/command/economic diagnostic logging engines are deferred; legacy
  options and stored results stay untouched for rollback. There are no live
  charging safety promises from this passive integration; station safety remains
  independent and no WBM charging control exists.

## Initial synchronization and current interface limits

Every accepted socket creates a connection generation. An accepted BootNotification
creates a boot generation and starts exactly one bounded bootstrap **after** its
response. Another boot cancels obsolete initialization and permission polling;
in-flight OCPP response consumption is shielded until settled to preserve library
serialization, while old replies cannot update new-generation observations.
Shutdown cancels/joins all owned tasks and closes the socket/listener. Old source
timestamps and generation tokens cannot repopulate new evidence.

| Field | Acquisition | Incomplete/unsupported result |
| --- | --- | --- |
| Identity | BootNotification | awaiting registration; no invented identity |
| Capabilities | GetBaseReport + completely sequenced NotifyReport | discovery unsupported/degraded; unknown diagnostic value |
| Authority | Advertised readable WallboxController.ControlAuthority Actual via GetVariables; hardwired NotifyEvent | unknown; no takeover |
| Permission | Advertised readable WallboxController.ChargingEnabled Actual; five-second readback | unknown/15s expiry; no corrective write |
| Physical phases | Advertised Connector.PhaseRotation Actual readback / NotifyEvent | unknown/5s evidence expiry |
| Voltage/current/power/lifetime energy | Station MeterValues | individually missing until current-generation push; no ordinary expiry |
| Connector/charging state | StatusNotification / TransactionEvent | individually missing; no inferred physical absence |
| Transaction/session | Explicit current real TransactionEvent; optional scope only from that same known transaction | last session historical; current activity unknown until reconfirmed |
| Applied operating point / command result | No command execution in this milestone | explicitly unavailable; no GetCompositeSchedule query or fabricated confirmation |

GetBaseReport is inventory, not live metering. There is no unproven TriggerMessage,
1.6 GetVariables, artificial startup counter or recurring full-snapshot request.
Completeness diagnostics list missing fields; arrival of a missing field updates
that result directly. Initialization is bounded by protocol response/report timeouts
and produces missing/unsupported/degraded results instead of a silent endless task.

The current station does **not** guarantee a complete immediate snapshot after
registration, particularly under CP pauses and for unchanged per-phase zeros.
Until separate station work, those fields remain missing. Future station-side
initial push is explicitly outside this branch. Physical phase proof is read once
and expires unless a fresh report arrives; there is no added phase polling. No
claim that arbitrary wallboxes, unsupported query endpoints, applied charging
points or real hardware initialization are fully verified.

## Registry migration, persistence and rollback

No config-entry schema bump: version3 remains readable by beta.18. Keep entry ID,
physical device ID and registry unique IDs. Explicit new-entry station binding or
legacy subentry/registry/profile target identifies exactly one connector. Multiple
configured stations/connectors, unknown subentry types or incomplete identities
fail before any migration write; they require a separate migration decision.
No first-connector selection and no dynamically created second physical device.

Migration order is essential: **entity subentry association → existing device
subentry association → removal of empty subentry**. HA removes entities left on
an old subentry when moving a device. The focused real-registry test found that
hazard and verifies the corrected order, both immediately and after queued events.
Archived original subentry data/ID/title/unique ID and beta.18-readable
station_references remain in options. Device/entity identifiers, names, disabled
settings and device associations are not recreated to change native HA layout.
The exact wording/layout of HA's native integration page is HA-owned; the runtime
has one physical device directly attached to each entry and no subentry flow.

Existing profile Store version1/key format remains. Load preserves values and all
unknown fields without normalizing/clamping them; edit validation runs on input,
not reads. Serialized writes avoid lost concurrent changes. Central timing/SoC
options retain their existing precedence. Legacy seconds-based smoothing is
preserved verbatim (no reinterpretation as cycle count); its approved versioned
unit migration belongs to future regulation implementation, which is deferred.
Manual numbers and approximation restore their HA states, including exact
rational current-limit attributes. Durations are saved seconds, never armed.

The full beta.18 session file, ownership/economic/battery/PV-day files, Recorder
and statistics are never written/deleted. The last legacy session is read-only
input for historical reporting, retaining session_id and unique IDs. New passive
session state/closed transaction IDs use `wallbox_manager.E.passive_session`.
No previous-generation power/activity is restored as live. Economic protection
and recovery cannot silently run because those modules do not exist.

Rollback assessment: tests preserve the original energy entity ID/device and
statistics metadata through subentry detach/removal and compatible subentry
reconstruction; profile values remain readable in the legacy map; the legacy
session document remains byte-semantically unchanged. Restoring beta.18 code can
rediscover/recreate its subentry from retained references without recreating HA
entities or repairing history. New passive session history is separate and is
not imported as control/recovery proof by beta.18. No live migration was attempted.
A real HA backup is required before the separately approved first installation;
real installation/version-specific behavior and CPU acceptance remain unverified.

## Focused tests and deterministic workloads

The new suite contains **21 new Python tests** and **7 new JavaScript tests**.
Specifically retained/adapted legacy Python tests: **0**; retained legacy frontend
tests: **0**. The fixture is newly written around real HA entity platforms and
registries. Real local WebSockets and the pinned OCPP library exercise actual
OCPP schemas, CALL/CALLRESULT/ERROR framing and supported readbacks. This is not
an assertion that the rewrite passes beta.18's different 2,714/145-test suite.
That obsolete suite was deliberately removed with its ownership, solver, policy,
economic, control and multi-version runtime implementation, and remains in Git.

| Requirement group | Evidence in the new suite |
| --- | --- |
| V04-01/02 | explicit binding, unexpected station/connector refusal, multi-target fail-before-write, independent entries/ports/state/settings/card |
| V04-04/05 | exact four-family/three-scope IDs, actual legacy energy entity registry ID/device retention, units/device/state classes, one native physical device, no subentry flow, compatible rollback references |
| V04-07 | legacy profile values loaded unchanged, Number/Select/Text editing, unknown fields preserved, concurrent saves serialized, forbidden physical services rejected |
| V04-08 | 2,000 same-value acquisitions, narrow writes, current provenance, three-day held measurement without expiry, unknown values, energy/session boundary separation |
| V04-09 | real boot/heartbeat/status/meter/transaction handlers; inventory readbacks; unsupported inventory; duplicate boot; deliberately late old-generation authority response; reconnect and same-counter restart fencing; five-second actual permission query and cleanup |
| V04-03/06 | outbound read-only allowlist, no automatic takeover, no physical action on config edits, explicit backend rejection, disabled/no-callback buttons, card instance isolation and German/English presentation |
| Passive session | real identity, measured delta, no invented start on Updated-only data, ended-ID tombstones, legacy display/file preservation, historical versus current activity, active duration timer cleanup |

Full local checks: Python suite passes with five existing dependency deprecation
warnings; JavaScript suite, JavaScript syntax, Ruff lint/format and whitespace
checks pass. The isolated browser preview verifies the retained card appearance
and visible disabled controls; it is not a live HA installation/native-page test.

The data-publication benchmark uses real HA entities/events with a complete
**simulated** supported baseline: 11 meter channels, reported connector/charging
state, authority/permission/phases, eight capabilities and two discovery fields.
It measures accepted observations and changed canonical values separately.
All counts for the second-instance scenario are effects on the **first** entry;
the second receives 100 updates. Protocol command observations are independently
checked with actual OCPP peers; no control-command path exists in production.

| Workload | Accepted | Changed canonical values | HA writes | HA changed-state events | Unrelated writes | Charging commands |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full simulated initial baseline | 26 | 26 | 39 | 39 | 0 | 0 |
| 2,000 same-valued power reports | 2,000 | 0 | 2,000 | 2,000 | 0 | 0 |
| 100 changed power reports | 100 | 100 | 100 | 100 | 0 | 0 |
| Change-only idle, no incoming report | 0 | 0 | 0 | 0 | 0 | 0 |
| Other instance receives 100 updates | 0 | 0 | 0 | 0 | 0 | 0 |
| Disconnect/reconnect + new full baseline | 26 | 26 | 75 | 52 | 0 | 0 |

An identical numerical value with new public acquisition metadata legitimately
creates a changed HA state; the benchmark does not hide that Recorder cost.
Initial completeness and connection/generation invalidation account for extra
diagnostic/physical writes. Unrelated means configuration/takeover entities or
other measurement channels during ordinary updates; the power-only scenarios
assert the exact single affected entity ID as well as event counts.

The real idle-protocol test observes exactly **one permission GetVariables query
in 5.2 seconds** after bootstrap. No recurring policy, phase query, meter query or
second heartbeat is created. The separate active-session duration timer is
legitimate UI work; no timer remains when the session completes or unloads.
Machine-readable results: [benchmark.json](benchmark.json).

## Deferred requirements and residual concerns

V04-10/11 future regulator, shared SoC/FAST/BALANCE/forecast/economic policy,
verified charging commands, physical application recovery and coordinated Energy
Manager work are intentionally absent. Their individually approved requirements
were preserved from specification branch commit
`707e0a9c561d31c1adf49f38137a7dd81f03094d`, including PV-ARCH-09, with only
specification documents imported. Overall V3 approval remains unresolved. The new
passive requirements and decision history distinguish that future scope explicitly.

Public interface exceptions above are deliberate: local configuration entities do
not mirror volatile hardware context (`connected` is null, control readiness is
false); actual hardware evidence is exposed by its own entities. Policy-only
attributes and capability fallbacks are not computed to keep an old regulator UI
appearing active. No Energy Dashboard/statistics identity exception is intended.

The current station's missing complete initial push is the principal interface
limit. Missing fields are explicit; ordinary silence after a valid initial report
is not failure. Real HA migration/native layout across the installed environment,
wallbox interoperability and the ambitious **<=0.1 percentage-point average CPU
increase** require separate live acceptance after review/release and backup.
No simulation proves live CPU improvement or resolution of Activity/Logbook load.
The generation, provenance and legitimate acquisition writes remain intentionally
visible. No Recorder exclusions, hidden telemetry suppression, station traffic
change or deployment was used to improve the counts.
