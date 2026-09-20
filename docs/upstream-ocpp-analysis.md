# Upstream OCPP adoption analysis

## Revision and scope

Analyzed repository: [lbbrhzn/ocpp](https://github.com/lbbrhzn/ocpp).
Exact commit: **`848407c11ff659ce59779a99ce69984bbb0e3ce1`**.
Retrieved from upstream HEAD on 2026-09-20 using a shallow clone, then pinned with
`git rev-parse HEAD`. All source links below use that immutable commit, not `main`.
This is a source-level architecture review, not a charger interoperability test;
the upstream test suite was inspected selectively, not executed.

The upstream integration and its `ocpp` Python dependency are distinct projects.
The [manifest](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/manifest.json)
declares `ocpp>=2.1.0` and `websockets>=14.1`; those dependency version numbers do
not establish OCPP 2.1 protocol conformance. Dependency selection/pinning requires
separate compatibility evaluation when implementation begins.

Decision: selectively adapt protocol mechanics and regression-test ideas; build a
new product core. Do not fork the integration wholesale or make Wallbox Manager
an entity-level wrapper around its services. The target layout and policies are in
[architecture.md](architecture.md).

## Adoption matrix

Every mark is a **future recommendation**, not a claim of incorporated code.
A row covers the named responsibility, not necessarily the entire file. `—` means
not selected. Attribution codes: **A** = if source/tests are copied or substantially
adapted, preserve upstream copyright and MIT permission notice, record original
path/SHA and local changes; **N** = independently implement from requirements,
retain this research citation, reassess A if source text/structure is later adapted;
**D** = excluded, no copied material or attribution artifact needed today.

| Upstream component/file | Purpose | Reuse as-is | Adapt | Rewrite | Do not need | Rationale | Licensing/attribution |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [api.py](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/api.py), transport/negotiation | WebSocket server, charger routing and reconnect | — | Yes | — | — | Extract strict version negotiation, identity routing and session replacement; remove HA service coupling and implicit no-subprotocol fallback unless explicitly configured. | A |
| [chargepoint.py](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/chargepoint.py), lifecycle | Session tasks, cancellation and generation changes | — | Yes | — | — | Useful cleanup/reconnect mechanics; core ownership must fence all commands and results. | A |
| chargepoint.py, post_connect/core object | Discovery, configuration, metrics and HA updates | — | — | Yes | — | Split lifecycle, discovery, telemetry and control; remove automatic availability enabling and HA imports from core. | N |
| chargepoint.py, metering | Measurand normalization and phase aggregates | — | Yes | — | — | Reuse selected conversion ideas, replace metric storage with scoped timestamped phase channels; never inherit aggregation as the canonical model. | A |
| [ocppv16.py](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv16.py), protocol handlers | Configuration, status, transactions, metering | — | Yes | — | — | Mature mappings and transaction ambiguity handling are useful behind normalized adapter contracts. | A |
| ocppv16.py, charging profiles | Station/session limits and response handling | — | Yes | — | — | Retain correct wire scopes and response checks; replace product limits, profile ownership and amp/watt policy. | A |
| ocppv16.py, DataTransfer | Send and receive vendor payloads | — | Yes | — | — | Envelope handling is useful; replace generic acceptance with validated vendor registry. | A |
| [ocppv201.py](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv201.py), inventory/transactions | Reports, EVSE mapping, transaction ordering | — | Yes | — | — | Preserve identity and late-event lessons, avoid flattened connector identity in the core. | A |
| ocppv201.py, standalone MeterValues | Acknowledge periodic readings | — | — | Yes | — | Handler currently discards data; route through common sample normalization independently of transaction creation. | N |
| ocppv201.py, DataTransfer gap | Missing version-specific extension path | — | — | Yes | — | Implement inbound/outbound 2.x mappings and registry contract with version-specific tests. | N |
| api.py/chargepoint.py, OCPP 2.1 path | Negotiates 2.1 but shares 2.0.1 classes | — | — | Yes | — | Dedicated 2.1 schema and behavior boundary required; only share mappings proven compatible. | N |
| ocppv16.py/ocppv201.py, feature discovery | Feature profiles, inventory and probes | — | Yes | — | — | Retain evidence gathering; replace single SMART flag with granular evidence and non-mutating discovery. | A |
| [number.py](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/number.py) | Current limit controls and refusal feedback | — | — | Yes | — | Configured max bounds lack per-mode envelope; product controls go through ownership/solver. Preserve refusal behavior as a requirement. | N |
| [sensor.py](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/sensor.py) | HA metrics and entity lifecycle | — | — | Yes | — | New scoped sample/snapshot model, phase entities and Wallbox Manager IDs; use discovery/stale-cleanup lessons. | N |
| [switch.py](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/switch.py), button.py | Availability, transaction and maintenance UI | — | — | Yes | — | No direct protocol control bypass; distinguish administrative availability from physical charge permission. | N |
| [__init__.py](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/__init__.py), config_flow.py, const.py, enums.py | Integration identity/configuration | — | — | Yes | — | Keep Wallbox Manager scaffold and product identity; use protocol enums only within adapters. | N |
| [services.yaml](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/services.yaml), api.py service facade | Direct OCPP operations | — | — | — | Yes | Not the Energy Manager API; raw profiles/configuration must not bypass ownership. | D |
| ocppv16.py/ocppv201.py firmware, diagnostics, reservation functionality | Broad CSMS maintenance features | — | — | — | Yes | Outside initial charging-management scope; reconsider as explicit future features. | D |
| [translations/](https://github.com/lbbrhzn/ocpp/tree/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/translations), manifest.json, branding/docs | OCPP integration presentation | — | — | — | Yes | Retain existing Wallbox Manager strings, English/German translations and MIT license; upstream product text is not ours. | D |
| [tests/](https://github.com/lbbrhzn/ocpp/tree/848407c11ff659ce59779a99ce69984bbb0e3ce1/tests), selected protocol/lifecycle regressions | Charger mocks and bug scenarios | — | Yes | — | — | Port relevant scenarios to new contracts; no wholesale tests coupled to old services/metrics. | A |

No whole production component is suitable for unchanged reuse. The useful unit of
adoption is a small reviewed protocol helper, mapping or regression scenario.

## Findings against the requested upstream concerns

### Periodic metering is a real gap; phase information is partly retained

[ocppv201.py, on_meter_values (1350–1352)](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv201.py#L1350)
returns only `call_result.MeterValues()`. In contrast,
[_set_meter_values (1551 onward)](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv201.py#L1551)
converts TransactionEvent sampled values, handles unit multipliers and calls
`process_measurands`. This confirms the periodic-reading gap for the shared 2.x
path, including its advertised 2.1 mode. The replacement must parse EVSE-scoped
periodic samples even outside transactions, preserving timestamps and scope without
inventing a transaction or connector.

[chargepoint.py, process_phases (1128 onward)](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/chargepoint.py#L1128)
does preserve phase values in `extra_attr`; claiming upstream discards every phase
value would be incorrect. However, primary metrics aggregate readings, average
nonzero voltage/current samples, and share per-measurand attribute storage.
`MeasurandValue` lacks a sample timestamp. This is not an adequate canonical model
for phase sensors, freshness, different contexts and physical power solving.
Extract unit conversion ideas, preserve zeros and missingness, and build first-class
sample channels. Test totals plus phase values without double counting, line-neutral
versus line-line values, neutral current, multipliers and delayed samples.

### DataTransfer needs registry semantics and 2.x implementation

[ocppv16.py, data_transfer (1404 onward)](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv16.py#L1404)
implements outgoing calls. Its [on_data_transfer (1922 onward)](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv16.py#L1922)
stores a timestamp/payload and returns Accepted generically. This is transport
support, not a validated vendor extension framework.
The [base data_transfer (588 onward)](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/chargepoint.py#L588)
is a no-op, and `ocppv201.py` has neither an outgoing override nor an inbound
DataTransfer handler. Implement both version families behind the registry;
unknown vendor/message responses, payload validation, timeouts and ownership
revocation must be tested. No wallbox-stationary payload specification was supplied,
so its concrete commands remain unverified and must not be invented.

### Current bounds and conversions are not a physical operating-point solver

[number.py](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/number.py#L125)
constructs limits from configured maximum current; session entities also store that
maximum. The current revision includes useful refusal/reversion handling, so this
is not simply an unvalidated slider. It still cannot represent independent 1P/3P
min/max/step constraints that change with device state.

[chargepoint.py, _phase_count/_amps_to_watts/_watts_to_amps](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/chargepoint.py#L1085)
uses observed phase activity and voltage for conversions. This is useful evidence
for metering, not proof of supported phase modes or switching. The
[2.x set_charge_rate](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv201.py#L917)
contains a 22,000 W clear-limit threshold and clears at configured maximum current.
Do not inherit that policy: PV_MAXIMUM still respects explicit installation limits,
and a target never means indiscriminately remove a limit. Use the capability-based
solver with explicit infeasibility, rounding and transition reasons.

### Schedule phase fields and availability are not hardware control guarantees

The 1.6 and 2.x charging-profile builders specify protocol limits and scopes; no
reviewed path establishes a verified physical 1P/3P switching sequence. A schedule
phase count or selected phase is not evidence of contactor actuation. The adapter
must declare and verify a separate switching capability, potentially via isolated
DataTransfer functionality where standard control does not provide it.

[1.6 set_availability](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv16.py#L1165)
and [2.x set_availability](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv201.py#L1051)
send ChangeAvailability. Their existence establishes administrative availability
control, not a guaranteed physical CP relay operation. The 1.6 path includes
status/fallback handling; the 2.x method largely awaits calls without translating
the response into equivalent confirmation semantics. Normalize accepted, scheduled,
rejected, timed-out and physically observed outcomes separately. OFF needs a
verified stop/pause contract and must report when actual stopping is unconfirmed.

### Smart charging has improved detection, but remains too coarse

[1.6 get_supported_features](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv16.py#L727)
reads SupportedFeatureProfiles and allows a force override.
[2.x get_supported_features](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/ocppv201.py#L729)
uses inventory SmartChargingCtrlr/Available, also permits an override, and probes
GetCompositeSchedule when availability is unknown. It distinguishes missing from
explicit false unless overridden, and catches probe timeouts. Thus the current
revision does not simply treat a missing Available field as unsupported.

However, any normal GetCompositeSchedule response, including Rejected, enables
SMART. That demonstrates a message response, not that a requested SetChargingProfile
purpose, rate unit, stack level or EVSE scope works. Timeout also needs an unknown
or degraded evidence state, not a permanent negative capability. Discovery includes
an UpdateFirmware call with a dummy URL as a firmware probe; do not adopt a mutating
operation as generic feature detection. Collect advertised evidence read-only,
then record accepted/refused commands during explicitly authorized operation.
Overrides cannot manufacture physical switching support or bypass hard limits.

### OCPP 2.1 must not be just a renamed 2.0.1 adapter

[api.py, _build_charge_point](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/api.py#L314)
routes 2.x to `ChargePointv201`.
[chargepoint.py, version setup](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/chargepoint.py#L270)
uses `callv201` and `call_resultv201` even for V21. This supports sharing some
message mechanics but does not demonstrate complete 2.1 behavior. Create a separate
adapter and conformance test boundary; choose compatible schemas/dependencies before
advertising support. This review does not claim an exhaustive OCPP specification audit.

## Useful regression knowledge and architecture to reject

Useful upstream test candidates include `test_reconnect_lifecycle.py`,
`test_initial_start_lifecycle.py`, `test_v16_transaction_identity.py`,
`test_connector_aware_metrics.py`, `test_charge_point_v201_multi.py`,
`test_v201_smart_charging_probe.py`, `test_v201_probe_timeout.py`,
`test_number_limit_refusal.py` and `test_set_charge_rate_v16.py` in the pinned
[tests directory](https://github.com/lbbrhzn/ocpp/tree/848407c11ff659ce59779a99ce69984bbb0e3ce1/tests).
They provide scenarios for cancellation, session replacement, ambiguous transaction
identity, multiple connectors, missing inventory, probes and rejected commands.
Port behaviors into fake-adapter/core contracts before adapting handlers; add the
new ownership/lease and solver tests absent from the upstream product model.

Reject the combined HA/protocol/metrics/control charge-point object, direct OCPP
service control as the Energy Manager API, scalar configured maximum current as
physical capability, fixed product-wide power thresholds, implicit schema/version
compatibility and generic success acceptance. Most critically,
[post_connect](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/custom_components/ocpp/chargepoint.py#L370)
performs standard configuration and automatically calls `set_availability()`.
Discovery and reconnect in Wallbox Manager must not acquire authority or enable a
charger after LOCAL. Keep lifecycle mechanics but replace that policy entirely.
Do not broadly clear external/station profiles: track Wallbox Manager-owned IDs,
transaction scope and expiration, and reconcile only owned artifacts while authorized.
The new ownership layer also needs a trustworthy device local-takeover signal;
ordinary OCPP connectivity is not that signal.

## Licensing and attribution record

The pinned [upstream LICENSE](https://github.com/lbbrhzn/ocpp/blob/848407c11ff659ce59779a99ce69984bbb0e3ce1/LICENSE)
is MIT and identifies `Copyright (c) 2021 lbbrhzn`. Wallbox Manager retains its own
MIT license. **No upstream production code, tests or translations have been copied
or substantially adapted in this task.** The upstream checkout is a temporary
research checkout outside the project and is not committed or distributed here.

When adoption actually occurs, keep the upstream copyright and full MIT permission
notice with the distributed substantial portions (for example in an included
third-party notices file), retain relevant file notices, and record source URL,
exact SHA, original/local paths and modification summary. Apply this to copied
fixtures/tests and translated text as well as production code. Review separate
third-party dependencies under their own licenses when selected. Do not add a
present-tense code-reuse claim or a copied-code attribution artifact merely because
this document recommends future adoption.
