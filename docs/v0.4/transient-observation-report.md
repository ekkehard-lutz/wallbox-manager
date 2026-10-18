# Transient physical-phase observation robustness

Base: `develop` / `v0.4.0-beta.2`, commit
`b4a38e8dbf7212355af8a726e020f284be45b6b2`.
Branch: `fix/transient-observation-availability`.

## Root cause and scope

The WBM defect is confirmed in its own receive path:
`Adapter.variable()` maps empty/unrecognized `Connector.PhaseRotation` feedback to
`None`; beta.2's `State.observe()` then replaces the prior valid observation with
that `None`. The last-observed sensor therefore immediately becomes unavailable,
even within the same connection/boot generation. The next valid `RST` replaces
it with 3 again, producing two unnecessary history transitions for one transient
invalid report. No actual WebSocket disconnect is necessary.

The reported station-side hardware-lock explanation remains a supported hypothesis,
not a captured-message finding. No station source or live HA installation was
modified. The regression uses a real OCPP 2.1 WebSocket peer to send schema-valid
`NotifyEvent` messages with an empty `actualValue`, reproducing the WBM input path
without relying on a particular charger or its transmission cadence.

Only physical-phase transient display handling changes. No global debounce,
measurement deadband, source rounding or active control is introduced.

## One observation, separate display and proof semantics

The existing canonical `Observation` holds three small additional fields:

- `reported_at`: timestamp of the latest accepted phase report, including invalid
  reports. This is an ordering watermark, not the last valid value's acquisition time.
- `reported_generation`: generation of that watermark. It can differ from the
  retained valid sample's original generation after a reconnect or new boot.
- `invalid_count`: consecutive independent invalid phase timestamps, capped at two.

When a phase report is invalid, the same store entry retains the last valid
`value`, `source`, `observed_at`, `received_at`, `generation`, unit, status and
original validity deadline. Only report-ordering metadata and the invalid counter
change. An invalid report never refreshes the acquisition/receipt time or deadline
of a retained valid value. Even an invalid first report in a new generation does
not relabel an older retained value as current; ordering/counters follow the new
report generation while provenance remains attached to the original sample.

`State.value()` immediately returns `None` after the first accepted invalid
report, regardless of the retained numeric value or unexpired original deadline.
It also requires a current connected generation and an unexpired deadline.
The retained observation's `status` describes its last valid sample; status/value/
deadline fields alone are **not** a current proof API. Any future safety consumer
must use `State.value()` and its additional command/safety fences.

`State.last_observed_value()` is explicitly a display projection. In the current
generation it returns the last valid count after zero or one invalid report, and
returns `None` after two. The existing phase sensor already uses this method for
both `available` and `native_value`; no per-entity observation copy is added.

| Input after valid 3 | HA display | Current proof |
| --- | --- | --- |
| One invalid/empty report | 3 | Immediately invalid |
| A second distinct consecutive invalid timestamp | Unavailable | Invalid |
| A fresh valid `RST` | 3 immediately | Valid within its original new five-second deadline |
| A fresh valid `Rxx` | 1 immediately | Valid within its original new five-second deadline |
| Five-second proof expiry without invalid reports | Last observed count | Expired |
| Disconnect, reconnect, or a new boot | Unavailable until valid current-generation evidence | Invalid until valid current-generation evidence |

A newer valid report resets the invalid sequence. Valid 3↔1 changes publish
immediately. A new generation starts its own invalid sequence and never inherits
display availability from old-generation values. Initial unknown remains unknown.

Older reports are rejected against the report watermark, including valid reports
newer than the retained sample but older than the latest invalid report. Duplicate
invalid timestamps do not increment the counter. Conflicting values at one
previously valid timestamp invalidate proof once; subsequent same-time reports
cannot increment again or restore proof. Recovery needs a strictly newer valid
sample. Epoch, future-time and adapter-generation checks remain in place.

No observation store, background loop, expiry scan, timer framework or state
machine was added. Existing public attributes remain unchanged; neither the new
counter/watermarks nor acquisition timestamps are exposed as HA attributes.

## Audit of other OCPP observations

| Category | Findings and retained behavior |
| --- | --- |
| Omitted meter sample/channel | The normalizer yields nothing for an omitted channel. Existing channel data is not erased or republished. Invalid group timestamps are skipped. |
| Malformed voltage/current/power/energy sample | An identifiable invalid numeric sample becomes unknown for its own channel immediately. It fabricates no zero and does not overwrite other channels. Unsupported channels/locations/contexts are ignored. No evidence establishes the same transient phase-feedback defect for numeric channels, so no numeric debounce is added. |
| Exact numeric and cumulative energy changes | Existing unit conversion, exact comparisons, source ordering, statistics metadata and total-increasing semantics remain unchanged. Small real changes still publish immediately. |
| ChargingEnabled | Explicit invalid/rejected readbacks immediately make evidence unknown. Explicit true/false changes apply immediately. No invalid-report counter is used. A timeout does not create a new observation, renew timestamps or extend the original 15-second deadline; existing proof can only remain usable until that original deadline. The existing permission expiry timer is unchanged. |
| Connector and charging status | Explicit `Unavailable`, `Faulted`, charging/suspension and other mapped states apply immediately. Unrecognized status remains unknown; missing fields do not infer idle/zero/false. No status is buffered behind phase tolerance. |
| Control authority | Local/remote/unknown changes remain immediate. An invalid authority maps to unknown, never to remote authorization. |
| Verified capabilities | Inventory validation remains strict; invalid capability evidence becomes unknown/degraded immediately. There is no transient-value retention policy for capability proof. |
| Transaction/session observations | Omitted registers do not invent values; transaction sequence/time/generation fencing and current activity confirmation remain unchanged. Malformed or contradictory session energy invalidates accounting rather than fabricating an amount or zero; source streams remain separate. Session power still depends on current evidence. Historical completed sessions remain historical. |
| Disconnect/reconnect | All live channels lose current availability immediately. The single canonical store retains original last-known observations; a new generation requires valid new evidence. |

No additional observation category is changed: the audit establishes the concrete
phase display defect, not a reason to suppress other unknown/error states.

## Diagnostics and Recorder results

Approved WARNING severity and Level 1 lifecycle behavior are unchanged. Level 2
can show the phase proof becoming invalid and recovering, including same-value
recovery without a public display change. Same-time conflicts are logged as
unknown proof rather than as an accepted physical phase change. Repeated invalid
reports are deduplicated; no healthy periodic phase or raw meter logging is added.
The test with 2,002 consecutive invalid reports emits one invalid transition,
followed by one recovery transition when valid data returns.

Measured with real HA entity writes/events:

| Workload after initialized phase 3 | HA writes | `state_changed` | Unchanged `state_reported` | Estimated additional Recorder rows |
| --- | --- | --- | --- | --- |
| `3 → invalid → 3` | 0 | 0 | 0 | 0 |
| `3 → invalid → 1` | 1 | 1 | 0 | 1 |
| `3 → invalid → invalid → 3`, including 2,000 further invalid reports | 2 | 2 | 0 | 2 |
| Existing 2,000 identical measurements | 0 | 0 | 0 | 0 |
| Existing 2,000 identical phase reports | 0 | 0 | 0 | 0 |
| Existing one-hour active-duration simulation | 60 | 60 | 0 | 60 |

The existing exact-change benchmark still records all 2,000 voltage changes at
0.00000001 V steps and all 1,000 cumulative-energy changes at 0.00000001 kWh steps.
Recorder rows are estimates from state-change events, not a live DB measurement.
No HA state object is rewritten during a single-invalid/same-value recovery.

## Validation, footprint and compatibility

54 maintained Python tests pass (43 previously), including real WebSocket empty
phase messages, duplicate/out-of-order/conflicting timestamps, retained provenance,
new connection/boot generations, entry isolation, proof expiry, explicit device
faults, fail-safe permission and unchanged exact energy. All 8 JavaScript tests,
Ruff lint/format, JavaScript syntax, Python compilation and whitespace checks pass.
Five upstream HA/backoff deprecation warnings remain. The test infrastructure and
CI workflow are unchanged; all new tests extend existing test files.

Production diff: **65 lines added, 12 removed; net +53 physical Python lines**,
including comments and blank lines. Python production footprint changes from
18 modules / 2,718 lines / 98,231 bytes to **18 modules / 2,771 lines / 100,317 bytes**
(+2,086 bytes). Only `store.py` and `ocpp.py` change in production. There are no new
production modules. The frontend, manifest, identities, device registration,
statistics metadata, source normalizer, permission switch, session module and
native options are unchanged from beta.2.

The intentional public behavior change is confined to the first transient invalid
phase report: the last-observed display remains visible; two independent invalid
reports make it unavailable. No public attributes are added or renamed. A single
invalid report followed by silence can leave the last-observed display visible
while connected indefinitely, just as ordinary proof expiry does; it never keeps
usable proof. Repeated invalid reports are not hidden, and a disconnect still
invalidates availability immediately.

No real station raw-message capture was available, so this validates the receive
path and conservative handling rather than proving the station's lock contention
as the source of the live incidents. Real live CPU/Recorder-size effects remain
unmeasured. No station or live HA changes, active charging commands, release,
manifest-version change, tag or merge are part of this fix.
