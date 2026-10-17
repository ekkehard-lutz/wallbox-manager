# v0.4.0-beta.1 – Clean passive Wallbox Manager rebuild

This first v0.4 prerelease is a complete clean architectural rebuild for passive
observation and a carefully backed-up Home Assistant migration trial. It is an
early test candidate. Charging control and regulation are deliberately unavailable;
it is not production-ready or a completed charging manager.

**A full Home Assistant backup is mandatory before upgrading an existing
installation. Real HA migration and rollback have not yet been field-tested.**
Reference/rollback release: `v0.3.2-beta.18`. Retain that release and the backup;
local registry tests are not a substitute for a tested installation backup.

## Included

- Exactly one wallbox/station and one EVSE/connector binding per WBM config entry.
  Independent entries remain isolated. Ambiguous legacy multi-target layouts fail
  migration before target/registry changes and require a separate decision.
- Passive OCPP 2.1 observations only. The initial milestone implements and tests
  OCPP 2.1; no OCPP 1.6/2.0.1 or arbitrary-station compatibility is claimed.
- Existing energy entity unique IDs, physical device identifiers and energy
  statistics metadata are preserved by the intended migration. Local tests cover
  historical entity IDs, disabled entities, registry associations and beta.18's
  subentry reconstruction helper. Existing Recorder history has not been verified
  through a real installation migration or full rollback restart.
- Simplified native HA integration with one directly attached physical device,
  and a simplified JavaScript card bound to one WBM config entry. Its charging
  permission and takeover buttons are disabled.
- Profile, Number, Select and Text settings remain editable and stored. Profiles,
  schedules, PV/economic policies and charging algorithms are not executed.
- Startup/readback uses only GetBaseReport and GetVariables outbound CALL actions.
  There is no physical charging-control command path or legacy ownership,
  regulator or economic runtime. Existing five-second read-only permission polling
  remains. Failure of an optional initial read no longer suppresses independent
  readbacks or polling of a validated permission endpoint. No bootstrap retry loop
  or additional timer was introduced.

Before the final bootstrap correction, production Python was approximately 2,456
lines versus 18,097 in beta.18. The final candidate has **2,463 Python lines across
18 modules**, plus **350 JavaScript card lines** (blank/comment lines included).

## Known limitations and validation

The station-side initial telemetry snapshot is currently incomplete, especially
for unchanged per-phase values and CP pauses. Missing initial measurements remain
unknown until reported; they are not fabricated as zero. Phase evidence can expire
without a fresh report. Applied operating-point readback and future station-side
initialization work are deferred.

There is **no verified live CPU improvement yet**. Deterministic local publication
counts are measured, and repeated same-valued reports still cause timestamped HA
writes. This release does not claim to resolve Activity/Logbook CPU spikes.
The backed-up real HA migration test must precede live CPU benchmarking.

All 31 maintained Python tests and eight JavaScript tests pass locally, together
with Ruff lint/format, JavaScript syntax and whitespace checks. Tests use isolated
local HA registries/entity platforms and simulated OCPP peers; no live hardware or
Home Assistant deployment was modified. Historical controller tests were not
restored. The source branch/release CI results are reported separately.
