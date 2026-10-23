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

Profile, Number, Select and Text entities persist local configuration. Eligible
NETZ sessions apply an approved fixed offered point and an optional battery reserve.
Power, start delay and duration are persistent drafts until SET approves them
together. Electrically equivalent power edits can be approved automatically;
discharge reserve and approximation remain immediate. The existing Take Control button
explicitly acquires OCPP authority; the permission switch independently requests
ON/OFF only under observed Remote authority and supported writable endpoints.
Switch state always reflects station observations, never an acknowledgement alone.
See the [active successor requirements](docs/specifications/active-ocpp-control.md)
and [source assessment / separate hardware procedure](docs/active-ocpp-control-assessment.md).
There is no automatic takeover or unconditional command replay. The NETZ successor
adds guarded fixed operating points; PV execution remains deferred.

Automatic wake-up replaces the manual HA button with
**Automatic vehicle wake-up / Automatisches Aufwecken** on the existing device
page, outside Lovelace. Stationary detects a genuine 0 A-to-positive CP B episode,
waits its configured delay and attempts one CP interruption. WBM only reads and
changes the station's confirmed persistent enable setting. First commissioning
defaults OFF; restarts, reconnects and authority changes preserve the saved value.
Enabling or restoring ON never arms an existing positive-current episode.
See the [approved contract](docs/specifications/automatic-vehicle-wakeup.md) and
[implementation assessment](docs/automatic-vehicle-wakeup-assessment.md).

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
authority and charging permission. PV profiles remain configuration-only. See the
[NETZ implementation and validation boundary](docs/grid-fixed-profile-implementation.md).
Automatic wake-up requires the matching Stationary implementation to be separately
installed. Simulated sleeping-vehicle tests are the acceptance
basis; physical CP restoration and hardware integration testing remain separate. If an accepted takeover is
unconfirmed, another explicit Take Control action reads authority again without repeating the write.

See [passive Recorder efficiency, availability and configuration](docs/v0.4/recorder-availability-report.md) for timestamp compatibility changes, diagnostics and measured before/after counts.
