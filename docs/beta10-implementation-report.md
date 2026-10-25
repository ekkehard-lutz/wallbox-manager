# v0.3.2-beta.10 — EV Measurement Ledger Consistency

Baseline: `v0.3.2-beta.9`, with the identical integration source on `develop`
at `376a737`. Fix branch: `codex/ev-ledger-consistency`.

## Problem and correction

A live TransactionEvent at t=12 can contain an embedded power measurement acquired
at t=12.4. The existing ledger excludes that sample from the event's session
projection but previously retained it in the live event cache. The power sensor
then selects the newer cache entry while the ledger still holds its preceding
t=10 measurement, producing `ledger_timestamp_mismatch` and a critical-input
safety pause in PV operation.

`SessionLedger.apply()` now filters embedded observations once by event scope and
the existing acquisition boundary (`observed_at <= event.at`). Both the session
projection and event cache consume that same collection. Later acquisitions are
excluded from both. Existing accepted observations retain their source timestamp,
provenance and validity deadline until eligible new evidence updates them.

Historical start/end and energy accounting keep the same time boundary. OCPP
2.0.1 and 2.1 share this TransactionEvent ingestion path; OCPP 1.6J keeps its
separate StartTransaction, MeterValues and StopTransaction semantics.

No change is made to the sensor's timestamp/value consistency checks, conflicting
measurement handling, source freshness, protocol future-timestamp rejection,
transaction/scope/generation fencing or genuine unavailable states. The production
code change is confined to `session_ledger.py`, plus the manifest version.

## Regression coverage

Sixteen new test cases cover excluded later observations, subscriber publication,
selected-source/projection agreement, ordinary meter updates, start/end energy
boundaries, out-of-order events, connection-generation isolation, same-time equal
and conflicting/invalid values, foreign scopes and OCPP future-timestamp rejection.
Wire tests exercise genuine schemas for OCPP 1.6J, 2.0.1 and 2.1 with local peers.

The integration regression publishes the real SessionSensor through a Home
Assistant EntityPlatform, then consumes it through PV measurement and command
planning for all three PV profiles. Excluded later samples preserve valid positive
charging. A genuinely invalid ordinary EV measurement still publishes unknown
and causes the existing zero-current safety pause to the simulated station.

The new primary regression and start-boundary regression failed on beta.9 before
the correction. Verification of the release source completed successfully:

- Focused ledger/sensor/protocol/PV selection: 78 passed.
- Full Python suite: 1,972 passed in 98.06 seconds, with five existing dependency
  deprecation warnings.
- Frontend suite: 129 passed; JavaScript syntax check passed.
- Ruff lint and format checks passed; Git whitespace checks passed.
- Manifest/HACS metadata validated. Compared with beta.9, all integration files
  except `session_ledger.py` and the version in `manifest.json` are byte-identical.

## Scope and hardware limitation

PV profile policies, conservative power equations, battery discharge limits,
minimum-current/start/continuation gates, the 50% minimum-import allowance,
10-second import confirmation, 20-second response settling, phase sequencing,
authority checks, station restart lockouts and busy retry cadence are unchanged.
Correction B is not included.

The October 9 hardware log establishes the original unavailable-measurement stop
path, but does not identify the exact incoming OCPP sequence that produced its
ledger mismatch. This release fixes the reproduced shared-ledger inconsistency;
confirmation against the original installed hardware remains outstanding.

No Home Assistant deployment, wallbox commands or live charging test is part of
this release. Local diagnostic logs and unrelated investigation artifacts are
excluded from the release commit and assets. This version is a GitHub prerelease
for selection through HACS; it does not replace the latest stable release.
