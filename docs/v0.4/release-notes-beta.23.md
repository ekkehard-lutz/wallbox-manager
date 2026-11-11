# Wallbox Manager v0.4.0-beta.23

This prerelease packages the reviewed runtime fixes, cleanup and session
recovery added since beta.22. It is intended for controlled field acceptance
in a private household.

## Runtime corrections since beta.22

| Change | Behavior |
| --- | --- |
| F1 · Ambiguous OCPP rejection | Preserve uncertainty about the actual offer after an ambiguous rejection. A supported readback can resolve it. Existing mandatory zero handling still applies. [Review](../ocpp-ambiguous-rejection-certainty-review.md) |
| F3 · PV economic journal recovery | Restore valid accumulated Wh and STOP provenance. Damaged or unreconciled current history inhibits automatic positive PV proposals. A required zero proposal remains possible. [Review](../pv-economic-journal-recovery-review.md) |
| F5 · Scoped critical fault | A new Faulted transition on the bound connector invalidates pending positive intent and fences late results. Stationary handles the physical fault. [Review](../critical-fault-intent-review.md) |
| R1 · NETZ reconnect | A known transaction must reconcile the retained offer. Matching readback avoids a repeat write, a valid mismatch can be corrected, and insufficient evidence leaves the point unconfirmed. Absolute schedule deadlines continue through the gap. [Review](runtime-blockers-r1-r3-review.md#r1--same-transaction-netz-resume) |
| R2 · Runtime teardown | Fence dispatch and drain the entry's owners even when a journal write or another cleanup step fails. Surface the failure after cleanup is attempted. [Review](runtime-blockers-r1-r3-review.md#r2--teardown-after-journal-failure) |
| R3 · Economic permission gaps | Observed disabled or unknown permission interrupts the pending economic interval and incomplete reset qualification. Accumulated Wh and STOP history survive. [Review](runtime-blockers-r1-r3-review.md#r3--pv-economic-evidence-across-a-permission-gap) |
| PV transaction rollover | Same-ID recovery retains the budget. A valid new transaction on the same scope starts a fresh budget on its first otherwise eligible PV cycle. The conservative F3 recovery gate still applies. [Review](pv-economic-transaction-rollover-review.md) |

PV budget edits retain accumulated Wh and pending or latched economic STOP.
Lowering the limit can trigger STOP on the next eligible ordinary tick.
A budget edit grants no release and schedules no additional tick. Existing
reset and restart criteria remain in force. [Approved PV-TUNE-09 and PV-TUNE-10](../specifications/pv-operating-point-response.md)

## Session journal recovery

Valid schema-2 journals restore the latest session and every known closed
transaction ID. A restored session becomes live only when a valid event in
the current registered generation confirms it. Started, Updated and Ended
messages received before registration leave session state, closed IDs and
meter attribution intact.

For an incomplete current snapshot, the integration archives the original
and normalizes trustworthy fields and closed IDs. Unknown readings stay
unknown, and unusable session records are discarded. For damaged JSON or an
invalid Store envelope, it preserves the original bytes or reuses the
existing Home Assistant quarantine and starts with an empty journal. It
verifies the replacement before opening the OCPP listener. A failed read,
archive or verified save stops entry setup and preserves available evidence.

An abrupt power loss can lose updates inside the existing one-second delayed
save window. A valid stale snapshot is indistinguishable from the latest valid
snapshot. An entirely missing file without quarantine evidence looks like
first setup. Lost history can leave session energy and statistics incomplete,
and replay protection can only reject IDs still known to the integration.
Session energy is informational household data. [Recovery review](session-recovery-registration-review.md)

## Reviewed cleanup

- Batches 1 and 2 retired unused desired-power and maximum-discharge Numbers,
  the PV approximation Select, legacy diagnostic attributes and the smoothing
  option. Current discovery, command status and approved settings remain
  available. [Inactive controls review](inactive-controls-cleanup-review.md)
- Batch 3 removed historical config-entry and registry migration, old
  restore-state adoption and flat options handling. A current entry requires
  explicit station/EVSE/connector binding. Existing current entity IDs and
  device associations are preserved. Historical registry rows can remain
  unavailable and can be reviewed separately by the operator. [Entry and registry review](entry-registry-cleanup-review.md)
- Batch 4 removed the retired `.pv_day`, `.pv_economy` and `.sessions`
  import paths and stopped serializing live generation in new session
  snapshots. Older schema-2 snapshots with that field remain readable.
  Unsupported historical payloads remain outside the upgrade path. [Journal cleanup review](journal-import-cleanup-review.md)

Harmless retired settings such as `pv_start_delay`, `pv_stop_delay` and
`power_smoothing_window` may remain stored and have no effect on runtime
decisions. Keep existing records for rollback and review.

## Current configuration

Each current config entry binds one station, EVSE and connector to its own
listener and scoped settings. NETZ uses a nominal-power draft, approximation
direction, current caps per supported phase mode and optional delay and
duration. Start and Apply approve settings through the existing schedule
rules. Stop retires release. A 0 A cap disables the corresponding phase mode.

PV offers SURPLUS, OPTIMUM and MAXIMUM. Configure the battery target or
planning bounds, estimated household energy and source mappings as required
by the profile. OptionsFlow **General** provides source mappings and
diagnostics. **Regulation** and the nine optional Number entities share
hysteresis, cadence, settling and ramp settings, grid deadband, import
budget, EV uptake and the FAST restart household-load limit. The current
requirements list the controls, defaults and valid ranges.
[Configuration inventory](../specifications/v0.4-current-requirements.md#home-assistant-entities-and-configuration)

The current per-entry Stores retain scoped settings and NETZ approvals in
`.profiles`, reserve ownership in `.battery`, PV economics and planning in
`.pv`, and session history in `.passive_session`. Review missing or
conflicting explicit bindings before setup. Historical beta migration is
outside the supported upgrade path. [Persistence contract](../specifications/v0.4-current-requirements.md#persistence-and-restart)

## Charging authority and acceptance

Stationary retains electrical and physical safety ownership. Only an explicit
**Take Control** requests Remote authority. Manager release is OFF after
reload and startup leaves positive charging authorization with the operator.
Hardware permission remains independent. Unreconciled PV economic history
keeps automatic positive PV inhibited. [Responsibility boundary](../specifications/architecture-responsibility.md) ·
[Release and control gates](../specifications/charge-release.md)

All 17 physical acceptance scenarios, including quantitative PV response and
performance, remain open. This build supports private field testing.
Session and economic measurements carry no billing or electrical safety
certification. [Physical acceptance matrix](release-readiness-audit.md#13-outstanding-physical-acceptance-matrix)

## HACS installation and rollback

Before the operator installs:

1. Create a complete, restorable Home Assistant backup.
2. Preserve the existing `.storage/wallbox_manager.*` records and any
   quarantine artifacts.
3. Record the installed Wallbox Manager and Stationary versions.
4. Record config-entry identities, station/EVSE/connector bindings, HA entity
   IDs and source mappings.
5. Verify listener reachability only on the approved private LAN or through
   authenticated VPN. [Trusted network boundary](../specifications/architecture-responsibility.md#approved-ocpp-lan-trust-boundary)
6. Arrange a controlled charging-test window.

Once the GitHub prerelease is published, select **v0.4.0-beta.23** using the
existing HACS version-selection workflow. HACS uses the standard tagged
source archive. The operator controls installation and the Home Assistant
restart.

After restart, verify the installed manifest version is **0.4.0-beta.23**,
entry setup succeeds and the expected station connects. Check the saved
configuration, device and entity identities, source mappings and current
frontend resource at `/wallbox_manager/wallbox-manager-card.js`. Review any
unexpected session recovery warnings before enabling a test. Confirm that
manager release is OFF and that WBM sent no unexpected positive charging
command.

Keep the original HA backup. For rollback, select the previously recorded
version through HACS in a controlled window. A package-only rollback can
leave newer storage or registry semantics in place. Restore the complete
pre-upgrade backup when the prior storage and identity state is needed.
Keep the damaged originals and any recovery evidence for analysis.

[Release validation](release-validation-beta.23.md)
