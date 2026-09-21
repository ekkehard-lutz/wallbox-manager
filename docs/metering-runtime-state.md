# Metering and reported runtime state

## Boundary and scope

Versioned OCPP adapters validate wire messages with ocpp 2.1.0 and normalize them
into frozen `core.telemetry.Channel` and `Observation` values. `Runtime.observe`
is the only publication boundary. Station snapshots contain tuples of supported
channels and current observations; neither protocol objects nor HA types cross
into the runtime. These observed channels are evidence of telemetry support,
not verification of physical charging envelopes or control capabilities.

A channel is `(StationId | EvseId | ConnectorId, Quantity)`. There are eleven meter
quantities: voltage/current/active import power for each L1/L2/L3, explicit total
active import power, and explicit total imported energy. A reading never creates
missing phase channels or an inferred total. All values are finite, nonnegative
exact fractions in V, A, W or Wh. HA presents energy in kWh.

OCPP 2.0.1 and 2.1 MeterValues `evseId=0` is station-scoped; positive IDs retain
EVSE scope. TransactionEvent metering and chargingState retain the explicitly
reported EVSE and optional connector. Missing EVSE scope is ignored rather than
assigned to connector 1. `offline=true` transaction reports are acknowledged but
not used as current state. There is no transaction ledger, authorization feature,
transaction-derived session energy, or replay archive.

OCPP 1.6J MeterValues connector 0 stays station-scoped. Positive connector N uses
the existing `EvseId(station, "connector-N") / ConnectorId(..., "N")` mapping.
Optional transactionId does not introduce a transaction model. No new 1.6 start/
stop transaction handlers or charging controls are added.

## Measurement interpretation

`Voltage`, `Current.Import`, `Power.Active.Import`, and
`Energy.Active.Import.Register` are accepted. The default omitted measurand is
the standard imported-energy register. Units are normalized independently of
OCPP 2.x `unitOfMeasure.multiplier` (value × 10^multiplier): V/mV/kV, A/mA,
W/kW/MW, and Wh/kWh/MWh are supported. Missing energy units default to Wh;
missing voltage/current/power units are not guessed. Legacy numeric strings must
be decimal numbers. Non-finite, negative, malformed, or excessive values do not
become zero. Multipliers are bounded to [-12,12], decimal magnitude to [-30,30],
precision to 40 digits and normalized values to 10^18 to bound input arithmetic.

Explicit voltage phases L1/L2/L3 and L1-N/L2-N/L3-N map to the corresponding
reported voltage channel; line-to-line and neutral-only samples are not converted.
These display observations are not automatically promoted to solver-grade
phase-to-neutral voltage evidence. Current and power use L1/L2/L3. Unphased
voltage/current and phase-qualified energy are deliberately unsupported. Only
unphased imported-energy registers create the cumulative total-energy channel.

Only Outlet (including its standard omitted default) samples are accepted. Inlet,
body and cable locations are not merged into outlet readings. Periodic, clock and
trigger contexts are accepted. Transaction Begin/End contexts are accepted for
instantaneous values but not lifetime energy: devices may use session-relative
registers there. SignedData, other contexts, export/negative readings, unsupported
measurands and ambiguous phase semantics are not promoted to supported telemetry.
This is a deliberately narrow live-data subset of the broader planned metering
architecture; original unsupported/historical payloads are not stored.

A malformed sample for an already known channel invalidates its value when its
timestamp is current; it does not create a new entity. Unsupported measurands or
unidentifiable scope/phase/timestamp are ignored. Missing channels in a partial
report keep their last sample only until its own deadline. Conflicting values for
one channel at the same timestamp invalidate that reading regardless of ordering;
identical duplicates do not extend freshness. Older samples cannot overwrite newer
ones. A lower energy register at a newer timestamp is retained as reported; HA's
`total_increasing` reset semantics apply, with no fabricated offset/session energy.

## Time, generations and state

Observations retain source time, receive time, source label and validity deadline.
Naive/invalid timestamps and source times more than five seconds ahead of receipt
are ignored. Meter readings expire 120 seconds after source time (and no later
than 120 seconds after receipt). This initial conservative policy is fixed in the
adapter, not inferred from a vendor or polling configuration. Historical meter
readings may establish channel support but are immediately unavailable if stale.
Consumers must check `Observation.fresh(now)`, not use stored values blindly.

For 1.6 StatusNotification only, an omitted optional timestamp uses receipt time;
malformed supplied timestamps are not used. Meter sample timestamps are mandatory.

Status is event-driven: an unchanged parked station need not resend status on a
schedule. Consequently its last explicit state has no wall-clock timeout while
the same connection/boot remains live. Boot, disconnect and reconnect clear all
current observations, including state. Channel support remains known. Runtime
incarnation/connection/boot fencing rejects obsolete publishers; timestamp
ordering rejects older messages within that generation. Values are never restored
from HA as fresh telemetry after restart.

Connector state and charging state are separate enums. OCPP 2.x Available,
Occupied, Reserved, Unavailable and Faulted map to connector state. Occupied means
occupied, not charging. Charging activity requires TransactionEvent chargingState
(Charging, EVConnected, SuspendedEV, SuspendedEVSE, Idle), or detailed 1.6 status
(Preparing, Charging, SuspendedEV/EVSE, Finishing, Available). Faulted/unavailable
1.6 status leaves charging state unknown. Nonzero power never establishes charging.

A newer Available connector status reconciles already-supported charging state at
that exact scope to Idle; other non-Occupied status makes it Unknown. This never
creates charging support or changes a sibling connector/whole EVSE. Connector
status 0 in 1.6 remains a station availability observation; 2.x StatusNotification
requires positive explicit EVSE and connector IDs for this initial mapping.

## Home Assistant projections

The existing six diagnostics remain unchanged. Operational entities have no
DIAGNOSTIC category and are created dynamically only for observed valid channels:

| Observed channel | Entities | HA semantics |
| --- | --- | --- |
| Each phase voltage | Voltage L1/L2/L3 as reported | voltage / V / measurement |
| Each phase current | Current L1/L2/L3 as reported | current / A / measurement |
| Phase or explicit total power | Active import power for that channel | power / W / measurement |
| Total imported energy | Imported energy | energy / kWh / total_increasing |
| Connector state | Connector-state enum, Available, Occupied | enum, plain binary, occupancy binary |
| Charging state | Charging-state enum, Vehicle connected, Charging active | enum, plug binary, battery_charging binary |

Unknown enum state projects to unknown binary values, not false. Faulted/reserved/
unavailable connector status does not assert whether a vehicle is plugged in.
EVConnected and suspended states mean vehicle connected but charging inactive.
1.6 Preparing and Finishing are explicit states with charging inactive; vehicle
presence remains unknown, since connector occupancy need not prove a plugged-in EV.

All entities attach to the station's existing HA device, with the explicit scope
in their translated names and attributes. Stable unique IDs encode entry ID,
station/EVSE/connector IDs, quantity and optional binary projection as a JSON tuple,
without ambiguous delimiter concatenation or transient generations. The Entity
Registry recreates only previously observed channels on reload; their values stay
unavailable until fresh observations arrive. Capabilities are not guessed from
station model names or an arbitrary universal entity list.

Updates are push-only. One-shot expiry callbacks update HA availability without
polling. They are cancelled on refresh/invalidation/removal. Entry-level discovery
and per-entity runtime subscriptions are removed on unload. Temporary value loss,
expiry, boot or disconnect never deletes a channel's entity identity. English and
German names and enum labels are provided.

## Verification and exclusions

Pure tests cover immutability, numeric validation, normalization, scope/time/
generation fencing, unknown states, partial data and observed support. Real HA
registry/state-machine tests exercise dynamic creation, proper units/classes,
expiry, invalidation, restart identity reuse and timer/subscription cleanup.
Versioned local OCPP wire tests cover 1.6J, 2.0.1 and 2.1 status, periodic and
transaction metering, offline/old data and multi-scope isolation. Existing ACK,
discovery, pending-call ownership, lifecycle, translation and solver checks remain.

There are no controls, PV modes, Energy Manager logic, EV learning, authorization
implementation, physical capability inference, voltage fallback, phase aggregation,
or new writable/public control API in this feature. Manifest version is unchanged.
