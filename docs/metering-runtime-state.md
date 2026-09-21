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
EVSE scope. TransactionEvent embedded measurements are consumed only by the
session ledger: they do not advertise ordinary meter channels or create HA meter
entities. TransactionEvent chargingState retains the explicitly reported EVSE and
optional connector. Missing EVSE scope is ignored rather than
assigned to connector 1. `offline=true` transaction reports are acknowledged but
not used as current state. The separate [session ledger](session-tracking.md)
can account for offline transaction lifecycle events without changing live
observation semantics. Authorization and raw replay archives remain unimplemented.

OCPP 1.6J MeterValues connector 0 stays station-scoped. Positive connector N uses
the existing `EvseId(station, "connector-N") / ConnectorId(..., "N")` mapping.
Optional transactionId also fences association with the separate active session.
StartTransaction/StopTransaction now feed the session ledger; they do not change
these lifetime-meter entities. No charging controls are added.

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
report retain their last valid live value while the connection generation remains
live; the sample deadline still limits session attribution and endpoint accounting.
Conflicting values for one channel at the same timestamp invalidate that reading
regardless of ordering;
identical duplicates do not extend freshness. Older samples cannot overwrite newer
ones. A lower energy register at a newer timestamp is retained as reported; HA's
`total_increasing` reset semantics apply, with no fabricated offset/session energy.

## Time, generations and state

Observations retain source time, receive time, source label and validity deadline.
Naive/invalid timestamps and source times more than five seconds ahead of receipt
are ignored. The 120-second sample deadline remains for time-bounded session
attribution and transaction endpoint accounting. It is **not** an HA live meter
availability deadline. Zero and non-zero measurements remain the current known
values while their station/connection generation is live, even if a station only
reports changed channels. Source/receipt timestamps remain visible; a known value
is not a claim that a fresh sample was recently received.

HA live meter availability requires a connected station and a valid, non-null
observation in the current runtime generation. Disconnect, connection loss,
boot/reconnect invalidation, missing data or a subsequently invalid/conflicting
reading makes the entity unavailable. Mere channel silence does not. The existing
WebSocket transport sends pings every 20 seconds and allows 20 seconds for a pong;
failed keepalive closes the connection and its receive-loop finalizer calls
Runtime.disconnect. Socket closure, transport failure and shutdown use this same
path. OCPP Heartbeat is acknowledged (boot advertises 60 seconds), but no separate
heartbeat watchdog or contradictory connection flag is added.

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
| Connector state | Connector state / Anschlussstatus | enum |
| Charging state | Charging state / Ladezustand | enum |

The enums are the canonical operational state surface. Charging, connected,
suspended_vehicle and suspended_station remain distinct; paused charging due to
insufficient station power is not flattened to charging or connected. OCPP
SuspendedEVSE maps to suspended_station and SuspendedEV to suspended_vehicle.
Available, Occupied, Vehicle connected and Charging active binary projections
are no longer created. Internal three-valued projection helpers remain available.

All entities attach to the station's existing HA device, with the explicit scope
in their translated names and attributes. Stable unique IDs encode entry ID,
station/EVSE/connector IDs, quantity and optional binary projection as a JSON tuple,
without ambiguous delimiter concatenation or transient generations. The Entity
Registry recreates only previously observed channels on reload; their values stay
unavailable until fresh observations arrive. Capabilities are not guessed from
station model names or an arbitrary universal entity list.

Updates are push-only; ordinary observation entities have no per-channel expiry
timers. Entry-level discovery and per-entity runtime subscriptions are removed on
unload. Temporary value loss, boot or disconnect never deletes an entity identity. English and
German names and enum labels are provided.

## Verification and exclusions

Pure tests cover immutability, numeric validation, normalization, scope/time/
generation fencing, unknown states, partial data and observed support. Real HA
registry/state-machine tests exercise dynamic creation, proper units/classes,
connection-based availability, invalidation, restart identity reuse and subscription
cleanup. Keepalive timeout tests exercise the actual WebSocket timeout path.
Versioned local OCPP wire tests cover 1.6J, 2.0.1 and 2.1 status, periodic and
transaction metering, offline/old data and multi-scope isolation. Existing ACK,
discovery, pending-call ownership, lifecycle, translation and solver checks remain.

There are no controls, PV modes, Energy Manager logic, EV learning, authorization
implementation, physical capability inference, voltage fallback, phase aggregation,
or writable/public control API in v0.1.0.

## Upgrading beta.6

The removed Available, Occupied, Vehicle connected and Charging active binary
entities may remain in HA's entity registry after upgrading. Session Charging
State is also no longer created; the live charging-state enum remains canonical.
There is no destructive automatic registry cleanup. Remove unwanted legacy test
entries manually. New installations create neither these projections nor old
TransactionEvent duplicate meters. Existing canonical IDs remain unchanged.
