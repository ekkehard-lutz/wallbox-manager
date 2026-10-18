# Responsibility boundary and no-expiry observations

Date: 18 October 2026. Base: latest fetched `origin/develop`,
`6e625ea478de5d2ed6dc4ba8ccbe908e5bd1d976` (`v0.4.0-beta.3`).
Feature branch: `feature/observation-no-expiry`.

## Approved requirements and explicit supersession

New normative [architecture-responsibility.md](../specifications/architecture-responsibility.md)
independently approves **WBM-ARCH-01–12**: station hardware safety (01), WBM policy
and presentation (02), proposed commands (03), normal station outcomes (04), held
observations (05), silence is unchanged (06), explicit invalid/lifecycle/generation
events (07), failed reads preserve observations (08), no fresh safety proof or
mandatory pre-command readback (09), request/applied/response distinction (10),
economic/authority/intent policy (11), minimal architecture and publication (12).

Changed specification files:

- `docs/specifications/architecture-responsibility.md` (new overarching approval
  and detailed clause-by-clause supersession register).
- `docs/specifications/README.md` (prominent precedence, milestone/index links,
  replacement of blanket preservation of manager safety gates).
- `docs/specifications/passive-v0.4.md` (V04-08 internal acquisition metadata,
  V04-10 station safety ownership).
- `docs/specifications/pv-regulation.md` (overarching precedence, explicit local
  clarifications at PV-ARCH-04/07, PV-OCPP-04 section 6.4.7 and PV-ECO-06;
  replacement of PV-PLAN-04 forecast expiry; supersession register entry).
- `docs/specifications/decision-history.md` (append-only 18 October approval,
  provenance, precedence, retained policy and authorized implementation scope).

The normative register explicitly supersedes conflicting portions of V04-08/10;
PV-ARCH-04–09; PV-PLAN-04; PV-FAST-03/04; PV-BAL-02/03; PV-PHASE-01/02;
PV-OCPP-01/02/04–06 including section 6.4 and its acceptance rows 1–23;
PV-ECO-01/02/05/06/08/09; PV-QA-01/02. Supersession is limited to physical safety
ownership, freshness/TTL and mandatory fresh/pre-command proof/readback. Existing
SoC/economic STOP/restart, authority, intent, transaction and response-causality
policy remains independently relevant. **Overall PV V3 remains DRAFT.**

Historical implementation descriptions in `docs/architecture.md` and
`docs/metering-runtime-state.md` now carry a prominent precedence notice. Their
historical body and previous decision-history entries remain intact. The
beta.3 transient-observation report is historical; its hidden proof, permission
expiry and unsuccessful-read invalidation claims are explicitly superseded.

## Runtime changes and removed age semantics

Only four production modules change: `store.py`, `ocpp.py`, `entity.py`, `switch.py`.

Removed completely:

- `PhaseRotation` → `valid_until = at + 5 s`.
- `ChargingEnabled` → `valid_until = at + 15 s`.
- `Observation.valid_until` and `State.observe(valid_until=...)`.
- `State.value()`'s comparison of wall-clock time against the deadline.
- `State.value()`'s separate single-invalid phase safety-proof gate.
- `WallboxEntity._expiry`, scheduling/cancellation, expiry callback and its HA
  point-in-time timer import/registration, including setup and unload hooks.
- Permission-specific unconditional publication on every accepted observation.
- Public permission `observed_at`, `received_at` and `enabled_observed_at`.

Public compatibility deadline attributes remain inert `None`: `valid_until`,
`enabled_valid_until`, `physical_phase_valid_until`, session `duration_valid_until`.
No invented deadline replaces the old values. The obsolete 900 s forecast TTL is
removed from the normative draft; passive runtime has no forecast expiry path.

`State.value()` is now the single held-value interpretation;
`last_observed_value()` delegates directly to it for compatibility. Phase invalid
counters/watermarks and original valid-sample provenance remain in the same store.
One distinct invalid report holds the valid current-generation count indefinitely;
two consecutive distinct invalid reports remove availability. New valid 1/3-phase
reports apply immediately. Replays/out-of-order reports cannot advance the invalid
count or restore availability. Same-time conflicts count as one invalid report.
Level-2 transition diagnostics follow the same canonical value as the entity.

New invalid numeric measurements and supported explicit unknown events still
update their channels. Explicit permission true/false/unknown is immediate.
No global debounce is introduced.

`Adapter.read()` accepts only a single matching Accepted Actual result with a
supported value for ChargingEnabled, ControlAuthority or PhaseRotation. Rejected,
missing, malformed, unsupported-value or mismatched results leave observations
untouched. Timeout/transport exceptions retain prior observation and propagate to
the existing caller/diagnostic handling. An initial failed read leaves the field
absent and missing, rather than creating a fake actual unknown observation.
A transient failed phase-switch read therefore cannot flip ChargingEnabled.

Connection loss makes live observations unavailable. Reconnect and BootNotification
require new generation-scoped input per field; old values stay historical in the
store. Late previous-generation results and pre-epoch source reports are rejected.
An initial invalid phase report cannot promote a historical value into the new
generation. No stored history, settings or identity schema changes.

## Publication and preserved timing

Repeated identical permission reads refresh internal observation/acquisition times,
without HA writes or changing public timestamps. A focused real-HA test executes
**2,000 identical successful readbacks: zero entity writes, zero state_changed,
zero state_reported events, identical public attributes and the same HA State
object**. This is deterministic publication evidence, not a measured live Recorder
DB or CPU result. Genuine permission changes still publish immediately.

Existing five-second permission polling remains solely for external change
detection where station notifications are absent. No task was added. Transport
20 s ping interval / 20 s pong timeout, OCPP response timeout, report-completion
wait, command response serialization/shielding, cancellation, boot fencing and
60 s active-session duration updates remain. Existing session/settings timers are
business/persistence behavior, not observation expiry.

The remaining five-second `timedelta` checks in `store.py` and `meter.py` reject
source timestamps too far **in the future**; they do not expire accepted values.
`state_fresh` is a compatibility attribute derived from current availability,
not an age check. Searches find no remaining observation expiry callback or timer.

## Validation and compatibility

- Complete maintained Python suite: **98 passed**, 5 existing upstream
  HA/backoff deprecation warnings, 15.75 s.
- Complete frontend suite: **8 passed**; JavaScript syntax check passed.
- `ruff check .`, `ruff format --check .`, `git diff --check`: passed.
- Python HA/OCPP tests run outside the network sandbox, as required for the
  repository's real localhost sockets/executor behavior. The sandbox run was
  interrupted after hanging; the unrestricted complete run passed.

Tests cover held voltage/current/power/phase/permission at 5 s, 15 s, 5 min and
5 h; no expiry API/callback; one/two invalid phase reports independent of elapsed
time; valid phase changes; malformed/rejected/missing/timeout Actual reads both
initially and after confirmation; explicit permission changes; publication counts;
disconnect/reconnect/boot and late-generation reports; source ordering; initial
unknowns; real library response timeout retaining an observation on a healthy
connection; real dropped Pong closing the connection; one-hour duration updates;
0.1 V / 0.001 A / 0.1 W resolution; exact cumulative energy; registry/migration,
unique IDs, device identity, persistent settings and historical session preservation.

Production Python footprint versus beta.3:

| Metric | Base | Result | Delta |
| --- | --- | --- | --- |
| Modules | 18 | 18 | 0 |
| Physical lines (including comments/blanks) | 2,789 | 2,730 | **−59** |
| Bytes | 101,234 | 99,090 | **−2,144** |

Production diff: **29 additions, 88 deletions**. Frontend, manifest/version,
identity constructors, device registration, registry migration, settings and
storage/session implementations are unchanged. Existing unrelated untracked
investigation files and logs are excluded from the commit.

## Remaining assumptions before active regulation

1. Stations must enforce all physical limits/interlocks and safely handle proposals.
   No WBM automated test or retained observation certifies that hardware contract.
2. Change-oriented delivery is assumed reliable while connected; indefinitely old
   held values are deliberately accepted and may yield suboptimal requests.
3. Source time ordering, five-second future-clock tolerance and generation epochs
   assume compatible station timestamps. These are input ordering/lifecycle checks.
4. Supported per-field bootstrap reads exist, but guaranteed complete initial
   push-only metering/status replay is still an interface gap. Missing fields stay
   unknown; no station capability or deployment verification is claimed.
5. Legacy physical proof/freshness text remains visible as explicitly superseded
   history. Before implementing active control, translate retained optimization
   causality, requested/accepted/applied/measured outcomes, station restriction
   handling and economic/authority/restart policy into the passive architecture.
   Do not restore safety TTLs, per-command physical verification or a second
   station interlock implementation. No active controller is implemented here.

No wallbox-stationary or live HA modification, merge, tag, release or deployment.
The resulting feature commit is identified by the Git commit containing this
report and reported with its exact SHA in the completion message.
