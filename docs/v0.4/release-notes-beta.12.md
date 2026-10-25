# Wallbox Manager v0.4.0-beta.12

This **prerelease** delivers the reviewed coordinated Stationary/WBM control
redesign, including the release-OFF recovery correction. Install Stationary and
WBM as a **compatible pair**. Publication does not install or restart either one.

## Required companion Stationary revision

Use Stationary `develop` commit
**`0f97ebf6f4d83e0b56f33d3f4bb68f9642c92313`**, whose complete tree is identical
to approved feature commit `8015fd3f4a4c066d903b647a243349f56ae27c0f`.
No Stationary release or tag is created by this WBM prerelease.

Automatic vehicle wake-up is configured exclusively through Stationary's local,
startup-only INI settings:

```ini
[wallbox]
auto_wakeup_enabled = false
cp_keep_awake = false
auto_wakeup_delay = 30
wakeup_duration = 5
```

`auto_wakeup_enabled` accepts only case-insensitive literal true/false and defaults
**OFF**. Delay remains integer 1–3600 seconds; pulse duration remains 1–300 seconds.
A configuration change requires a separately authorized Stationary service restart.
Old persisted remote enable values cannot override the INI. Keep-awake is a separate,
default-OFF option; its GPIO timing and CP ownership remain unchanged. Automatic
wake-up now works under Local and Remote authority without WBM registration.

## Hardware permission and internal manager release

- The old WBM **Automatic vehicle wake-up / Automatisches Aufwecken** switch is
  removed. The implementation-defined OCPP variable is exactly
  `WallboxController.AutoWakeUpEnabled` (sometimes described as
  AutomaticWakeUpEnabled); it is removed from Stationary inventory, reads, writes
  and notifications. WBM ignores old advertisements and has no replacement setter.
- `WallboxController.ChargingEnabled` is the independent **whole-wallbox hardware
  permission**. The HA device/integration-page **Wallbox enabled / Wallbox aktiviert**
  switch retains its identity and confirmed Actual state. It works under Local
  and Remote authority, without Take Control, manager release or current/phase
  side effects. Its control is removed from the custom Lovelace card. Enabling it
  under Local can expose the station's retained local positive target.
- Internal per-connector **charge release / Ladefreigabe** is a separate WBM user
  intent, not an OCPP variable or an observed charging state. It defaults **OFF**,
  resets OFF on actual profile changes and WBM reload/connection or authority loss,
  and never enables hardware or takes Remote control implicitly.
- Release OFF requests **0 A** when Remote authority, registration and a valid
  transaction permit it; it preserves hardware enable, phases and transaction
  identity. After reconnect, WBM reload or explicit Local-to-Remote takeover, a
  confirmed retained positive offer is also reconciled to 0 A. Confirmed zero is
  not resent; old positive targets are never replayed. Local/offline/no transaction
  means no unauthorized write and no claim that physical charging has stopped.

## Unified NETZ action and technical power bounds

The previous NETZ **SET** button and card hardware-permission action are replaced
by one backend-authoritative charging action:

| Manager state | German / English action |
| --- | --- |
| Release OFF | **Ladung starten / Start charging**, disabled unless the NETZ draft resolves to an executable positive current |
| Release ON, equivalent approved parameters | **Ladung beenden / Stop charging** |
| Release ON, meaningful pending NETZ parameters | **Ladeparameter übernehmen / Apply charging parameters** |

Apply deliberately takes the first press; stopping then requires a second press.
Reverting drafts to an equivalent approved point restores Stop without applying.
Start atomically approves draft power, delay and duration with release and schedule;
Apply preserves release and existing schedule deadlines. Draft edits alone do not
dispatch unapproved current/phase points. Multiple cards and direct HA edits share
the canonical backend state, durable approval, legal-point equivalence and fences.

The positive power selector bounds now come from advertised supported phase modes,
MinimumCurrent, MaximumCurrent, MaximumAsymmetryCurrent, CurrentStep, each mode's
allowed-current cap and the existing consistent voltage basis. A 0 A cap excludes
a phase mode; no eligible mode disables the selector/start action. No 2p hardware
support is invented. DOWN/NEAREST/UP quantization and phase retention remain: a
positive selected power can resolve to **0 A**, in which case Start stays disabled.
Legacy out-of-range drafts remain available for correction without authorizing a
positive dispatch. Stop is an explicit release action, not a zero-watt slider value.

PV regulation algorithms remain deferred. Only shared Start/Stop release foundation
is present: PV may be released at zero, normal future PV zero pauses retain release,
and profile changes reset it. There is no PV SET/Apply state or new economic policy.

## Migration, paired installation and rollback

- Only exact integration-owned obsolete wake-up-switch and NETZ SET registry
  entries are retired. Foreign entries/configuration remain untouched. Existing
  hardware-switch, Take Control, Number/Text settings and energy/statistics identities
  are retained. Dependencies, config-entry/storage versions and HACS layout are
  unchanged. Automations using obsolete entities must be updated.
- Hardware acceptance is separate. Before manual installation, back up both code
  revisions, Stationary INI/persisted data and HA integration/profile/entity-registry
  storage. Install the companion Stationary revision first, deliberately choose
  local wake-up options, then install WBM and refresh the Lovelace resource. Any
  restarts/reloads are manual, separately controlled installation steps.
- Expect manager release OFF after WBM initialization. Recovered positive Remote
  charging will be paused once the required transaction/readback is established.
  No automatic Remote takeover occurs. Review the device hardware switch separately.
- Roll back as a matched pair. First reconcile a confirmed stop while authorized
  and restore any WBM-owned battery reserve; restore prior code/configuration and
  HA backups as needed. Deleted legacy registry entries may need rediscovery.
  Old WBM uses hardware-permission semantics and may resume retained positive
  targets; old Stationary may restore its former persisted wake-up flag. Review
  both explicitly rather than assuming the new default-OFF policy survives rollback.

## Validation and accepted limitations

Complete regression, frontend, formatting, lint, translation and packaging evidence
is in [release validation](release-validation-beta.12.md). The release source is the
annotated tag's tracked GitHub archive; HACS requires no additional ZIP asset.

Unknown readback and rejected/unconfirmed commands are never fabricated as confirmed
zero. Local authority, missing transactions and offline stations cannot be stopped
through WBM release. Recognized lockout retries, session identity and battery journal
ownership remain unchanged. Simulated peers do not establish vehicle wake-up,
physical switching, relay endurance or live HA CPU acceptance. Keep-awake retains
its accepted delayed unplug detection (nominal remaining blocked interval up to
120 seconds plus debounce/sample delay, unbounded during communication loss).

The reviewed implementation references are
[coordinated redesign](../coordinated-control-redesign.md) and
[release-OFF recovery correction](../release-off-recovery-correction.md).
Neither `main` branch is changed. No hardware operation, production INI change,
deployment, HACS installation, Home Assistant reload or production service restart
is part of this publication task.
