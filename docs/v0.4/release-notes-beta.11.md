# v0.4.0-beta.11

Hardware-test prerelease of the independently reviewed NETZ zero-current,
lockout and explicit SET implementation, including the reviewed pending-draft
equivalence correction.

**Requires the coordinated Stationary NETZ implementation**, matching Stationary
develop `ce823cf52faa511475ef87a776c7a0dce28e1184`. Its connector diagnostics
`MaximumCurrent` and `MaximumAsymmetryCurrent`, EVSE diagnostics `MinimumCurrent`,
`CurrentStep` and `SupportedPhaseModes`, immediate TxProfile/readback support and
lockout responses form the required contract. The old phase-specific maximum
inventory is incompatible. The matching Stationary is already installed and is
not changed by this release. Publication alone deploys neither component.

## Approximation, lockouts and truthful display

- DOWN, UP and NEAREST now include **0 A** as a real pause candidate below the
  selected phase mode's minimum. DOWN below minimum selects 0 A; UP selects the
  minimum positive current; NEAREST selects the closer candidate, with an exact
  zero/minimum midpoint choosing the positive minimum. Explicit zero stays zero.
  Whole-ampere boundaries use exact arithmetic, including adjacent-ampere ties,
  station minima, steps and maxima. Zero current does not withdraw permission.
- During **PhaseSwitchLockout**, keep the original approved phase/current target
  pending and, where needed, apply one interim point on the known physical phase
  mode. Examples at nominal 230 V: DOWN at 1.5 kW can offer **3p/0 A** while
  awaiting **1p/6 A**; UP at 1.4 kW can offer **3p/6 A** while awaiting **1p/7 A**.
  Interim acceptance is not completion of the original target.
- Handle a subsequent **ReenableLockout** independently. Recognized lockouts retry
  after reported remaining seconds plus five, or after 60 seconds if missing or
  malformed. Intent, authority, permission, session and connection guards fence
  stale retries; unrelated errors do not create an uncontrolled retry loop.
- An accepted or uncertain profile followed by failed readback makes actual
  phase/current unknown until supported confirmation. Atomic rejection preserves
  a still-confirmed actual point. Internal generation-scoped observations remain
  available for planning. The card shows concise **actual → approved requested**
  phase/current, for example `3-phasig 0A → 1-phasig 6A`, or `—` for unknown actual.
  Requested offers, protocol acceptance and measured vehicle draw stay distinct.

## Explicit SET and persistent configuration

- Power, start delay and duration are **persistent editable drafts**, separate
  from the last approved request. The new connector-scoped **SET** button
  validates and durably approves all three together. A failed save keeps the old
  approval and pending draft. Existing Number/Text services, entity identities,
  multi-card behavior and locale input remain supported.
- Ordinary draft edits send no charging-point command. SET can approve valid
  configuration offline, in Local or without a vehicle; later execution still
  requires the existing authority, permission and transaction conditions. SET
  neither takes control nor enables charging.
- Electrically equivalent numeric power changes auto-approve when timing is
  unchanged and reliable planning context establishes the same approved desired
  phase/current. This adds **no OCPP write** and preserves lockout deadlines.
  Pending drafts are also reevaluated after relevant approximation, current-cap
  or capability changes and supported confirmation. Missing capabilities,
  uncertain readback or an in-flight command keep explicit SET conservatively.
- **Discharge reserve and power approximation remain immediate**, outside SET;
  allowed-current caps also retain their immediate behavior. Approximation
  always recalculates approved numeric power and cannot execute an electrically
  different unapproved draft. Battery reserve ownership, persistent restoration
  and reviewed restart recovery remain independent of OCPP point dispatch.
- Preserve one-shot start delay/duration, consumed timing and existing absolute
  deadlines. SET during a session does not rearm a delay or alter its active
  duration. Approved timing applies to the next applicable start event. Empty
  means unset; explicit `00:00` duration expires immediately.
- Upgrade from beta.10 initializes approved settings from existing active values,
  with equal drafts, preserving stable entity IDs and foreign profile data.
  Restart retains subsequent drafts and approval separately without silently
  accepting a different draft or rearming consumed schedules.

## Validation and remaining acceptance

The approved feature is `24767915eaa0b7e6d6dc287ec74d71727b5e4333`, preserved
through a non-fast-forward develop merge. Release preparation changes manifest
version and documentation only; dependencies and storage/config-entry versions
are unchanged. See [release validation](release-validation-beta.11.md) for gates
and results. HACS uses the annotated tag's tracked source archive; no separate
ZIP asset is required. This is a **prerelease**, not a stable latest release.

**Real hardware acceptance remains pending:** paired inventory/readback,
zero-current pause/re-enable, 1p/3p phase switching and actual lockout durations,
physical CP/automatic wake-up, installed HA battery-number restoration, EVSE
fallback recovery and live CPU behavior. Stationary supports 1p/3p; generic WBM
2p calculations do not add 2p hardware support. Its DIN interface still lacks a
positive reboot counter: a reset missed between samples or without detectable
boot-current fallback cannot be positively identified.

Simulated HA/OCPP tests and offline contract validation do not replace physical
acceptance. Deployment and hardware testing are manual and separate. Publication
does not install software, change configuration, interact with Home Assistant,
restart Stationary or issue hardware commands. Both main branches remain unchanged.
