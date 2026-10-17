# Phase 4: passive ownership and control publication review

Base: `8817a827e5d5baa9c9134b34fd02f76e80a12a71` (`v0.3.2-beta.17`).
Branch: `codex/passive-ownership-control-publication`.
Scope: passive publication work only; no changed PV/charging algorithm,
configuration normalization, transport behavior, version or release.

## Verified source causes

`ProfileOwnership.register()` subscribed an unfiltered lambda to Runtime.
Every publication therefore called `changed()`, which called `publish()` even
when readiness/context/status/record were unchanged. Its `controls=False` flag
only prevented control broadcasts: it still notified every `ActiveWallbox`.
HA then evaluated `options` and attributes, calling `inventory()` three times.
The mixed baseline reproduces this exactly: **191 publications, 191 ownership
callbacks, 573 inventory builds**. It includes real registry lookups and the
actual HA entity platform, rather than mocked attribute getters alone.

Each control's bound `runtime_view()` separately called `control_view()` on
both snapshots. That projection included complete phase, permission and
voltage observations, so new acquisition/deadline timestamps rewrote all
controls. Those state writes repeatedly entered capability resolution through
control attributes and dynamic number limits. The baseline reproduces
**3,820 telemetry callbacks for 191 publications and 20 controls**, consistent
with the supplied live ratio of 4,114 callbacks to 187 publications for 22 controls.

## Removed and simplified paths

- Ownership Runtime input now selects connection/boot token, connected state,
  identity, connector inventory, authority/revision and permission value/revision.
  Meter and same-valued phase/permission refreshes do not call `changed()`.
  Readiness transitions and live command fences still read canonical Runtime.
- Removed unconditional ownership listener publication from unchanged and
  in-progress `changed()` paths. Explicit activation still publishes its
  transition and final result. Inventory changes have a separate small existing
  Runtime subscription; public config-entry update listeners cover configured
  subentries. Registry callbacks check the affected entry and visible naming
  fields, including identifiers from device-removal events.
- `ActiveWallbox` compares its last visible ownership/inventory view before
  writing state. This is an entity presentation comparison, not authoritative
  ownership or telemetry storage. Registry names, offline configured inventory,
  connection changes, selection, readiness and recovery status remain observable.
- Ordinary controls share the same pure control selector. Runtime evaluates a
  shared selector once for each old/new snapshot within that publication, then
  discards the local results. There is no persistent selector cache, invalidation
  state machine, new notification service or additional polling task. Callable
  selectors need not be hashable.
- The control projection compares values, permission revisions and validity,
  rather than acquisition timestamps. Actual voltage, phase, authority,
  permission, capability, transaction/activity and generation changes still
  update presentation. Existing explicit `control.publish()` semantics remain.
- Existing control expiry timers have a lightweight deadline-refresh listener.
  This updates the same timer when evidence is renewed or shortened without
  rebuilding HA attributes. The semantic callback no longer duplicates timer
  scheduling. There is still one existing expiry timer per control entity.

## Physical evidence and public reporting

All accepted observations, acquisition times, source labels, revisions and
deadlines remain in the canonical Runtime; the intake and adapter confirmation
code is unchanged. Same-valued `read_physical_mode()` readbacks immediately
replace the phase evidence used by command guards. A focused real-OCPP-peer
test verifies the newer timestamp/source while an unrelated power-intent entity
performs zero state writes. No HA publication is a prerequisite for confirmation.

The confirmed `ChargingEnabled` hardware entity continues publishing each new
permission readback and phase acquisition deadline. Voltage/current/power/energy
measurement entities retain their acquisition provenance. Other intent/config
entities retain all attribute names and return current data when read/published,
but do not continuously rewrite their last HA snapshot solely to renew a phase
timestamp. Consumers needing current acquisition timing use the hardware and
measurement projections, rather than an older intent publication's context.
Explicit control publication refreshes that context as before.

Validity expiry still writes state. Tests cover an unchanged permission value
with a newly shortened deadline, then actual unknown-state publication across
all controls. Same-value refresh after a validity lapse is a semantic change;
generation changes invalidate old proof. Explicit zero-point invalidation still
bypasses selector filtering. Existing authority loss, asynchronous command,
phase confirmation, ownership recovery, session and reporting suites remain in
force. Five-second permission polling, 20-second WebSocket Ping/Pong and all
OCPP commands are untouched. No entity identity, unit, device association,
history/statistics setup or public attribute was removed.

## Mixed before/after benchmark

Run `.venv/bin/pytest -q -s tests/test_passive_mixed_publication.py`. To reproduce
the baseline, export `git archive v0.3.2-beta.17`, copy that test into its `tests/`
directory and run it with `WBM_MEASURE_ONLY=1` and the same Python environment.
The measurement-only flag skips performance assertions, not the workload.

The simulated 120 seconds submit **56 MeterValues batches** (all three voltages,
current, power and energy), **108 phase observations**, and **23 permission
readbacks**, with repeated and changed values, physical phase/permission/authority
transitions, an active real ledger transaction, disconnect/reconnect, boot and
fresh Local authority. One meter, two phase and one permission acquisition occur
during the disconnected interval and are rejected in both versions. Thus 183
accepted acquisitions plus five authority and three generation publications
produce 191 Runtime publications. All 20 controls and real HA measurement/session
platforms are registered before counting. Duration is driven once per simulated
second, with real-time interval callbacks cancelled in the benchmark only.

| Work | beta.17 | Phase 4 |
| --- | ---: | ---: |
| Runtime publications | 191 | 191 |
| Ownership callbacks | 191 | 11 |
| Ownership inventory builds | 573 | 8 |
| `control_view()` | 8,022 | 764 |
| `actively_charging()` | 9,038 | 1,162 |
| `resolve_capabilities()` | 3,408 | 936 |
| Control telemetry callbacks | 3,820 | 721 |
| Common control attribute builds | 3,994 | 895 |
| HA state writes (changed + same-valued reports) | 4,844 | 1,556 |
| HA changed-state events | 3,011 | 1,325 |
| Extra lightweight expiry selectors / refresh callbacks | 0 / 0 | 7,640 / 3,720 |
| Inventory builds in the settled unchanged-traffic probe | 9 | 0 |
| Unrelated control writes in that probe | 57 | 0 |
| Actual OCPP control commands | 0 | 0 |

These are work/event counts, not exclusive CPU timings or live HA CPU results.
Deadline selection and timer renewal remain work: the table explicitly includes
them. Changed-voltage presentation, raw hardware/measurement provenance, genuine
control changes and active duration still publish. Recorder/Logbook query CPU
is not claimed resolved, and no Recorder or entity exclusions were introduced.

## Validation and size

Production changes add **127 lines**, remove **24**, and grow by **103 net
lines** against beta.17. Tests add **573 lines**, remove none, separately from
production and documentation.

Final validation passes: **2,714 Python tests** in 189.02 seconds, with five
existing dependency deprecation warnings; **145 frontend tests**; Ruff lint and
formatting; JavaScript syntax validation; and Git whitespace checks. The mixed
candidate benchmark was repeated with identical call/event counts. Focused tests
cover refreshed proof, shortened deadlines, immediate asynchronous phase
readback, registry naming/removal, configured inventory and shared selectors.
No live installation or hardware was accessed. Commit/push is limited to the
feature branch; merge, tag, release and deployment remain separate work.
