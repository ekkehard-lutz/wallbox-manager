# Wallbox Manager v0.4.0-beta.14

This **prerelease** includes all reviewed changes since the published beta.12:
nominal NETZ power steps, UI simplification, the reserve restart-test
synchronization correction and the production draft-initialization/approval race
fix. The beta.13 candidate was tagged but never published after tag CI exposed
the approval race; that annotated tag remains unchanged. Physical acceptance and
installation are separate; publication installs nothing.

## Nominal NETZ planning and technical steps

- NETZ planning now uses fixed **230 V per phase**, independent of measured grid
  voltage. The requested-power editor always displays **one decimal in kW**.
  Bounds, approximation, phase selection, navigation and operating-point
  equivalence use nominal offers rounded deterministically to 0.1 kW. Measured
  voltage/current/power, accounting and Recorder precision remain unchanged.
- Each supported, enabled **1p/2p/3p** mode has its own contiguous series derived
  from MinimumCurrent, CurrentStep, hardware maxima and its WBM allowed-current
  cap. A **0 A cap excludes the mode entirely**. Optional 2p is used only when
  genuinely advertised; this does not add 2p support to the owner's Stationary.
- +/- advances one legal current step within the current phase series, even
  where another mode's power range overlaps. Phase changes are considered only
  when pressing + at that series' actual upper endpoint or − at its lower
  endpoint. Choose the nearest strictly increasing/decreasing rounded offer in
  another eligible mode, skipping equal or backward powers.
- For the illustrative 1p/6–20 A and 3p/6–27 A installation, the selector spans
  **1.4–18.6 kW**. Examples: 1.4 → 1.6 → 1.8 → 2.1 within 1p; 1p/20 A at
  **4.6 + → 3p/7 A at 4.8**; 3p/6 A at **4.1 − → 1p/17 A at 3.9**.
- An explicit phase hint is stored with each selected technical draft/approved
  point. This distinguishes **1p/18 A** from **3p/6 A**, both displayed as
  **4.1 kW**, and preserves the selected phase intent through Start/Apply and
  reload. Direct numeric edits clear this hint and use ordinary approximation
  and confirmed-positive phase retention.
- DOWN / UP / NEAREST remain administrator-configurable through their existing
  HA Select entity/services. They compare rounded nominal offers: DOWN 1.6 kW
  selects 1p/7 A, and DOWN 4.8 kW can select 3p/7 A. Manual values between offers
  retain their approximation behavior; +/- uses the separate technical-series
  navigation rule. No independent JavaScript electrical solver is introduced.

Actual measured power may be above or below the nominal requested power as grid
voltage varies. One-decimal rounding is a planning convention, not electrical
protection. Stationary continues to enforce physical limits and switching safety.

## Draft initialization, card and HA services

- Missing, invalid, out-of-range or legacy-zero NETZ drafts initialize to the
  **lowest valid positive technical point** once capabilities are known. With
  missing capabilities the editor shows an unavailable placeholder. This is a
  persisted **draft-only** initialization: it grants no release, does not replace
  previous approval/timing and issues no current/phase command. Valid saved
  selections remain stored; older higher-precision values are displayed/planned
  in the rounded one-decimal domain. Capability/cap changes can invalidate a
  saved draft and trigger the same initialization; review it before Start/Apply.
- The Lovelace **power approximation** and **battery discharge reserve** editors
  are removed. Their HA entities, identities, settings, supported services and
  backend functionality remain. Reserve ownership, compare-before-restore,
  persistent recovery and immediate administrator configuration are preserved.
- Live actual charging power now displays **exactly two locale-aware decimals
  in kW**, including zero: for example `1,38 kW`, `5,52 kW`, `0,00 kW` in German.
  The requested editor retains one decimal; energy/duration formats are unchanged.
- The card uses backend neighbours and `wallbox_manager.step_power`, targeting
  the existing NETZ Number entity. Relative steps require direction (-1/+1),
  current draft revision and runtime incarnation; stale requests are rejected.
  Hold, keyboard and multiple-card operations wait for backend acknowledgment.
  Existing `number.set_value` automation/direct-entry workflows remain supported.

## Preserved control and lockout behavior

NETZ still has one **Start / Apply / Stop** action. Draft navigation alone never
dispatches an unapproved different point. Manager release starts/resets **OFF**
on initialization, reconnect and authority loss; it never takes Remote authority
or enables hardware implicitly. Under confirmed Remote authority and a valid
transaction, release OFF reconciles retained positive offers to **0 A** after
reload/reconnect/takeover; confirmed zero is not resent. No current/phase commands
are sent under Local authority. The independent hardware-enable switch keeps its
beta.12 authority-independent behavior on the HA integration/device page.

Recognized phase-switch lockouts retain the desired phase/current point. For
DOWN 3p/6 A → 1p/17 A and UP 1p/20 A → 3p/7 A, the temporary fallback is **0 A**
on the physical phase mode. A positive interim offer is allowed only when it
meets the applicable rounded-domain direction. Existing remaining-seconds + 5 s
and 60 s fallback retry rules, subsequent charging-reenable lockout, transaction,
connection and late-command fencing are preserved. Interim zero is not reported
as final target confirmation; generic errors do not create a retry loop.

## Both CI-discovered corrections

**Reserve restart-test synchronization:** the reserve restart test now waits for
Home Assistant's tracked journal-save operation before asserting storage contents.
The in-memory reserve record can clear before the awaited save finishes. This
corrects an asynchronous test race; production compare-before-restore and battery
reserve recovery behavior are unchanged by that follow-up.

**Draft initialization/automatic approval:** when MinimumCurrent rises from 6 A
to 7 A, an invalid 1.5-kW draft can initialize to 1.6 kW. Previously a later
operating-point equivalence callback could automatically approve it instead of
retaining the old approved 1.4-kW request. Callback order could also advance the
approval revision and hide pending parameters. The correction persists the private
**`grid_power_initialized`** marker in the existing Settings profile document:

- Initialization saves `true` atomically with draft power, selected phase hint and
  draft revision. Approved power/revision, charge release and timing remain intact.
- Automatic equivalence adoption rejects marked drafts across delayed callbacks,
  capability changes, timing edits, reconnect and reload. While released, the card
  keeps **Apply charging parameters** for the pending replacement; while OFF it
  keeps **Start charging**. Initialization does not grant release or dispatch a
  positive current/phase command.
- A genuine explicit power edit clears the marker and resumes ordinary equivalent
  user-edit adoption. Explicit Start/Apply clears it together with atomic approval.
  Failed storage leaves the old state intact; superseded approval restores the
  marker with the prior approval/timing state.
- Existing stores without this marker retain ordinary legacy-draft behavior. The
  marker is internal persistence metadata, not a new HA entity, OCPP variable or
  controller. No schema-version bump, polling loop or authority path is introduced.

Deterministic event-barrier regressions force initialization-before-reconciliation
and reconciliation-during-persistence, checking both release ON/OFF, explicit
Apply/Start, approved revisions, timing, phase hints and late callbacks. Ordinary
approved-request capability reconciliation remains unchanged: the old approved
1.4-kW request can resolve to 7 A under the new minimum with NEAREST; initialization
adds no independent dispatch. See the
[correction report](../grid-initialization-approval-race.md).

## Upgrade, packaging and rollback

- Entity IDs, HACS layout, dependencies and config-entry versions are unchanged.
  HACS uses the tagged source archive; no separate ZIP asset is required.
- After a separately authorized WBM installation/reload, refresh all browser/app
  Lovelace caches to load the matching card at
  `/wallbox_manager/wallbox-manager-card.js`. An old cached card does not provide
  the new backend-driven step navigation. Do not reuse saved revision/incarnation
  payloads across reloads; read the Number entity's current metadata.
- Keep the beta.12 compatible Stationary implementation, exact develop commit
  **`0f97ebf6f4d83e0b56f33d3f4bb68f9642c92313`**. Beta.14 requires no Stationary
  modification/release. Automatic wake-up remains local startup INI configuration
  and defaults OFF; its retired WBM switch/OCPP setting is not restored. See
  [beta.12 compatibility and migration notes](release-notes-beta.12.md).
- A request already incorrectly approved by the unfixed candidate has no reliable
  stored history from which to reconstruct the previous approval. Beta.14 does not
  invent or silently reverse it. Review saved approved and draft NETZ powers before
  any later Start, especially after testing the unpublished beta.13 candidate.
- Back up HA configuration/profile/entity-registry data before manual installation.
  Review initialized/rounded drafts and authority before an explicit Start.
  Older WBM versions ignore the new phase hint/initialization marker and use
  their prior voltage, phase-selection and approval policy; rollback with manager
  release OFF, restore backups as appropriate, and review drafts before starting
  again. Existing battery-reserve
  ownership/restoration and beta.12 paired rollback considerations still apply.

See [release validation](release-validation-beta.14.md),
[WBM-GRID-31–40](../specifications/grid-profile.md) and the
[nominal-power implementation report](../grid-nominal-power-steps.md).
Simulated OCPP and mocked hardware do not establish physical vehicle switching,
live HA acceptance or production CPU impact. PV regulation remains deferred.
No main branch, Stationary source, installed HA component, production service,
hardware configuration or physical wallbox is changed by publication.
