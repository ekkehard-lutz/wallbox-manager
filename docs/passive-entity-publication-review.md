# Phase 3: passive entity and configuration overhead review

Base: `develop` at `9b528ca` (`v0.3.2-beta.16`).
Branch: `refactor/passive-entity-publication-overhead`.

This changes configuration-read and HA-publication work, not charging policy.
Applicable requirements are PV-ARCH-05–08 and PV-QA-01/02; existing PV-SOC,
PV-OCPP, phase, authority and economic guarantees remain applicable. The
architecture, current specifications and SoC/migration documentation were read.
PV-ARCH-09 was inspected on specification branch commit
`707e0a9c561d31c1adf49f38137a7dd81f03094d`: provisional regulation across a
disconnect remains a future requirement and is not implemented here.

## Source findings

The supplied profiles show 8,907/18,182 HA state calculations,
1,892/3,520 `ControlEntity._telemetry()` calls, 16,400/31,193 `setting()` calls,
and 50,199/95,481 `target_bounds()` calls in 60/120 seconds. These are call
counts, not exclusive CPU time, and must not be added together as independent
costs. The source explains the amplification:

1. `Runtime.observe()` accepts a measurement, updates the session ledger and
   publishes a station snapshot. All Runtime subscribers previously ran, even
   when their own input channel or prerequisites had not changed.
2. Every control entity's `_telemetry()` cancelled/recreated its expiry timer
   and wrote HA state. Its ledger subscription could independently write again
   for the same measurement. Every write rebuilt common control and profile
   attributes, even for configuration entities unrelated to the measurement.
3. Profile attributes call `setting()` through several helpers, including
   `grid_attributes()`/`grid_phase()` and `common_pv()`. Each `setting()` created
   a default dictionary, overlaid options, read the live battery reserve and
   projected all three SoC target fields. Reads could mutate saved settings.
4. Each projection called `clamp_target()` three times; each call rebuilt the
   same rational bounds. This explains the approximately three-to-one
   bounds/settings count in the supplied profiles. Number entity availability
   and min/max calculations also legitimately use bounds.
5. Every SessionEntity additionally inherited station-wide Runtime updates.
   A power measurement therefore rewrote session ID, timestamps, energy,
   duration and active entities as well as power. Two per-session interval
   callbacks existed even after session completion; inactive callbacks returned
   without writing, but still woke every second. During an active session,
   power was rewritten every second without a new source acquisition.
6. Platform discovery callbacks also repeatedly visited the device registry or
   resolved electrical capabilities on ordinary meter reports.

## Changes and preserved boundaries

- `setting()` now reads an existing configuration dictionary without defaults
  insertion, reserve reads or projection. Defaults are created at target
  initialization. Load, explicit edits, reserve input events and the existing PV
  target boundary normalize configuration. The execution boundary still reads
  live reserve and rejects missing/invalid/impossible input. This also handles a
  synchronous policy call before HA has delivered a reserve-change event.
  Reserve decreases expand editing bounds without restoring previously clamped
  preferences. Legacy targets, central-option precedence, migration and saved
  values remain covered by the existing tests. Configuration dictionaries are
  stable between actual changes; there is no configuration polling/cache task.
- `clamp_settings()` calculates the shared bounds once for all three fields.
- Runtime subscriptions can select their dependencies directly from the old
  and new immutable snapshots. There is no notification queue or second station
  store. Unfiltered internal subscriptions keep their existing behavior.
  The old snapshot is explicitly supplied after coherent Runtime/ledger intake;
  otherwise the pre-published new snapshot would hide real changes.
- Control and profile Runtime subscribers depend on lifecycle, authority,
  permission, capabilities, phase, voltage, physical states and actual positive
  flow transitions. Power/energy accounting arrives through the existing ledger.
  The unchanged `actively_charging()` helper moved into the generic core and is
  still exported at its previous import location. Current-only positive/zero
  transitions therefore still refresh the actual-charging display.
- Control ledger notifications write only for a changed transaction/activity
  projection. Profile-owned economic attribute changes explicitly publish after
  accounting, independent of subscriber order. Ordinary meter intake does not
  rebuild full control/configuration attributes merely because a report arrived.
- Measurement entities select their own channel, retaining timestamps and
  provenance, plus their actual lifecycle/display dependencies. Discovery and
  current-default initialization select inventory/metadata dependencies.
- Session projections compare their own lifecycle/value dependencies. Power and
  energy also observe relevant exact/parent Runtime meter changes: an invalid
  parent sample can change measurement selection without changing the immutable
  session record. This path must immediately publish unknown power, not wait
  for a timer or another ledger change. Runtime/ledger notifications for an
  already-published sample do not cause a second state write.
- Active duration retains its one-second update. Completed duration has no
  recurring timer. Power has one actual source-expiry deadline, with no periodic
  refresh. Controls retain their existing validity deadlines, now also covering
  parent-EVSE voltage inputs, and do not cancel/recreate an unchanged deadline.

No entity IDs, unique IDs, device associations, attributes, units, state classes
or history/statistics configuration were changed. All state writes use
`async_write_ha_state()`. Explicit same-valued `control.publish()` calls still
produce `state_reported`, update `last_reported`, respect customization and
preserve `force_update`; new same-valued measurement acquisitions still publish
their new provenance. Irrelevant measurements no longer manufacture a control
report. Explicit zero-point physical-validity invalidation bypasses subscription
filtering, including when snapshot values themselves are unchanged.

## Deterministic before/after measurement

Run `.venv/bin/pytest -q -s tests/measure_entity_publication.py`. For the baseline,
export `git archive v0.3.2-beta.16` to a temporary directory, copy this benchmark
file into its `tests/` directory and run it there using the same Python environment.
The benchmark is compatible with both versions; it is separate from normal
regression collection. It attaches seven common control-attribute projections
to the real Runtime/ledger, with Local authority and configured PV references,
and supplies 2,000 fresh, same-valued zero-power acquisitions in one active
transaction. Timer construction is included; OCPP test sockets are simulated.
It measures the shared attribute path, not every optional ProfileNumber property.

| Operation | beta.16 | Phase 3 |
| --- | ---: | ---: |
| Accepted fresh power reports | 2,000 | 2,000 |
| Control telemetry callbacks | 14,000 | 0 |
| Control attribute rebuilds, including ledger fan-out | 28,000 | 0 |
| `setting()` calls, including internal bookkeeping | 130,000 | 8,000 |
| `clamp_settings()` calls | 130,000 | 0 |
| `pv_soc.target_bounds()` calls | 390,000 | 0 |
| Additional OCPP operations | 0 | 0 |

`setting()` calls fall by about 93.8%; the remaining calls are cheap reads in
existing ledger bookkeeping. A separate regression performs 5,000 setting and
profile-attribute reads and verifies zero reserve reads, zero projections, zero
bounds calculations, and unchanged saved configuration. Expiry regression makes
1,000 unchanged-deadline scheduling requests and observes zero replacement timer
registrations. These counts establish removed work, not a percentage reduction
in live HA CPU. Active economic changes, voltage-dependent limits and genuine
permission/authority/phase transitions can and must still publish controls.

## Activity/Logbook investigation and attribution limit

The actual 60/120-second profiler files, live Recorder database, query plans,
Logbook WebSocket timings and live HA version were not supplied. The reported
25–56% panel-open spike is reproducible user evidence; its exclusive CPU owner
cannot be identified from the supplied aggregate counts alone.

The installed test environment uses Home Assistant **2026.8.0**. Its source
provides a distinct path for panel queries: `components/logbook/websocket_api.py`
dispatches historical work through the Recorder executor;
`logbook/processor.py::EventProcessor.get_events()` opens a read-only Recorder
session, resolves metadata/event types, executes the selected SQL query and
processes rows/context. `logbook/queries/devices.py` includes device-context
subqueries/unions and `queries/common.py` filters continuous domains; the
processor also skips continuous sensors. The live installation may differ.
Opening Activity does not call a WBM API or request a WBM policy calculation.

Recorder and publication load must be distinguished. In that HA version,
`recorder/core.py::Recorder.async_initialize()` listens to `MATCH_ALL`, while
`homeassistant/core.py::EVENTS_EXCLUDED_FROM_MATCH_ALL` excludes `state_reported`.
Identical ordinary state writes therefore consume HA publication work without
necessarily creating Recorder rows. Changed source timestamps, validity or other
attributes can create `state_changed` rows even if the displayed value is the
same. Real telemetry, permission provenance and active duration still create
legitimate history; this change preserves it. It does not erase historical rows
already present when the panel queries the database.

WBM's proven contribution here is unnecessary entity work and duplicated
publications. The source also establishes a separate HA-owned query/rendering
path; it does not prove that Recorder, WBM history, or an unrelated HA problem
accounts for the measured spike. A later authorized live comparison should
separately count `state_reported`/`state_changed` by entity during closed/open
panel windows, capture executor/SQL stacks and query timing/row counts, and
compare equal history windows before/after the candidate. Sustained WBM event
volume, panel-triggered database CPU and unrelated tasks can then be separated.
Database/query/index fixes belong to HA if that capture identifies them; no
Recorder/Logbook disablement, entity hiding, exclusion or private HA API workaround
is introduced by this integration.

## Verification, size and remaining periodic work

Targeted regressions cover 2,000 current, power and energy reports in each of
Local/OFF, Local/ON and Remote/OFF; Local wallbox permission toggles; phase and
voltage updates; disconnect/boot/reconnect and old-token rejection; stable
configuration reads, changing/missing battery reserve and central H edits;
session start/stop, inactive timers and actual power/permission expiry; per-channel
same-valued/force-update reporting and explicit physical invalidation. The
existing active-PV parked-cycle storm now supplies 2,000 HA and 2,000 Runtime
updates, with no policy computation between cycles and exactly one normal edit
on release. Existing invalid-parent-meter, accounting, phase, command-fencing,
startup/reconnect, ownership and PV algorithm suites remain required.

Against beta.16, production Python adds **354 lines**, removes **83 lines**, and
grows by **271 net lines**, including relocation of the unchanged flow helper.
The additional selectors, explicit initialization and lifecycle-owned expiry
handling replace repeated execution; this is not a source-line reduction claim.

Final checks pass: **2,708 Python tests** (188.90 seconds, five existing dependency
deprecation warnings), **145 frontend tests**, Ruff lint, Ruff formatting,
JavaScript syntax validation and Git whitespace checks. The separately invoked
deterministic benchmark passes and prints the Phase 3 counts above. Tests ran
against the simulated OCPP peer with local socket access; no live HA or hardware
was accessed. Live HA CPU and Logbook-query measurements remain a separate step.

Remaining periodic work is intentional: the unchanged five-second read-only
ChargingEnabled poll, existing WebSocket liveness, active configured regulation
and bounded recovery/retry work, forecast/midnight planning when applicable,
active-session duration updates, and actual permission/phase/voltage/power expiry
deadlines. No permanent polling task was added. Local observation performs no
charging-control or full PV-policy loop. Reconnect generation fencing and
initialization remain unchanged; there is no queued reconnect command or new
reconnect calculation cadence. No version, tag, release, deployment, hardware,
`main`, `develop` or `wallbox-stationary` change belongs to this branch.
