# PV economic journal recovery review

This branch addresses pre-release audit finding F3. The verified baseline is
`origin/develop` at `3a2dd08208f24a90e4e0d8221e2314a0e0df3a92`, whose parents
are the beta.22 develop commit and the reviewed F1 fix
`6111d6acc48c25555cddbd4595d58fb7cd86c25a`. The merge tree is identical to
the F1 fix tree. [Exact develop CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42403790943)
· [F1 CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42403258181)

## Root cause and Store semantics

The baseline `pv_economics.py:28–31` used `if not record` to initialize a
budget with no scope or prior STOP. Baseline `pv.py:124–144` passed
`record.get("economics")` to restore, making a missing key and an explicit
null indistinguishable. A recovered budget with no scope could then bind to
the first eligible transaction. The damage could erase effective STOP
provenance and let the regulator request a fresh positive point.

The installed Home Assistant Core 2026.8.0 implementation was inspected at
`homeassistant/helpers/storage.py:304–467` and
`homeassistant/util/json.py:61–81`. `Store.async_load()` unwraps a versioned
envelope and can return `None` for several reasons. A cache miss or a missing
file can produce absence. A stored null or a bare empty JSON object can also
produce `None`. On a JSON decode error, Store can rename the file to
`<key>.corrupt.<timestamp>`, create an HA repair issue and return `None`.
Unreadable files, malformed envelopes and unsupported major versions can
raise instead. The normal Store load/write contract remains in use.
[Home Assistant Store source](https://github.com/home-assistant/core/blob/2026.8.0/homeassistant/helpers/storage.py#L304-L467)

[`load_journal()`](https://github.com/ekkehard-lutz/wallbox-manager/blob/4d2992e66ad42605f768dc4c7d6fa49f256fbcf2/custom_components/wallbox_manager/pv_migration.py#L13)
checks the storage directory only when Store returns `None`. It uses the
public `Store.path` and runs the directory check in HA's executor. A matching
file name or its HA quarantine name is evidence of existing history. Load
errors and filesystem access errors take the invalid path. Only absence of
both names produces the internal missing-source marker. A good current
payload takes precedence over old quarantine or legacy files. Corrupt current
history never falls back to older economic data.

## Recovery decision matrix

| Recovery case | Baseline behavior | Behavior with this fix |
| --- | --- | --- |
| First installation, current and applicable legacy economics absent | An empty budget could bind on the first eligible tick. | Same outcome, after checking absence. Valid legacy day-only data can restore planning without introducing economic history. |
| Valid current journal | Restored Wh and STOP, discarded the reset evidence and accounting endpoints at startup. | Same behavior. The written schema remains version 1. A valid saved unbound budget has zero Wh, no STOP or reset, and the existing first-install, operator Stop or NETZ-exit provenance. |
| Existing current record with falsey, missing or malformed economics | Falsey or missing economics could initialize a fresh budget. Some truthy malformed data was already rejected. | Budget restoration validates the complete economics record, version, scope, finite nonnegative Wh, STOP, reset and cause types. Existing damage takes the unreconciled path. The zero-valued field in the existing marker is a placeholder that cannot bind as a fresh allowance. |
| Current envelope or file invalid, unreadable, or quarantined by HA | Some failures aborted setup. A `None` result could try legacy data or initialize fresh. | Evidence of current history blocks fallback. An invalid or unreadable current load stays unreconciled, including on a subsequent startup with only HA's quarantine file present. |
| Valid beta.18 migration | Recognized entry/connector/station and transaction were imported, including Wh, STOP and valid planner phase. | Same behavior. A recognized `records: []` and a genuinely absent economic source can still initialize a fresh budget. |
| Invalid beta.18 history | Nonempty invalid economic payloads typically entered legacy unknown recovery. A wrapped null could look absent. | Existing invalid legacy files, including wrapped null, are unreconciled. A malformed optional day document supplies no planner state. It cannot replace valid economics or invent economic history. |
| Legitimate reset or scope replacement | Explicit Stop and NETZ exit reset the budget. A recorded transaction end could close a scope, including if its ID happened to match the unknown-history marker. | Explicit Stop, NETZ exit and genuine matched transaction ends retain the existing behavior. End reconciliation additionally requires the saved connector and authority prefix. An unknown marker or another connector's scope cannot satisfy that check. |

[`Budget.restore()`](../custom_components/wallbox_manager/pv_economics.py#L29)
now treats every argument as existing economic data requiring validation.
[`PV.start()`](../custom_components/wallbox_manager/pv.py#L124) leaves the fresh
budget created by `PV.__init__()` in place only after proven current-source
absence and a recognized empty or absent legacy economic source. The
`economic_scope_unreconciled` diagnostic already used by the active tick
continues to report failed reconciliation. WBM adds no journal payload
logging, diagnostic entities, or recurring I/O.

## Compatibility and invariants

- [PV-ECO-01–09](specifications/pv-regulation.md#pv-eco-01--scope-and-priority-approved)
  govern the configurable Wh threshold, last-value-held sampling, strictly
  greater than 15-second reset interval, STOP provenance, and existing
  conditional restart rules. This branch changes recovery validation.
  Accounting, SoC policy, battery-free SURPLUS and FAST/BALANCE decision
  logic are preserved. Restore never recreates previous samples or downtime.
- [PV lifecycle](../custom_components/wallbox_manager/pv.py#L269) still records
  an observed current-generation transaction end. A saved matching scope and
  station identity can resume under the same transaction. Mismatches cannot
  authorize an automatic positive point. [Explicit Stop](../custom_components/wallbox_manager/pv.py#L497)
  retains its reset and durable write. Existing checkpoint fallback, journal
  lock, cancellation shielding and post-write verification remain in use.
- [WBM-ARCH-01–12](specifications/architecture-responsibility.md) govern the
  boundary. This change adds no hardware safety logic, OCPP messages,
  interlocks, timers, watchdogs or polling. Stationary retains exclusive
  physical safety ownership.
- The historical cross-check used `v0.3.2-beta.18`
  (`87963014c9af73416c438807f523a3da2a98bd22`),
  `pv_economy_storage.py:179–204,242–265`, for validated legacy
  identity and conservative recovery. The openWB source cross-check used
  `849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
  `packages/control/chargepoint/chargepoint.py:557`, for the separate
  charge-point current-limit layer. It does not supply this integration's
  economic journal format. [Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py)

## Regression and validation evidence

- [New restoration and Store tests](../tests/test_pv_journal_recovery.py)
  cover invalid fields and payloads, null envelopes, raw corruption and repeat
  startup after HA quarantine, simulated unreadability, valid zero and stopped
  records, first-install and empty/day-only legacy startup, and valid and
  invalid legacy import. Simulated peers cover station and transaction
  mismatch, positive first-install charging, damaged-history inhibition,
  explicit Stop, reload with STOP retained, and matched transaction-end reset.
- [Existing runtime](../tests/test_pv_runtime.py) covers conditional restart
  thresholds and reload without a new release. [Existing checkpoint tests](../tests/test_pv_review_regressions.py)
  cover failed durable writes and checkpoints, preserving newer STOP
  provenance and draining a cancelled save before a newer write. [PV planning
  tests](../tests/test_pv_planning_release.py) cover persistence failure
  reporting and read-only planning. [Delta compatibility](../tests/test_delta_compatibility.py)
  and [identity migration](../tests/test_identity_migration.py) were included
  in the focused run.
- Against an isolated archive of the exact F1 merge, the new damaged-journal
  test failed at the expected assertion. The simulated peer received a 9 A
  request on the first eligible tick. On the fix branch that tick sends no
  positive profile and reports unreconciled history.

The activated local environment used Python 3.14.7 and HA Core 2026.8.0.
The full invocation was `python -m pytest -q`, with coverage enabled through
`PYTEST_ADDOPTS`. The Stationary companion contract used the read-only
checkout at `279287c3c1d8863e803144eccf773ab3f10889a5` with mocked hardware.

| Gate | F3 branch | Validated F1 baseline |
| --- | --- | --- |
| Full Python | 1,061 passed, five existing dependency warnings, 255.50 s | 987 passed, five warnings, 247.82 s |
| Statement coverage | 93.97%, 4,191 of 4,460, 269 missed | 93.83%, 4,153 of 4,426, 273 missed |
| Focused recovery and migration | 361 passed, five warnings | Different test selection |
| JavaScript syntax and frontend | Passed, 95 frontend tests | Passed, 95 frontend tests |
| Headless Chrome | One test passed, 96 layouts and 48 action/theme combinations | Same |
| Ruff lint and format, translations, manifest/HACS, review links and diff whitespace | Passed, including 159 translation keys and three service field sets | Gates passed |

## Changed files and limits

- `pv.py`, `pv_economics.py`, and `pv_migration.py` contain the scoped recovery changes.
- `tests/test_pv_core.py` uses the complete persisted scope shape in one existing fixture. It retains the STOP and scope assertions.
- `tests/test_pv_journal_recovery.py` and this report contain new regression evidence.

Byte comparison with the F1 tree verified that 216 of 220 existing files
are identical. This includes the F1 implementation, its tests and report,
the public HA entity, config and service definitions, the frontend,
translations, the versioned manifest, dependency pins, HACS metadata and
release documentation. The persisted PV schema and profile settings retain
their existing formats.

Store and file evidence can distinguish supported cases only while that
evidence exists. A deleted journal and deleted quarantine/legacy evidence are
indistinguishable from a first installation. Concurrent external editing of
HA storage is outside this contract. The quarantine check follows the naming
convention verified in HA Core 2026.8.0. A syntactically valid journal that
deliberately fabricates otherwise consistent values is beyond field validation.
The established explicit Stop and scope retirement paths remain the recovery
mechanisms. The installed runtime and operator-controlled physical acceptance
are separate from these simulated tests.
