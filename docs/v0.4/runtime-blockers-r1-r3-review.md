# v0.4 runtime blockers R1–R3 implementation review

The exact source baseline was `origin/develop` at
`d43db890a2f0335763ebcf3e66e0e36a4ed800e0`. The findings came from the
documentation-only audit at `4e54c2dcbdfa06fd2a48df907d3341db14e1edc3`.
[Exact baseline CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42598585170) · [Pinned reconnect audit](https://github.com/ekkehard-lutz/wallbox-manager/blob/4e54c2dcbdfa06fd2a48df907d3341db14e1edc3/docs/v0.4/reconnect-runtime-audit.md)

## R1 · Same-transaction NETZ resume

A connection or Boot generation change discarded Grid's remembered transaction
ID. A resumed ID then looked like a new transaction, which gave the active
schedule permission to issue a positive point without any valid applied-point
readback. On the exact baseline, the rejected and wrong-scope composite cases
both resent 3p/16 A.

Grid now retains the last known transaction ID while generation transitions
still clear dispatch authorization and fence command outcomes. The existing
composite reconciliation confirms a matching point without a write and can
correct a valid mismatch. A same-ID resume with rejected or invalid evidence
stays unconfirmed. A genuinely new transaction or a fresh authorized action
retains its normal path. An independently matured delayed business window can
issue its first proposal, including after an offline start. Once Grid
recognizes the matching offer or sends the positive proposal, the window
cannot grant a second write on resume. The original absolute end is preserved.
Stop issued before transaction recovery relies on the existing supported
retained-offer reconciliation for its zero-current write.

[Grid change](../../custom_components/wallbox_manager/grid.py) · [NETZ replays](../../tests/test_netz_resume.py) · [WBM-GRID-13](../specifications/grid-profile.md) · [WBM-TIME-05–07](../specifications/operational-display-and-timing.md)

## R2 · Teardown after journal failure

Setup-error cleanup, HA Stop and unload previously awaited the final PV
journal write before closing the other owners. An injected write failure
escaped before Grid, battery and listener cleanup. On the exact baseline,
an old adapter and its listener could survive after HA platforms were removed.

The entry runtime now owns a shared shutdown task. It fences Grid, closes the
battery owner and listener, and drains the adapter before final PV and
applicable session persistence. Each cleanup is attempted even when an
earlier one fails. A single failure propagates as itself, and multiple failures
are grouped. Setup retains its primary failure with cleanup failure in the
exception chain. Cancellation of a lifecycle caller joins the shielded
shutdown, and partial setup before journal recovery cannot overwrite an
existing PV record with defaults. The journal's existing lock, verification
and data format continue to apply.

The tests observe the original `OSError`, old socket closure, cancelled
pending ACK handling, cleared subscriptions and timer, successful rebinding of
the same port, session-save attempt on HA Stop, cleanup after platform failure,
and preservation of a saved STOP when setup is cancelled during load.

[Entry lifecycle](../../custom_components/wallbox_manager/__init__.py) · [Adapter shutdown](../../custom_components/wallbox_manager/ocpp.py) · [PV journal](../../custom_components/wallbox_manager/pv.py) · [Teardown replays](../../tests/test_runtime_teardown.py) · [WBM-CTRL-10](../specifications/active-ocpp-control.md)

## R3 · PV economic evidence across a permission gap

Grid eligibility covers live Remote authority and transaction scope. The PV
lifecycle callback previously used that eligibility alone to interrupt
economic timing. An observed false/unknown permission and return to true
between ticks left the previous import sample and low-import clock intact.
The exact baseline tests reproduced both the retained sample and a premature
positive restart.

The existing PV lifecycle subscription now invokes `Budget.interrupt()` when
the canonical permission is false or unavailable. The next eligible tick
establishes a new interval. Accumulated Wh, STOP cause, durable history and
already qualified reset evidence are retained. The fake-clock matrix verifies
renewed evidence at exactly 15 seconds and a legitimate restart only after the
threshold is exceeded. It covers battery-equipped profiles and battery-free
SURPLUS, duplicate reports, unknown permission, disconnect and independent
SoC STOP. The permission observation does not issue a PV proposal or add a
regulation tick.

[PV lifecycle](../../custom_components/wallbox_manager/pv.py) · [Economic interval logic](../../custom_components/wallbox_manager/pv_economics.py) · [Permission replays](../../tests/test_pv_permission_intervals.py) · [PV-ECO-04/05/07](../specifications/pv-regulation.md)

## Boundaries and source cross-check

F1 readback certainty, F2 trusted-LAN/VPN scope, F3 current-format journal
recovery and F5 positive intent invalidation retain their requirements. The
different-transaction case without a matching old `Ended` still reports
`economic_scope_unreconciled`. That operator decision is deferred. The fixes
add no control loop, new acquisition timer, sensor expiry, hardware safety
logic or format conversion. Stationary owns electrical safety and physical
execution. [Architecture](../specifications/architecture-responsibility.md) · [F3](../pv-economic-journal-recovery-review.md) · [F5](../critical-fault-intent-review.md)

The Stationary cross-check used `279287c3c1d8863e803144eccf773ab3f10889a5`.
`ocpp_runtime/station.py` lines 499–546 and 647–661 confirm that the live
transaction-scoped composite can legitimately be unavailable and that
permission is separately reported. `ocpp_runtime/transactions.py` lines
145–174 and 214–235 show persisted identity and sequence on resume.
`v0.3.2-beta.18` at `87963014c9af73416c438807f523a3da2a98bd22`,
`docs/phase-lockout-protocol.md` lines 16–48, separates station rejection
from physical feedback. The actual openWB source at
`849340ec8d91f4e7cca08a7664cf9ec0187fb496`,
`packages/control/chargepoint/chargepoint.py` lines 154–188, separates local
charging eligibility and transaction end. It does not define this Stationary
interface. [Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py#L154-L188)

Operator acceptance still covers the trusted-LAN/VPN boundary, real Stationary
initial/reconnect reporting, same-transaction NETZ recovery and offline Stop,
HA reload after current-format writes, and PV STOP/restart under actual
permission changes. An unobserved physical permission pulse cannot be
reconstructed by WBM. A failed journal write remains an error even when the
listener and other owned runtime resources close successfully.

## Regression and validation evidence

The three new test files were copied to a separate archive of the exact
baseline and run with `python -m pytest -q`. The selection exposed eight R1
failures, five R2 failures and eight R3 failures, with ten controls passing.
All 31 cases passed with the fixes. An existing paused-session battery-reserve
test now waits for Grid's approved zero-current reconciliation and checks
that the reserve remains owned. A settled replay on the exact baseline
confirmed that a suspended vehicle could still have a retained positive
offer that legitimately needs one zero-current write.
[Reserve regression](../../tests/test_grid_battery_restart.py)

The combined focused selection passed 502 tests, covering F1, F3, F5,
NETZ timing and Stop, PV profile behavior, protocol and lifecycle. Ruff
lint and formatting, JavaScript syntax, all 95 frontend regressions and the
headless Chrome fixture passed. Static checks validated the manifest, HACS
and translation JSON, matching localization structure, unchanged metadata
bytes, documentation links and Git whitespace.

The final `python -m pytest -q` run, with integration coverage enabled through
`PYTEST_ADDOPTS`, passed **1,126 tests** with five dependency deprecation
warnings. Integration statement coverage was **94%** overall, with 97%
for Grid, 94% for OCPP, 96% for PV and 90% for entry lifecycle. All
hardware-facing tests used simulated OCPP peers and local HA fixtures.
