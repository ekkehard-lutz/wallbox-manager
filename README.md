# Wallbox Manager — OCPP control and NETZ Grid profile

Home Assistant integration domain: `wallbox_manager`.

This prerelease extends the passive rebuild for OCPP **2.1**, targeting the
supported wallbox-stationary interface. The integration version is
**0.4.0-beta.11**; see the [beta.11 release notes](docs/v0.4/release-notes-beta.11.md)
for zero-current approximation, lockout handling, explicit SET approval and
the required matching Stationary NETZ implementation. The old Stationary
capability inventory cannot satisfy the new NETZ execution requirements.
Publication alone deploys neither component.
The [beta.6 release notes](docs/v0.4/release-notes-beta.6.md) retain the historical
Idle display, session meter corrections and migration details. The immutable
[`v0.3.2-beta.18`](https://github.com/ekkehard-lutz/wallbox-manager/releases/tag/v0.3.2-beta.18)
remains the historical v0.3 reference and rollback source.

Each config entry has exactly one explicitly bound station/EVSE/connector and
one listener port. Multiple wallboxes require separate independent entries and
nonconflicting ports. Configure the station's existing OCPP client URL as
`ws://HA-address:port/station-id` in a separately authorized installation step.
There is no active-wallbox selector or wallbox subentry workflow.

The integration receives measurements and reported status, performs supported
read-only discovery/readbacks, and retains cumulative imported energy/statistics
identities. Ordinary measurements remain valid until explicit invalidation or a
connection/boot generation change. Silent, unchanged values do not expire.

Profile, Number, Select and Text entities persist per-connector configuration.
The manager-owned **charge release / Ladefreigabe** starts OFF and resets on every
actual profile change, reload, reconnect or authority loss. The card has one charging
action: **Start charging**, **Apply charging parameters** for meaningful pending NETZ
drafts, or **Stop charging**. Applying consumes the first press; stopping takes the
next press. Start atomically approves power/timing and creates the one-shot schedule.
Stop requests **0 A** where Remote authority and a transaction permit it. A positive
slider value resolving to zero cannot start. The selector's positive technical
range is calculated from current station diagnostics and enabled phase caps.

The independent hardware **Wallbox enabled / Wallbox aktiviert** switch remains on
the integration/device page and works under Local or Remote authority, without
Take Control or changing manager release. Its Actual state always comes from the
station; enabling hardware under Local can activate the station's retained target.
Only explicit Take Control acquires Remote current/phase authority. WBM cannot stop
locally controlled charging using manager release.

Automatic vehicle wake-up is configured only in Stationary's startup INI;
WBM neither exposes a switch nor uses AutoWakeUpEnabled. The old wake-up switch
and standalone NETZ SET registry identities are retired with exact ownership checks.
PV regulation remains deferred; its shared release foundation supports zero-target
pauses. See the [approved requirements](docs/specifications/charge-release.md) and
[implementation, migration, validation and operational procedure](docs/coordinated-control-redesign.md).

```yaml
type: custom:wallbox-manager-card
# Optional with one WBM entry; required with multiple entries:
entry_id: YOUR_EXISTING_CONFIG_ENTRY_ID
```

The resource remains `/wallbox_manager/wallbox-manager-card.js`. A previously
configured single-instance card can continue with only its existing type. With
multiple entries, a configured `entity` belonging to the intended entry can
also provide the binding. No global first-wallbox fallback exists.

Read the [compatibility inventory](docs/v0.4/compatibility-inventory.md),
[architecture/migration report](docs/v0.4/passive-rebuild-report.md) and
[approved milestone requirements](docs/specifications/passive-v0.4.md). The
consolidated [V3 specification](docs/specifications/pv-regulation.md) remains
DRAFT overall; individually approved decisions are preserved for future work.
Other historical implementation reports describe the tagged v0.3 code, not
this new runtime.

Development checks:

```
.venv/bin/pytest -q -s
.venv/bin/ruff check .
.venv/bin/ruff format --check .
node --check custom_components/wallbox_manager/www/wallbox-manager-card.js
node tests/test_wallbox_card.cjs
git diff --check
```

Live HA CPU acceptance (target <=0.1 percentage points average increase) requires
separately authorized, longer alternating real measurements. No unit-test or
call-count result establishes that target. The station does not yet guarantee a
complete immediate initial telemetry snapshot; missing fields remain explicit.
For separately authorized testing, select **v0.4.0-beta.11** through the HACS
prerelease versions and verify integration version **0.4.0-beta.11** after the
operator performs installation and the Home Assistant restart. Publication does
not include installation or physical validation. NETZ now executes an event-driven fixed offered point under confirmed Remote
authority and charging permission. PV profiles expose only the shared Start/Stop release foundation; regulation remains deferred. See the
[NETZ implementation and validation boundary](docs/grid-fixed-profile-implementation.md).
Automatic wake-up requires the matching Stationary implementation and explicit local startup configuration. Simulated sleeping-vehicle tests are the acceptance
basis; physical CP restoration and hardware integration testing remain separate. If an accepted takeover is
unconfirmed, another explicit Take Control action reads authority again without repeating the write.

See [passive Recorder efficiency, availability and configuration](docs/v0.4/recorder-availability-report.md) for timestamp compatibility changes, diagnostics and measured before/after counts.
