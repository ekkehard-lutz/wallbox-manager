# Wallbox Manager v0.4.0-beta.15

This **prerelease** contains the reviewed live NETZ timing, active-warning and
presentation implementation, including abandoned-editor recovery. It accompanies
the reviewed Stationary hardware-disable/CP keep-awake correction. Publication
installs nothing; supervised installation and physical acceptance remain separate.

## Display and active warnings

- Session energy is displayed with exactly **one locale-aware decimal in kWh**,
  including valid zero. Raw sensors, meter accumulation, the durable session ledger,
  Recorder and statistics retain their precision. Actual live charging power
  remains two decimals in kW; requested nominal power remains one decimal.
- Messages shows only the **newest currently active warning/error**. Resolved
  conditions clear automatically and reveal an older still-active condition, or
  `—` when none remain. Connection, command/lockout, capability, persistence,
  validation, battery-reserve and station-fault sources retain complete diagnostics.
  Ordinary waiting, pending execution, normal zero-current pauses and historical
  successful commands are not warnings. Runtime/generation fencing prevents stale
  callbacks from reviving obsolete warnings.

## Live NETZ schedules and timing edits

- Explicit Start establishes absolute schedule epoch, planned start and optional
  planned end **without requiring a plugged-in EV or transaction**. Delay starts
  immediately; duration starts at planned start, independent of actual power,
  transport, late vehicle arrival or Stationary lockouts. No implicit authority
  takeover or hardware enable is performed. Positive current/phase proposals still
  require normal Remote, transaction, hardware, capability and command eligibility.
- Existing timing fields show live `HH:MM` countdowns. Remaining complete minutes
  are floored, the final 1–59 seconds show `00:00`, and completed/unset countdowns
  show `--:--`. Local rendering creates no per-second HA state or OCPP updates.
- Apply changes the **ongoing schedule**. Duration-only Apply preserves planned
  start and recomputes/removes planned end. An end already in the past expires
  immediately. Changing delay creates a new epoch at approval and pauses authorized
  charging to **0 A**, preserving transaction and hardware enable. Combined power,
  phase and timing changes retain atomic durable approval and dispatch fencing.
- Both timing fields form one editing area. Draft edits do not alter the approved
  schedule. After editing ends, a **15-second approval window** retains pending
  timing values. Without Apply, the backend rolls back timing drafts only; separate
  power/phase drafts survive. Refocus postpones the window.
- If old duration expires first, manager release becomes OFF, pending positive
  intents/retries are retired and authorized zero is requested. **Start replaces
  Apply**, and unapproved time drafts remain for the next explicit Start. Expiry
  also wins over delayed Apply/rollback storage; late completion cannot restart
  release. Explicit `00:00` duration expires at planned start, unlike unset duration.
- A focused editor has a renewable **60-second ownership lease**, renewed every
  **20 seconds** only while editing an active schedule. Renewals change only an
  internal deadline: no durable writes, revision churn or HA state publication.
  Abandonment revokes ownership and starts the ordinary 15-second window from the
  lease deadline. Stale Renew/Update/End callbacks cannot restore it. A new card or
  direct HA Text write can supersede ownership through the existing revisions.

## Coordinated Stationary behavior and compatibility

- Stationary hardware `ChargingEnabled=false` cancels keep-awake cadence and
  pending CP transitions, restores CP through existing ownership/confirmation,
  and prevents keep-awake or automatic wake-up from re-arming while disabled.
  This applies under Local and Remote authority. Failed restoration remains a
  genuine reported failure; no physical state is inferred from elapsed time.
- Re-enable requires fresh connected/zero-current eligibility and a fresh initial
  15-second CP-active interval. It never resumes an old deadline, changes current,
  phase or authority, or grants manager release. A manager-requested **0 A pause
  with hardware enabled** retains the existing 15/120/15 keep-awake policy.
- The coordinated Stationary candidate for controlled installation is develop
  **`279287c3c1d8863e803144eccf773ab3f10889a5`**, containing approved implementation
  `c32e9c935601b8b9271200f1dc3f669dde43275e` unchanged. No Stationary tag/release is created.
- The existing **OCPP 2.1 interface and HA entity unique IDs are unchanged**.
  Stationary retains exclusive CP, relay, register and physical-safety ownership.
  WBM hardware enable, manager release, positive offer, measured charging,
  vehicle connection and business deadlines remain distinct.

## Upgrade, operational limits and rollback

1. Install the coordinated Stationary candidate and this WBM prerelease only in
   the separately supervised deployment phase. Do not mix new card JavaScript
   with an older WBM backend: the existing `wallbox_manager.edit_timing` service
   now supports `renew`. Existing direct HA Text services and power-step service
   identities remain available.
2. Refresh cached Lovelace JavaScript after updating, including each browser/app
   instance. The resource remains `/wallbox_manager/wallbox-manager-card.js`.
   No entity recreation or dashboard entity-ID migration is required.
3. Saved valid/initialized power drafts, phase hints and time selections remain.
   Old `grid_next_timing` storage is preserved but ignored. **Reload/restart keeps
   release OFF** and retires old deadlines/editor ownership; never assume charging
   resumes automatically. A new explicit Start establishes fresh deadlines.
4. An active business schedule survives temporary OCPP disconnection, but an actual
   Remote-to-Local authority loss/profile change cancels release. Offline/Local
   expiry cannot physically stop locally controlled charging; authorized zero
   reconciliation occurs when execution eligibility returns.
5. A browser frozen/disconnected long enough to miss the lease loses ownership.
   After lease loss or failed editor service, deliberately refocus/edit to claim
   fresh ownership. Reconnect alone never replays Begin or approves drafts.
   Persistence failures retain an actionable warning and do not busy-loop retries.
6. Retain configuration/storage backups and the prior compatible package pair
   before installation. Rollback is a separate supervised action, with release
   kept OFF and cached frontend refreshed. The old next-start-only timing/card
   semantics differ from this release; do not restore saved release/deadline state
   as an automatic charging instruction. No INI migration is required by beta.15;
   automatic wake-up remains local Stationary configuration and defaults OFF.

No merge to main, production configuration change, service restart, HACS install
or physical hardware operation is included in preparation/publication.
