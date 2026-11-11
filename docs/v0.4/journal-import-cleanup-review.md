# Historical journal import cleanup review

## Validated baseline

Batch 3 merged into `develop` as `26877615c8eb4d912cf13840bfe2f3823de72b16` with both reviewed parents and a tree identical to `fc60fd958c523c2cd1fcf1cc79f87e4773bae90e`. The merge is the exact Batch 4 baseline. [Reviewed Batch 3 CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42664605393) and [exact merge CI](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42675792625) passed. Local merge validation passed 1,170 Python tests at 95% statement coverage (4,462 statements, 222 missed), with five dependency deprecation warnings. Ruff, 95 frontend tests, headless Chrome, manifest/HACS metadata, localization parity, four documentation links and anchors, and Git whitespace checks passed.

The feature branch implements R11, R12 and S04 from the [pinned cleanup inventory](https://github.com/ekkehard-lutz/wallbox-manager/blob/863a6aece4b80e09d2780212eca7cf9d68876743/docs/v0.4/code-parameter-cleanup-inventory.md). Batch 3 binding and registry validation is unchanged.

## PV journal

| Case | Before | After |
| --- | --- | --- |
| Genuinely missing current `.pv` | Opened retired `.pv_economy` and `.pv_day` stores, then either imported their records or used the ordinary initial state. | Starts with an unbound zero-Wh budget, an initial planner and no stored station identity or Ended marker. Startup does not inspect retired stores. |
| Valid current `.pv` | Restored economics, local-day planning, station identity and Ended marker. | Same current-format restore and validation. Store version 1 and payload version 1 are unchanged. |
| Current history is unreadable, malformed, unsupported, quarantined or explicitly unreconciled | Automatic positive PV remains inhibited under F3. | Same inhibition, diagnostics and recovery. The current-loader sentinel still distinguishes actual absence from an unreadable or quarantined journal. |

R11 removes the former policy, rational watt-second, legacy restart flag, hardware identity and day-state conversions. The retained loader moved to `pv_journal.py`. Its checks for a current file or its HA quarantine artifact are unchanged. Current `.pv` retains `economics`, `day`, `station_identity` and `ended_transaction`. `Budget.restore()` remains authoritative for transaction scope, Wh, STOP and cause, and still validates saved `reset` without reusing prior reset qualification across downtime. Durable writes and read-back verification remain required on the existing eligible positive proposal path.

A same-ID reconnect retains accumulated Wh and STOP provenance. A different valid ID on the same binding gets a fresh budget on its first otherwise eligible PV cycle without requiring prior `Ended`. A transaction change cannot bypass damaged history. Current strict `>15 s` reset qualification, permission-gap interruption, STOP/restart evidence and charging gates are unchanged. Ignoring historical storage does not issue a command or grant manager release, authority or permission. See [approved PV transaction rollover](../specifications/pv-regulation.md#approved-pv-transaction-rollover--9-november-2026).

## Session storage and replay

| Case | Before | After |
| --- | --- | --- |
| Missing current `.passive_session` | Opened `.sessions` and could reconstruct a session and retired IDs. | Starts without recovered history. The retired ledger is never opened. |
| Existing schema-1 or unversioned session payload | Converted provisional meter values before restoring state. | Fails setup with a clear unsupported-schema error. The loader does not rewrite the payload or treat it as an empty replay history. |
| Valid current schema 2 | Restored session ID, scope, timestamps, sequence, energy, attribution metadata and the complete closed-ID set, invalidating the saved live generation. | Same restoration with Store version 1 and payload schema 2. The reader accepts existing records with or without `generation`. |

S04 is implemented. The serializer copies the live session record and omits only its `generation` field. It keeps the separate energy generation and link metadata and the complete sorted closed-ID set. Loading still sets the live session generation to `None`, even when an old saved value happens to match the new runtime's generation. A valid current-generation event must reconfirm the session before it becomes command-eligible or accepts current energy samples. Tests verify both schema-2 variants, unchanged active sensor behavior and that serialization leaves the live record intact.

Repeated restarts retain terminal IDs and reject delayed events carrying those IDs even when the events otherwise have a current generation and a fresh timestamp. No pruning, expiry, generation-only replay check or transaction watermark was introduced. The current OCPP event validation, energy rules and entity identities remain unchanged.

## Storage and safety boundaries

Production startup no longer opens `wallbox_manager.<entry_id>.pv_economy`, `wallbox_manager.<entry_id>.pv_day` or `wallbox_manager.<entry_id>.sessions`. Those historical files can remain on disk. They are not imported, rewritten, renamed or deleted. Current `.pv` and `.passive_session` keys and versions, scoped settings and battery reserve storage are unchanged. Unsupported session payloads now require operator review before that entry can start. The task did not introduce new recovery behavior for session Store I/O errors.

Source comparison used WBM `v0.3.2-beta.18` at `87963014c9af73416c438807f523a3da2a98bd22` (`custom_components/wallbox_manager/pv_economy_storage.py:136–260` and `session_storage.py:13–24`). The old economic store distinguished missing records from uncertain recovery and retained transaction scope. Its formats are historical. Stationary `279287c3c1d8863e803144eccf773ab3f10889a5` (`ocpp_runtime/transactions.py:132–174,214–237`) emits opaque transaction IDs and durably reserves sequence numbers before sending. openWB `849340ec8d91f4e7cca08a7664cf9ec0187fb496` (`packages/control/chargepoint/chargepoint.py:154–188`) was checked for station-owned interlocks and unplug-driven transaction closure. [WBM-ARCH-01–12](../specifications/architecture-responsibility.md) still governs safety ownership. There is no change to charging policy, Stationary, command fencing, polling or teardown.

## Validation and independent review

The pre-cleanup no-read tests detected both historical PV Store loads and the retired session ledger load. The three unsupported-session cases exposed baseline acceptance or conversion. The two S04 cases reached the final document comparison and detected the old persisted generation. Those cases pass with the cleanup. Import-only tests were retired or replaced with historical-file isolation, current planner and budget recovery, schema rejection, and repeated restart/replay coverage. Existing F1/F3/F5, R1/R2/R3, PV rollover and battery reserve coverage remains.

- Full `python -m pytest -q` with the local Stationary source fixture passed 1,162 tests in 288.45 seconds. Statement coverage was 95% (4,393 statements, 217 missed). Five dependency deprecation warnings remain from Home Assistant HTTP and Backoff.
- The combined focused run passed 609 tests. The PV-only change passed 220 tests, the session loader change passed 121, and six targeted schema and restart cases passed with S04.
- Ruff lint and formatting, JavaScript syntax, 95 card regressions, one headless Chrome fixture, two local documentation links and anchors, and Git whitespace checks passed. Manifest, HACS metadata, localization and HA service schemas match the baseline.

The branch is ready for independent review of the missing versus damaged PV journal boundary, the unsupported session schema error and current schema-2 restart behavior. Tests used isolated Home Assistant and simulated OCPP peers. Physical acceptance remains separate. There was no deployment, release, tag, manifest version or HACS change.
