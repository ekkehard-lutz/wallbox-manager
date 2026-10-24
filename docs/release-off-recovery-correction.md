# Disabled manager release recovery correction

## Finding and narrow correction

Independent review of feature base `8f94c6a8f7a553e2db3abda8d005ff10b4d45a94`
identified a genuine deviation from WBM-RELEASE-03/04/08. The branch correctly
reset `charge_release` to false on reload/reconnect and refused stale positive
replay, but `Grid.reconcile()` then returned early whenever both release and
`allow_issue` were false, even after reading a confirmed positive offer.
The existing reconnect test explicitly expected retained 16 A with release OFF.

Before changing production code, real OCPP frames over simulated local WebSockets
reproduced retained 16 A after reconnect, WBM integration reload, and explicit
Local-to-Remote takeover. All three new positive-offer cases failed on the base;
the old reconnect test passed. The three confirmed-zero cases also exposed the
old `waiting/release_off` status instead of confirming the already-zero offer.

The production correction removes this early return only. Existing eligibility,
readback, point comparison, zero-current TxProfile dispatch, ACK/readback outcomes
and adapter/epoch/transaction guards handle the rest. No Stationary changes,
additional polling, timer, state store or recovery state machine are introduced.
`allow_issue` still prevents blind replay when readback is unknown; release remains
the canonical gate that makes the desired recovery point zero. No saved positive
point, phase change, hardware permission or transaction end is requested.

| Recovered state with release OFF | Result |
| --- | --- |
| Remote + valid transaction + confirmed positive offer | One 0 A TxProfile, no phase selection; confirm through existing readback |
| Remote + valid transaction + confirmed zero | Confirm zero without writing another profile |
| Local authority | No current/phase command and no claim that local charging stopped |
| Missing transaction | No current/phase command; reconcile when existing lifecycle establishes eligibility |
| Unknown supported readback without an issuable intent | Remain unconfirmed; no blind command or invented zero |
| Rejected or unconfirmed zero request | Preserve truthful rejection/acceptance/readback status; no fabricated zero |

The changed reconnect assertion now checks a single zero request instead of
positive retention. The draft-reload regression retains the unapproved draft until
an explicit Start, while checking that the recovered offer first becomes zero.
The battery restart regression checks zero-current reconciliation without changing
hardware permission or ending the transaction, then verifies the existing owned
reserve restoration after a real simulated session end. Battery implementation
and compare-before-restore ownership behavior are unchanged.

## Regression and quality evidence

`tests/test_release_recovery.py` covers reconnect, reload and takeover with both
16 A and 0 A offers; repeated reconciliation of confirmed zero; Local authority;
missing transaction; unknown readback; queued zero commands fenced by Local loss,
disconnection, boot generation and transaction termination; late zero ACKs under
the same fences; and rejected/unconfirmed pause results. Existing tests retain
positive-intent cancellation, late-response ordering, hardware permission, timing,
battery lifecycle, exact entity migration, frontend and publication coverage.

Final validation on this correction:

- Complete WBM Python suite: **390 passed**, including all **19 new recovery
  regression cases** and the combined Stationary Domain/DeviceModel compatibility
  test; five unchanged dependency deprecation warnings.
- Frontend/card suite: **27 passed**.
- Ruff lint, Ruff formatting check, JavaScript syntax and Git whitespace checks:
  **PASS**.
- Manifest/version and English/German translation files: unchanged from the base;
  existing migration tests remain in the complete passing suite.

Baseline reproduction used the reviewed production code before changing it:
**6 failed / 1 passed** for the six new recovery/zero cases and the original
reconnect test. The three 16 A scenarios retained 16 A; the three zero scenarios
remained `waiting/release_off` instead of acknowledging confirmed zero. The
original test passed because it expected the positive offer to be retained.
No inherited Python or quality-check failures were observed in the final run.

## Historical and external source cross-check

Re-examined WBM `v0.3.2-beta.18`, commit
`87963014c9af73416c438807f523a3da2a98bd22`, specifically
`custom_components/wallbox_manager/profiles.py:recover`. Its disabled historical
intent could withdraw hardware permission on recovery; its permission-based
runtime cannot be restored under the current release/hardware separation.
The applicable behavior is reconciliation of disabled intent, implemented here
using the current zero-current proposal contract and existing fences.

Re-inspected actual openWB/core source at pinned revision
`849340ec8d91f4e7cca08a7664cf9ec0187fb496`, file
`packages/control/chargepoint/chargepoint.py`, `_is_manual_lock_inactive` and
`is_charging_possible`, using the source retrieved for the coordinated task.
Its policy lock gates charging separately from plug observations, but provides no
OCPP generation/reconnect algorithm to copy. No openWB loop, hardware enforcement
or historical WBM runtime is imported.

## Limitations and operational boundary

A zero request requires live registered Remote authority and the current valid
transaction. Local, offline or missing-transaction states cannot be stopped by
this manager action. Unknown readback remains unconfirmed; rejection or missing
confirmation does not establish physical zero. Existing recognized lockout retry
semantics remain unchanged; there is no new retry for generic failures. Hardware
acceptance remains separate from simulated OCPP evidence.

Only WBM is changed on `feature/manager-charge-release-requirements`. No merge,
tag, release, deployment, HA installation/reload, production service restart or
physical hardware operation is authorized or performed by this correction.
