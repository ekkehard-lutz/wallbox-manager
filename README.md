# Wallbox Manager — passive v0.4 prerelease

Home Assistant integration domain: `wallbox_manager`.

This prerelease is the passive rebuild for OCPP **2.1**, targeting the supported
wallbox-stationary interface. The integration version is **0.4.0-beta.6**;
see the [beta.6 release notes](docs/v0.4/release-notes-beta.6.md) for Idle display,
session meter corrections and migration details. The immutable
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

Profile, Number, Select and Text entities persist local configuration. They do
not regulate charging or control the battery. Permission and authority remain
observable; takeover and permission actions are explicitly unavailable. The
card retains its familiar appearance, with both control buttons disabled.

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
For separately authorized testing, select **v0.4.0-beta.6** through the HACS
prerelease versions and verify integration version **0.4.0-beta.6** after the
operator performs installation and the Home Assistant restart. Publication does
not include installation or physical validation.

See [passive Recorder efficiency, availability and configuration](docs/v0.4/recorder-availability-report.md) for timestamp compatibility changes, diagnostics and measured before/after counts.
