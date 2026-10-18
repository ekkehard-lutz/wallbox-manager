# v0.4.0-beta.4

This prerelease publishes the approved architectural responsibility boundary and
last-value-held observations in the passive v0.4 runtime.

## Architectural decision

[WBM-ARCH-01–12](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.4.0-beta.4/docs/specifications/architecture-responsibility.md) is independently
APPROVED. The charging station alone guarantees physical/electrical safety,
including physical limits, interlocks, CP operation and safe phase switching.
WBM owns charging policy, optimization, diagnostics and presentation. Its requests
are proposals; station limits, interlocks, adjustments and rejections remain
authoritative. The specification explicitly supersedes conflicting older WBM
safety/freshness requirements. **PV V3 remains DRAFT overall.**

## Functional changes

- Ordinary measurements and reported states have no observation time-to-live.
  Silence retains the last value within the current observation generation.
- Removed the five-second physical-phase proof expiry, fifteen-second
  ChargingEnabled confirmation expiry, observation expiry callbacks and associated
  Home Assistant timers.
- Phase observations use one canonical last-value-held interpretation, without a
  separate hidden safety-proof value.
- Failed, rejected, malformed or timed-out GetVariables reads preserve previous
  observations. Missing initial values remain uninitialized.
- Explicit valid and invalid observations remain meaningful. The beta.3 phase
  policy is retained: one distinct invalid phase report may retain the last value;
  two consecutive distinct invalid reports make it unavailable; a newer valid
  phase report updates the value immediately. Duplicate/out-of-order reports do
  not create false transitions.
- Connection and boot generation changes still require new observations; late
  old-generation reports remain fenced out.
- Identical ChargingEnabled readbacks no longer repeatedly publish HA state or
  changing public acquisition timestamps. Compatibility deadline attributes are
  `None`.
- Existing five-second permission polling remains for detecting external changes,
  not renewing a TTL. Transport/response deadlines and session duration updates
  remain functional.

## Compatibility

Existing entity IDs, unique IDs, device identity, settings, persisted historical
data and statistics are preserved. No active WBM charging control is introduced.
Electrical measurement resolution remains 0.1 V / 0.001 A / 0.1 W; cumulative
energy precision is unchanged. There are no changes to `wallbox-stationary`.

## Validation

- Implementation branch: **98 Python tests passed**, **8 frontend tests passed**;
  GitHub [feature CI succeeded](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/39151060332).
- Final release source: **98 Python tests passed**, **8 frontend tests passed**;
  Ruff lint/format, Python compilation, JavaScript syntax and whitespace checks
  passed. Five existing upstream HA/backoff deprecation warnings remain.
- A real-HA regression executes 2,000 identical successful permission reads with
  zero additional HA publications or Recorder-significant attribute changes.
- Implementation delta relative to beta.3: **−59 production Python lines and
  −2,144 bytes**. Result: 18 Python modules, 2,730 lines, 99,090 bytes.
- Required develop CI must succeed before tagging/publication; tag CI is verified
  separately after pushing the release tag.

**Physical Home Assistant validation remains pending until the user installs
beta.4.** Automated validation is not hardware acceptance. Installation, HACS and
live observation are separate user-controlled steps; this release performs no
Home Assistant or station deployment.

See the [implementation report](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.4.0-beta.4/docs/v0.4/no-expiry-observations-report.md) for the complete
supersession register, removed deadlines, lifecycle behavior, compatibility and
remaining assumptions before active regulation.
