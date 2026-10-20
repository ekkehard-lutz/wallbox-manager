# v0.4.0-beta.5

This prerelease prepares the passive v0.4 runtime for change-oriented OCPP 2.1
station reporting. It packages reviewed Phase 2A implementation
`4d1210df6822ed12a330b76c42bab06036b53cdc`, based on beta.4/develop
`b472db435737d766e566fb9d843c92e59c507466`.

## Architectural decision

[WBM-ARCH-01–12](../specifications/architecture-responsibility.md) remains the
approved responsibility boundary: physical/electrical safety belongs to the
station, while WBM retains generation-scoped observations without age expiry.
The reviewed Phase 2A exception in WBM-ARCH-07 permits a single authoritative
phase-unknown transition. It preserves the legacy two-invalid-report rule for
ordinary reports. This release preparation changes no specification or broader
requirements; **PV V3 remains DRAFT overall**.

## Functional changes

- A correctly scoped `Connector.PhaseRotation` HardWiredNotification with
  `trigger=Delta` and explicitly empty `actual_value` makes physical phase
  availability unknown immediately. Missing/malformed values and ordinary
  periodic reports do not qualify for this authoritative exception. Timestamp
  ordering, replay protection and connection/boot generation fencing remain;
  newer confirmed `Rxx`/`RST` feedback recovers immediately.
- An Available connector without a current active transaction can finish
  initialization without a TransactionEvent or charging-state observation.
  No synthetic transaction or artificial charging state is created. Occupied or
  unknown connectors and active transactions retain their evidence requirements.
- Transaction/session energy accounting is unchanged. Changed transaction energy
  updates the existing session; omitted unchanged readings retain the value;
  duplicates remain harmless. Transaction and lifetime energy streams remain
  separate, with existing Started/Updated/Ended/TxResumed and sequence handling.
- Existing partial MeterValues parsing and independent last-value retention are
  unchanged. Electrical normalization remains **0.1 V / 0.001 A / 0.1 W**;
  cumulative energy receives no new rounding, deadband or suppression of small
  increments.
- ChargingEnabled change events use the existing observation path. The existing
  **five-second read-only polling fallback remains enabled** for external changes
  and stations without reliable notifications; it does not refresh a TTL.

## Compatibility

Existing entity IDs, unique IDs, devices, settings, statistics, persisted sessions
and history are preserved. **There is no active WBM charging control in v0.4**:
local profile/settings controls remain intent configuration, not station dispatch.
No takeover, charging-current/phase commands or permission writes are introduced.
No new timers, caches, state fields, tasks, persistence formats or dependencies
are added.

The existing deployed stationary wallbox's periodic phase, status, MeterValues,
transaction and readback interface remains supported. Legacy phase-invalid
handling is retained; no station firmware update is required by this manager
release. The forthcoming Phase 2B1 producer can send initial/changed authority and
permission observations and authoritative phase-unknown/recovery events using the
supported interface. WBM polling is not removed, and this release does not enable
or deploy that station producer.

Compatibility here is supported by source review and automated schema/adapter
tests, not a new physical deployment test. Full initial metering synchronization
and station MeterValues delta filtering remain later station-side work.

## Validation

- Final preparation source: **119 Python tests passed** in 21.06 seconds;
  **8 frontend tests passed**. Five existing upstream HA/backoff deprecation
  warnings remain.
- Ruff lint/format, Python compilation, JavaScript syntax and whitespace checks
  passed. Manifest JSON was checked against the reviewed implementation: its
  version is the only changed value, and these notes use the same version.
- Remote develop remains beta.4 at `b472db4`; the reviewed implementation is its
  direct child and contains only approved Phase 2A changes. No beta.5 tag existed
  at preparation time.
- Required feature/develop CI must succeed before tagging/publication; tag CI
  must be verified separately after pushing the tag. CI success is not claimed
  by these local validation results.

The preparation changes only the integration manifest to `0.4.0-beta.5` and adds
these notes. Reviewed Python/JavaScript implementation, architectural
specifications, dependencies and storage/configuration versions are unchanged.

**Home Assistant installation and physical acceptance are separate owner-controlled
steps.** No merge into main/develop, tag, GitHub Release, Home Assistant installation
or station deployment is performed by this release preparation.

See the [Phase 2A implementation report](phase2a-delta-compatibility.md) for the
wire contract, scope and regression evidence.

## Recommended publication sequence — not executed

1. Review the preparation commit and require successful feature-branch CI.
2. Merge `feature/ocpp-delta-consumer-prep` into current `develop` with a normal
   history-preserving merge (`--no-ff`), push develop, and wait for successful
   develop CI. Review any intervening changes; verify the merged manifest and
   release notes still identify `0.4.0-beta.5`.
3. Create annotated tag `v0.4.0-beta.5` on that verified develop merge commit,
   push only that tag, and wait for successful tag CI.
4. Publish a GitHub Release from the existing tag, titled `v0.4.0-beta.5`, marked
   **pre-release**, using these notes. Do not publish it as the latest stable
   release. The repository's checks workflow does not publish releases itself.
5. Keep main and station firmware unchanged. Home Assistant/HACS installation
   and physical validation require a separate owner-controlled step.
