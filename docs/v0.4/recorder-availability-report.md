# Passive Recorder efficiency and availability

Base: `develop` / `v0.4.0-beta.1`, commit
`3a122fdc13f4144c5e26a8bbadb364a1c77c891e`.
Branch: `fix/passive-recorder-availability`. No manifest/version change.

## Publication and timestamp compatibility

The existing single observation dictionary accepts every valid newer observation,
including unchanged values. Source acquisition/receipt times, source, units,
generation, validity deadline and evidence status remain authentic internally.
Ordering and generation fencing apply before publication decisions. Exact numerical
changes, source/unit/status changes and first observations in a new generation
publish immediately. There is no rounding, quantization, tolerance or cumulative
energy suppression.

**Intentional public compatibility adjustment:** `observed_at` and `received_at`
are removed from observation-backed sensor attributes, including status,
capability and phase sensors, and from session power/energy projections. They are
not frozen or redefined as value-change timestamps. `source` and source-generation
attributes remain where applicable. HA `last_updated` and `last_reported` describe
HA publication, not OCPP freshness. Consumers must not use them as measurement
acquisition timestamps. Future internal regulation must read canonical observations.
Public `valid_until` is null on these sensors; it asserts no current physical proof.
The physical permission switch retains its exact observation/deadline attributes
and existing one-shot expiry behavior.

Avoiding redundant HA writes is the simplest and cheapest path: no repeated
attribute construction, HA write or `state_reported` event. An explicit comparison
with native unchanged HA writes confirms that native reporting avoids Recorder
changes but still performs one HA write and emits one `state_reported` per report.
No coordinator, scanner, additional observation cache or publication framework was
added. Session power compares a temporary before/after view around the canonical
measurement update, so real power changes still reach both projections.

## Availability and physical phase semantics

Disconnect retains the observation objects and their original generation and
provenance, while live measurements/status/physical phases become unavailable.
The connectivity sensor reports disconnected. Number/Select/Text preferences stay
available. Reconnect and BootNotification change the generation and leave retained
measurements unavailable until each channel receives valid current-generation
evidence, even if its value is identical. Old adapters, source timestamps predating
the current epoch, and late previous-generation messages cannot establish freshness.

Retained values can be inspected internally via `state.observations`; `state.value`
returns only current-generation, connected and unexpired evidence. No retained value
provides physical command authorization. Retention is in memory across transport
outages, not a new persisted measurement history across HA restarts.

“Last observed physical phases” retains the last reported valid count during the
current connection/boot generation after the five-second proof deadline. The
sensor explicitly represents a last-known observation and has no expiry timer.
`state.last_observed_value` projects that same observation; `state.value` separately
checks its proof deadline and returns no expired proof. Identical new reports
refresh the proof internally without history changes. A real disconnect/new boot
invalidates public availability until new evidence; a newly invalid/conflicting
observation makes the count unknown rather than inventing a phase count.

Completed session records retain their historical identity, timestamps, energy and
final duration across disconnects. Ongoing session power and duration need current
transaction confirmation; power additionally needs a valid current measurement.
Activity remains unknown until the real transaction is reconfirmed. Missing reports
never imply idle, zero or permission off.

## Session duration

The existing duration entity and seconds unit remain unchanged. Its existing HA
interval is now 60 seconds, only while a matching current-generation transaction
is active and connected. Disconnect, superseding scope, end and entity removal
cancel the timer. Lifecycle changes publish immediately, and ended duration is the
exact difference between authentic start and end times. No display tick persists
a session document. `duration_sampled_at` was removed; `started_at` is the actual
transaction start, and `duration_valid_until` remains null. The card displays the
sampled duration in minutes; second-by-second client rendering was not added.

## Passive options and diagnostics

Configure the one config entry to access two sections:

- General: diagnostic level and HA EntitySelectors for existing measurement
  mappings. Reserve uses Number/Input Number; SoC permits existing entity sources;
  power/energy/capacity references use Sensor.
- Regulation preferences: interval, SoC hysteresis, PV start/stop delay and the
  legacy seconds-based power smoothing window. These remain stored preferences.

Explicit central options retain precedence over profile values, then defaults.
Unrelated options and mappings absent from a partial edit are preserved. Explicit
empty source values clear that mapping. Existing selectors validate entity IDs and
number selectors preserve numeric ranges. Live SoC validation also uses the
existing Settings validation. Offline flows load legacy profiles for defaults.
Edits update local settings without reload, OCPP reconnect, regulator, command or
control timer. The legacy diagnostic boolean fallback remains false/absent → 0,
true → 3; explicit `diagnostic_level` wins.

Diagnostics are a small listener-local emitter, bounded by fixed event families:

| Level | Output |
| --- | --- |
| 0 (default) | Genuine protocol/task/connection errors only |
| 1 | Accepted connection, reconnect, disconnect, BootNotification with new generations, initialization completed/degraded and optional read failures |
| 2 | Additional real connector/charging/authority/permission/phase transitions |
| 3 | Deduplicated read operation and endpoint-result detail for troubleshooting |

Messages use `WBMGR subsystem=ocpp` followed by JSON containing station, generation,
event and reason. Opt-in events intentionally use WARNING severity so Level 1
appears under HA's default WARNING filtering without modifying global logger
levels; real errors use ERROR. The diagnostic level controls detail, not Python
severity. Explicit logger settings stricter than WARNING still filter these
messages. There are no meter payloads, credentials, transaction IDs, periodic
healthy-connectivity messages or unchanged-meter logs, including Level 3. Logging
collection/output failures are caught and cannot affect OCPP processing.

## Measured results

Real HA entities and event counters, without a live HA installation or Recorder DB.
Each row lists writes / `state_changed` / `state_reported` / estimated Recorder rows.
Recorder estimates assume the entities are recorded and exclude DB metadata costs.
Initial entity creation is excluded from repeated-report and duration workloads.

| Workload | beta.1 | Updated |
| --- | --- | --- |
| 2,000 identical numerical reports | 2000 / 2000 / 0 / 2000 | 0 / 0 / 0 / 0 |
| 2,000 identical phase reports | 2000 / 2000 / 0 / 2000 | 0 / 0 / 0 / 0 |
| One hour active duration | 3600 / 3600 / 0 / 3600 | 60 / 60 / 0 / 60 |
| 100 actual power changes | 100 / 100 / 0 / 100 | 100 / 100 / 0 / 100 |
| 2,000 exact voltage changes, 0.00000001 V steps | Not rerun | 2000 / 2000 / 0 / 2000 |
| 1,000 cumulative-energy changes, 0.00000001 kWh steps | Not rerun | 1000 / 1000 / 0 / 1000 |
| 2,000 native unchanged HA writes, alternative comparison | Not rerun | 2000 / 0 / 2000 / 0 |

Disconnect/reconnect still creates the necessary availability transitions: 75 HA
writes, 52 changes and 23 unchanged reports in the initialized synthetic workload.
These occasional lifecycle writes are deliberately retained. Static preferences
receive no telemetry writes; the other independent entry produces no writes in
the measured entry. Physical charging commands: zero.

The reported live 1,748 voltage rows/hour cannot be divided into timestamp-only
and actual numerical changes without the exact source observations. The synthetic
comparison proves both cases separately. No voltage fluctuations were rounded
away, and no live CPU, DB-size or hardware savings are claimed.

[Raw benchmark counts](recorder-availability-benchmark.json) include relevant
attributes and baseline-method notes. The baseline duration regression test
intentionally fails its new 60-change assertion after measuring beta.1's 3,600;
all tests on the updated implementation pass.

## Size and validation

Physical source lines including comments and blank lines, UTF-8 byte counts:

| Category | Before | After | Delta |
| --- | --- | --- | --- |
| Production Python | 18 modules, 2,463 lines, 88,430 bytes | 18 modules, 2,718 lines, 98,231 bytes | +255 lines, +9,801 bytes |
| Python tests | 5 files, 1,573 lines, 59,287 bytes | 6 files, 1,958 lines, 74,298 bytes | +385 lines, +15,011 bytes |
| Card | 350 lines, 26,928 bytes | unchanged | 0 |
| JavaScript tests | 126 lines, 6,373 bytes | unchanged | 0 |

[Machine-readable footprint](recorder-availability-footprint.json). The production
increase primarily restores native options and bounded OCPP diagnostics. There
are no new production modules, observation stores or timer frameworks.

Validation: 43 Python tests, 8 JavaScript tests, JavaScript syntax, Ruff lint/format
and whitespace checks pass locally using the CI workflow's commands. Python tests
use real local OCPP WebSockets; the sandbox's executor/socket behavior requires
running those tests outside the network sandbox. Five upstream HA/backoff
DeprecationWarnings remain. The existing five-second permission poll and its
public timestamp/deadline writes are intentionally retained. There is no active
regulation, no release/tag, no live HA change, no station modification and no
Recorder data deletion.
