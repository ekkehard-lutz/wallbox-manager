**Phase 2: passive Runtime simplification**

Base: Phase 1 merge `3b1525fbd0272068bdced9f3a7a0e71b72dcc804` on `develop`.
Feature branch: `refactor/passive-runtime-simplification`.
Requirements: Issue #2 and the three specification documents at
`16c9bfaa00467a632c62e9f0ce80b5f174532422`. The individually approved decisions
govern this change; consolidated V3 remains a draft.

**Removed work and ownership**

- Removed `GridProfiles.load()`'s unconditional one-second `optimum_refresh()`
  subscription, `PVOptimum.optimum_refresh()`, its SoC-transition wake path,
  `optimum_wait()`, and the now-unused `pv_input_gap()` helper. The existing
  authorized `pv_sequence()` remains the periodic owner of charging policy.
- Removed FAST's one-second override of the regulation clock. FAST and BALANCE
  use the configured interval and existing required validity deadlines. Existing
  regulator arithmetic, downward response, command validation, physical evidence,
  and bounded technical retry rules remain in place.
- Preserved independent daily obligations with `optimum_plan()`: forecast planning
  follows the existing approximately 300-second deadline and local midnight.
  Configured PV/load events retain first-surplus day evidence without calculating
  a charging target. The planner runs no SoC mode or power-regulator evaluation.
  It is scheduled only while an Optimum profile is configured.
- Replaced `selected_power_states()` and its complete HA sensor scan with
  `selected_power_observations()`, reading authoritative Session Ledger data.
  `SessionLedger.power_status()` now owns the attribution checks previously in
  the public session sensor. Both policy and the unchanged sensor use that owner.
  Exact normal meters, eligible transaction samples, parent precedence, ambiguity,
  transaction boundaries, invalid values, and provenance remain validated.
- Replaced the two economic global HA event filters with indexed subscriptions
  to the configured grid-import entity. EV economic evidence arrives directly
  from ledger notifications. Existing first-acceptance bookkeeping is reused;
  no observation cache or state machine was added. Control-context accounting
  callbacks remain because minimum/threshold changes require causal accounting.
- Exposed the matching Runtime snapshot before ledger callbacks run. HA can
  execute a background callback eagerly: previously a subscriber could see new
  ledger power with old Runtime charging status. The coherent publication order
  prevents a transient NETZ battery-reserve restore/reassert cycle.

All public measurement entities, their identifiers, device associations, and
statistics remain available. HA state writes continue through the public Entity
API, preserving normal reporting, registry, and lifecycle behavior.

**Execution inventory**

| Work | Before Phase 2 | After Phase 2 |
| --- | --- | --- |
| Unconditional cyclic PV-policy observer | 1 per entry with configured SoC, every second | 0 |
| Active periodic PV regulation loop | 1 per actively controlled target, alongside observer | 1 per actively controlled target |
| Observer-mediated SoC wake path | 1 | 0 |
| Extra sleep/event child tasks per active cycle | 2 | 0 |
| Ordinary HA/OCPP event → full PV-policy entry paths | 0 after Phase 1 | 0 |
| Independent forecast/midnight planning | Polled by one-second observer | One scheduled planning deadline |
| Economic HA listeners | 2 globally filtered listeners | 2 indexed listeners for configured import entity |
| Complete HA sensor scans for internal EV selection | Each selection | 0 |
| ChargingEnabled monitoring | Existing five-second read-only poll | Retained |
| Public duration/power and control-expiry timers | Existing per-entity timers | Retained |

The table distinguishes charging policy from daily forecast planning and economic
observation accounting. Explicit activation/recovery and asynchronous command
dispatch/confirmation checks remain separate from ordinary measurement intake.
Existing within-cycle debounce and live dispatch revalidation can still evaluate
policy; this change does not claim that every command requires only one function
call or that physical checks can be replaced by a cached tick result.

**Reproducible work counters**

Run `.venv/bin/pytest -q tests/test_passive_observation.py tests/test_passive_runtime.py`.

- Each passive event-storm case supplies 1,000 unrelated HA changes, 200 configured
  power changes, 100 Runtime meter updates, and a daily-planner callback. Local/OFF,
  Remote/OFF, and Local/ON all produce zero full PV-policy calls, zero regulation
  tasks, and no additional OCPP operations.
- The active storm supplies 100 HA and 100 Runtime updates while the existing
  regulator is parked. These produce zero policy calls between cycles and one
  `pv_edit()` on release of the next cycle, using the same task.
- Internal EV selection performs ledger lookups and zero complete HA sensor scans.
  Removing, duplicating, renaming, or corrupting synthetic HA output projections
  does not alter canonical EV power or retained-current accounting.
- Two simulated passive permission polls sleep for five seconds each and perform
  two `GetVariables` readbacks, without permission, authority, or profile writes.
- Public HA reporting remains tested separately: three identical publications to
  seven control entities produce 21 `state_reported` events, no `state_changed`
  events, and updated `last_reported`. A changed desired power changes only its
  corresponding state; provenance, invalidation, customization, and force-update
  behavior remain covered.
- The Runtime/ledger regression verifies that a ledger subscriber sees the matching
  charging-state observation and power from a single accepted observation batch.

These are isolated execution counts, not Home Assistant Core CPU measurements.
The historical 0.41%/9.07% samples remain motivation, not a measured improvement
attributable to this change. No live installation or wallbox firmware was accessed.

**Validation and verified size**

The final full Python suite passes: **2,691 tests**, with the same five dependency
deprecation warnings as Phase 1. The frontend suite passes **145 tests**. Ruff lint,
Ruff formatting, JavaScript syntax validation, and Git whitespace checks pass.
Against the Phase 1 merge, production changes add **198 lines**, remove **204**,
and reduce production by **6 net lines**. Tests separately add **635 lines**, remove
**165**, and grow by **470 net lines**. These small net production counts include
relocating shared attribution checks and adapting diagnostics to canonical observations;
the significant reduction is in repeated execution and competing scheduling paths.

**Remaining work and limits**

Passive observation retains necessary telemetry processing, HA output reporting,
permission readbacks, public duration/power freshness timers, and economic lifecycle
bookkeeping. No unconditional recurring charging-policy evaluation remains.
Runtime/ledger fan-out, repeated entity attribute construction, per-entity timers,
and duplicate economic refresh opportunities remain CPU suspects requiring profiling.

The existing economic arithmetic and gap/validity implementation, smoothing migration,
CP display-state simplification, and full reconnect bootstrap remain subsequent work.
This phase implements the observation/control separation of PV-ARCH-05–08 and its
PV-QA-01/02 verification; it does not claim complete implementation of every later
specification decision. PV-PLAN-02–04 planning duties and established OCPP, economic
STOP/restart, authority, transaction, generation, and physical-confirmation safeguards
remain applicable. The accepted startup delay and technical retry deadlines are kept.

Phase 2 requires review before merge. No release, tag, deployment, or change to
`main` or `wallbox-stationary` belongs to this phase.
