# PV economic transaction rollover review

The Phase B baseline is the reviewed R1–R3 merge on `origin/develop` at
`60859f5e1591411689bfe3efd04977aa1d3fe094`. Its tree matches reviewed tip
`b416ecd5fe62b91bed4b27e694705ce752c45068`. The merge passed all 1,126 local
Python tests and the static and frontend checks before push.
[Exact develop merge CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42625373556)

## Decision and implementation

The operator approved a per-transaction import budget for private charging
optimization on 9 November 2026. Billing and financial settlement are
outside its scope. The operator accepts that an interrupted transaction
or Stationary restart can occasionally grant another allowance.
[PV-ECO-07 amendment](../specifications/pv-regulation.md#approved-pv-transaction-rollover--9-november-2026)

Before this change, a valid newly observed ID without matching `Ended` for
the prior economic transaction left a valid old budget unreconciled. A
same-transaction resume retained its balance and STOP as intended. The
new rule applies only after PV has reached its existing eligible accounting
path on an ordinary tick. `Budget.bind()` retains an identical scope, starts
an unbound budget as before, and permits a different ID on the same
station/EVSE/connector/Remote binding to replace the prior budget. The
replacement starts with zero Wh, clear STOP/reset/cause and unset interval
endpoints before its first sample. No Ended requirement remains for this
rollover. [Budget binding](../../custom_components/wallbox_manager/pv_economics.py#L77)

PV uses the current epoch, live registered adapter, exact connector-scoped
transaction and existing permission and station-identity checks before
binding. The already supported matching Ended case retains its existing
station-identity retirement behavior. A different saved binding or damaged
or explicitly unreconciled scope remains inhibited. The canonical session
handler retains its scope, generation, timestamp, sequence and retired-ID
checks. An OCPP notification alone adds no tick, journal format or positive
proposal. [PV scope and identity](../../custom_components/wallbox_manager/pv.py#L710) · [Canonical session checks](../../custom_components/wallbox_manager/session.py#L67)

The existing journal lock, write verification and pre-dispatch persistence
continue to govern a positive proposal. A failed new-scope write inhibits
that proposal. R3 permission-gap handling and the strictly greater than
15-second low-import reset remain unchanged within a transaction. The
runtime changes are confined to `pv.py` and `pv_economics.py`. The test changes
cover rollover, journal recovery and checkpoint invariants. The requirements
change is confined to PV-ECO-07, its earlier Q1 reference and the index.

## Source cross-check and limits

Stationary `279287c3c1d8863e803144eccf773ab3f10889a5`,
`ocpp_runtime/transactions.py` lines 132–174 and 214–235, reports the existing
durable ID on `Updated/TxResumed` and creates a fresh ID on `Started`. The
sequence is reserved durably before transmission. WBM consumes those
identities without inferring a cable event. The actual openWB source at
`849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
`packages/control/chargepoint/chargepoint.py` lines 154–188, separates
local eligibility from the unplug-driven stop path. The applicable
`v0.3.2-beta.18` cross-check at `87963014c9af73416c438807f523a3da2a98bd22`
was the separation of physical feedback, manager commands and station
ownership in `docs/phase-lockout-protocol.md` lines 23–44.
[Pinned Stationary source](https://github.com/ekkehard-lutz/wallbox-stationary/blob/279287c3c1d8863e803144eccf773ab3f10889a5/ocpp_runtime/transactions.py) · [Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py#L154-L188)

Stationary remains the exclusive physical and electrical safety owner. The
change adds no control loop, timer, polling, command, safety logic or
historical format conversion. Physical acceptance remains operator-controlled
and includes real same-ID resume, new-ID restart, and truthful behavior if
Stationary evidence is unavailable.

## Regression evidence

On the exact pre-fix merged baseline, four of the 11 new tests failed at
the old transaction-end requirement. The 40 Wh and STOP cases retained the
previous scope, including after a current-format reload. The journal-write
failure case could not reach new-scope persistence. The controls passed.

The permanent tests exercise a valid Ended, same-ID resume with Wh and STOP,
duplicate and newer same-ID reports, stale and previous-generation input,
wrong station/EVSE/connector, valid reload, corrupted or quarantined history,
explicitly unreconciled history and durable write failure. The updated F3
matrix distinguishes eligible new IDs from a foreign saved binding or
station identity. The existing strict economic restart, F1/F3/F5 and R1–R3
suites exercise the surrounding behavior.
[OCPP rollover tests](../../tests/test_pv_transaction_rollover.py) · [F3 journal tests](../../tests/test_pv_journal_recovery.py) · [Checkpoint tests](../../tests/test_pv_review_regressions.py)

## Validation

- Complete `python -m pytest -q` with integration coverage enabled through
  `PYTEST_ADDOPTS` passed **1,141 tests**. Statement coverage was **94%**
  overall, **96%** for PV and **97%** for PV economics. The run reported five
  dependency deprecation warnings.
- The focused recovery and lifecycle selection passed **531 tests**. It
  covered the 11 new replays and the R1–R3 and F1/F3/F5 regressions.
- Ruff lint and formatting, JavaScript syntax, all **95 frontend tests**,
  headless Chrome, manifest and translation validation, **113 local
  documentation links and anchors**, and Git whitespace checks passed.
  Validation used isolated HA fixtures and simulated OCPP peers.
