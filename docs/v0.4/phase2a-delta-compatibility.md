# Phase 2A — minimal delta consumer compatibility

Base: `develop`, `b472db435737d766e566fb9d843c92e59c507466`.
Branch: `feature/ocpp-delta-consumer-prep`.
Station reference: `ecd0b20c73ae6683cc426c9727f7b480b3a347a5` (unchanged).

## Phase unknown wire contract

Verified against the OCA OCPP 2.1 Edition 1 `NotifyEventRequest` schema bundled
with the pinned `ocpp==2.1.0`, and through the real library/WebSocket path. EventData
requires `actualValue`, permits an empty string (no minimum length), and includes
`Delta` and `HardWiredNotification` in its standard trigger/notification enums.
No custom payload fields, DataTransfer message or monitoring subscription is added.

The owner-approved station contract interprets exactly this event as authoritative
unknown: HardWiredNotification, trigger Delta, explicitly empty actual_value,
component Connector with the bound EVSE/connector and no component instance,
variable PhaseRotation with no variable instance. This is the agreed interpretation
for this station; schema validity does not establish a universal empty-string
meaning for every OCPP variable/device.

The adapter forwards that narrow condition as a call-local flag to the canonical
store. The store saturates its existing invalid_count at 2; there is no new
counter or retained flag. Legacy periodic/malformed phase reports retain the
two-distinct-invalid rule. Same-time conflicts retain the legacy single-invalid
handling. Replays, old source timestamps and old connection/boot generations
remain fenced. Newer confirmed Rxx/RST observations recover immediately.

## Idle initialization

The baseline always treated charging_state as mandatory, even with a confirmed
Available connector and no current active transaction. Initialization now omits
only that requirement in this context, using the existing connector observation
and session view. It does not modify the raw missing-evidence set, create a
charging-state observation, or fabricate a transaction. Occupied/unknown connectors
and current active transactions still need charging-state evidence.

The existing discovery entity is notified when connector/session changes affect
this interpretation; repeated sparse transaction updates do not republish
unchanged readiness. Initialization diagnostics use the same effective missing
list. No independent readiness state, helper framework or initialization timer
is introduced.

## Compatibility left unchanged

Session accounting required no production change. Tests exercise Started, sparse
Updated measurements, repeated/omitted energy, small energy increments, reconnect
TxResumed, Ended, sequence replay and strict source separation in both directions.
Lifetime readings cannot replace a transaction stream or vice versa. No additional
accumulator, conversion, persistence or periodic session report is needed.

Permission HardWiredNotification Delta events update the existing canonical
observation; the existing five-second read-only poll remains the fallback. No
subscriber or permission state is added. Partial MeterValues, normalization,
exact cumulative energy, authority, entity identities, persistence and transport
supervision retain their existing implementations.

The approved baseline is the passive OCPP 2.1 rebuild: active regulation/control,
multi-wallbox ownership selection and OCPP 1.6J/2.0.1 runtime implementations are
not present in this baseline. This change neither adds nor removes those features;
their historical branches are untouched. No version or dependency change occurs.

## Complexity and review boundary

Only `ocpp.py` and `store.py` change in production. Their additions are necessary
to convey the exact event semantics, reuse existing invalidation, and remove the
idle-only initialization requirement consistently from public status/diagnostics.
No existing legacy invalid-report protection is deleted.

| Production file | Added | Deleted | Net |
| --- | ---: | ---: | ---: |
| `ocpp.py` | 31 | 5 | +26 |
| `store.py` | 6 | 2 | +4 |
| **Total** | **37** | **7** | **+30** |

Added state fields, timers, caches, background tasks and persistence formats: **0**.
The standalone station checkout and the original manager checkout (including its
pre-existing untracked files) are unchanged. No Home Assistant deployment, merge,
tag, release or version bump is part of this delivery. Review before integration;
the future station delta publisher and its full initial snapshot remain Phase 2B.

## Validation

- Complete Python suite: **119 passed**, five existing upstream HA/backoff
  deprecation warnings, 21.26 seconds. The baseline's 98 tests remain covered;
  the idle-readiness expectation in the existing protocol test is updated to the
  approved contract, retaining its missing-evidence assertion.
- Complete frontend suite: **8 passed**; card JavaScript syntax check passed.
- `ruff check .`, `ruff format --check .`, and `git diff --check`: passed.
- Real OCPP 2.1 framing/schema tests accept the explicitly empty Delta event and
  reject an event missing required actualValue. Focused cases cover legacy
  periodic/malformed events, instances/scopes, equal-time conflicts, replay,
  reconnect/boot fencing and recovery to both 1p/3p.
- No live Home Assistant instance or physical wallbox was contacted or updated.
