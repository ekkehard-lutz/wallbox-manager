# Fronius polling analysis — proposal, not implemented

Inspected read-only on 2026-09-28: `~/Projekte/fronius-pv-manager`, branch
`develop`, commit `527d1e22902a1cadb686558ab13fc8d007f442db` (clean).
Paths below are relative to its `custom_components/fronius_pv_manager/`.
No Fronius files, branch, settings or devices were changed.

## Confirmed current flow

`const.py:DEFAULT_SCAN_INTERVAL = 30`; `coordinator.py:FroniusPVCoordinator`
passes this to `DataUpdateCoordinator(update_interval=timedelta(...))`.
There is one coordinator per entry. Setup creates one persistent TCP endpoint and
bound views for the configured `device_ids` (legacy/default fallback: device ID
1). The actual installation's IDs and discovered addresses are not present in
this repository; do not assume a particular Smart Meter ID.

Each refresh runs `_poll_devices` in an executor under `async_run_io`'s shared,
cancellation-safe I/O lock. Devices are read sequentially; every supported
occurrence in each discovered chain is read and decoded in full by `_poll_device`.
Unknown models are discovered but their payloads are skipped. `PollClass`
STATIC/FAST/NORMAL/SLOW metadata already exists in register maps but is **not used
by the polling loop**. Disabling entities does not reduce these reads.

After Modbus, each refresh also awaits one optional local Solar API HTTP request
(`GetPowerFlowRealtimeData.fcgi`, timeout 3 s). Currently only backup mode,
battery standby and battery mode semantics are consumed from that response;
PV/site power comes from Modbus. A wholesale interval reduction also accelerates
this HTTP request. Coordinator execution time/lock contention can extend the
observed publication period beyond the nominal interval.

Discovery is cached per device and endpoint generation, with a 300 s monotonic
expiry. Discovery verifies the two-register SunS signature and reads each
model's two-register header plus the end header. A new endpoint generation,
expired discovery, or unavailable model invalidates the live topology. The next
poll rediscovers; persisted topology constructs entities but never authorizes
writes. Periodic fast reads must not extend that discovery authority deadline.

## Concrete Modbus cost

`transport.py:read_holding_registers_chunked` uses **100 registers maximum per
request**. Every model uses its *discovered* payload length, not a short list of
requested sensor registers. For payload base `B` and length `L`, addresses are
`B .. B+L-1`, split into `(B, min(100,L))`, `(B+100, min(100,L-100))`, etc.
Absolute addresses follow the live SunSpec chain; hardcoding them is incorrect.

| Supported model | Map payload length | Relative ranges read at that length | Requests | Suggested future cadence |
|---|---:|---|---:|---|
| 1 Common | 65 | 0–64 | 1 | slow/static identity |
| 103 inverter | 50 | 0–49 | 1 | fast; AC power/current/voltage + counters/status |
| 120 nameplate | 26 | 0–25 | 1 | slow/static capabilities |
| 121 basic settings | 30 | 0–29 | 1 | slow, refresh after writes |
| 122 measurements/status | 44 | 0–43 | 1 | initially slow; promote if a control consumer needs its status |
| 123 immediate controls | 24 | 0–23 | 1 | slow, refresh after writes |
| 124 storage | 24 | 0–23 | 1 | fast; SoC shares the small block with reserve/configuration |
| 160 MPPT/storage DC | 88 (four modules) | 0–87 | 1 | fast; PV and battery DC measurements |
| 203 three-phase meter | 105 | 0–99 and 100–104 | 2 | fast; signed grid power/current/voltage + counters |

Model 160 has 8 fixed registers plus 20 per module: `L = 8 + 20N`; its request
cost is `ceil(L/100)` (four modules: 88/one request; five: 108/two). Other discovered
lengths/extra occurrences also change costs. Map lengths are expected layouts,
not measurements of the user's hardware.

General steady successful poll cost: `R = sum(ceil(L/100))` over all supported
model occurrences on all configured devices. Example: inverter models
1/103/120/121/122/123/124/160 with four MPPT/storage modules, plus meter 1/203:
**8 + 3 = 11 reads, 521 payload registers per poll**. Discovery additionally
costs `M+2` requests per device, including unsupported model headers. For precisely
that example: `10 + 4 = 14 reads / 28 registers` per discovery, approximately
once per 300 s; a discovery-due successful poll has **25 reads**. These counts
exclude writes, failure recovery, HTTP, and TCP framing/connection establishment.

| Full-poll interval | Reads/s (11-read example) | Reads/min | Payload registers/s | HTTP requests/s |
|---|---:|---:|---:|---:|
| 30 s (current) | 0.367 | 22 | 17.37 | 0.033 |
| 10 s | 1.1 | 66 | 52.1 | 0.1 |
| 5 s | 2.2 | 132 | 104.2 | 0.2 |
| 1 s | 11 | 660 | 521 | 1 |

Add roughly `14/300 = 0.047` discovery reads/s for this healthy example; the exact
schedule is the next poll after expiry. One-second operation is a calculation,
not a recommendation or a throughput measurement.

Dynamic values share blocks with slow data. Model 103 W/W_SF are offsets 12/13;
phase measurements occupy earlier offsets. Model 203 W/W_SF are 16/20. Model 160
DCW is module offset 11 (absolute payload `8 + 20*i + 11`), with fixed scale
factors at 0–3 and module identity used to distinguish MPPT from battery channels.
SoC and reserve live together in model 124. Reading energy counters or identity
incidentally in a fast model often adds no request. Individual semantic
`read_register` calls would typically require a separate scale-factor read;
using them per fast sensor would increase request count rather than reduce it.

The actual derived dependencies matter: `sensor.py:SolarPowerSensor` sums
classified model-160 MPPT DCW; signed grid import/export uses model-203 W.
`SolarConsumptionSensor` requires exactly one mapped inverter and meter and
computes `max(0, inverter_103_W + import_203_W - export_203_W)`. Read those sources
in the same fast refresh and publish once. SoC comes from 124; battery DC channels
are in 160. Do not classify all storage data as slow just because it includes
configuration, or confuse inverter AC W with the PV DC sum.

## Recommendation and alternatives

| Approach | Traffic/consistency | Complexity and failure behavior |
|---|---|---|
| A: entire coordinator at 5 s | 11 reads each tick in example; all values remain one snapshot, but identity, controls and HTTP are unnecessarily repeated | smallest code change; preserves current failure model; useful only as a measured temporary experiment after device limits are known |
| B: one 5 s coordinator, selectively due models | fast 103/124/160/203 = 5 reads/267 registers; remaining models = 6 reads/254 registers every 30 s initially | recommended; one transport owner, lock and snapshot; requires explicit per-model sample metadata and slow-cache expiry |
| C: separate coordinators | same potential read savings as B, but separate updates can publish mismatched inverter/meter samples | more scheduling, availability and write-invalidation complexity; must still share one endpoint/lock, so parallel coordinators do not justify concurrent Modbus calls |

Start with **B, whole models**, after resolving hardware update frequency. In the
example a 5 s fast / 30 s slow schedule averages `5/5 + 6/30 = 1.2` reads/s
(+ discovery), versus A's 2.2. A full refresh still costs 11, not 16: do not reread
fast models on a slow-due tick. Keep optional HTTP at 30 s initially. Longer
static intervals can follow evidence. A later model-203 fast subrange 0–20 would
fit in one request, but requires a partial-block decoder/cache with scale-factor
and validity guarantees; do not start by adding that complexity. Model 160 at
88 registers cannot save a request by dropping fields.

Preserve current per-device/per-model isolation: failed reads expose unavailable
values, never quietly reuse the previous fast sample. All configured device views
share one endpoint: a transport error resets the session/generation, potentially
invalidating other devices' cached topology. Successful sibling reads are still
represented independently. Retries currently occur on the next scheduled poll
(no transport retry loop; pymodbus retries=0). Faster scheduling also increases
outage reconnect attempts, so add bounded retry backoff without presenting failed
fast measurements as fresh. Do not let slow-cache merging mask endpoint changes.

Keep poll and write/readback operations under the existing `async_run_io` lock;
workers must drain before cancellation releases it. Write transactions already
perform authorization/readback; schedule affected models due after writes,
without bypassing their fences or issuing implicit writes. Static cached data
cannot authorize commands after discovery's 300 s lease expires. Slow/fast paths
must retain per-model availability, actual read time, last-success time and source
generation. A new coordinator notification must **not** advance apparent HA
freshness for an unread slow value. Derived site power must not mix a failed/old
meter with a fresh inverter. Tests should cover these invariants before migration.

## Hardware questions and next step

Repository code establishes request costs, **not** GEN24/Smart Meter internal
measurement frequency. Obtain exact firmware/model-specific Fronius documentation
or support answers for:

1. GEN24 Modbus/SunSpec measurement publication interval (especially 103, 124, 160).
2. Smart Meter measurement interval and transfer/cache interval as exposed by
   the inverter's Modbus device view (203).
3. Whether those models or individual fields update at different rates.
4. Whether reads return instantaneous samples, a fixed-cycle cache, or independent
   caches; whether any timestamp/sample counter identifies a new acquisition.
5. Recommended/minimum polling interval and maximum requests/s, concurrent clients,
   connection limits, and timeout/reconnect guidance.
6. Whether multi-request model reads can cross a device update boundary and how
   sample coherence can be established.

Next: answer these questions, capture actual discovered IDs/lengths, measure poll
latency and value-update cadence on hardware, then implement B with configurable
fast/slow intervals, sample provenance and regression tests. Approximately 5 s is
a target to validate, not an established supported rate. Do not poll materially
faster than source publication without a measured reason. No Fronius change is
included in this Wallbox Manager implementation.
