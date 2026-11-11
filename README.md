# Wallbox Manager

Wallbox Manager is a Home Assistant integration (`wallbox_manager`) for
operator-controlled EV charging and household PV optimization. It provides
fixed-power NETZ charging, three PV profiles, station and session telemetry,
and an entry-scoped Lovelace card.

This source tree targets **v0.4.0-beta.23**. Once the GitHub prerelease
is published, HACS users can select that version. The release notes include
installation checks and rollback guidance.
[GitHub prerelease](https://github.com/ekkehard-lutz/wallbox-manager/releases/tag/v0.4.0-beta.23) ·
[Release notes](docs/v0.4/release-notes-beta.23.md) ·
[Release validation](docs/v0.4/release-validation-beta.23.md)

## Stationary and OCPP

Wallbox Stationary owns electrical protection, CP, interlocks, physical
limits and phase switching. Wallbox Manager owns optimization, operator
intent and OCPP proposals. An accepted proposal, the station's confirmed
offer and measured charging are separate observations.

The integration serves the Stationary device model over **OCPP 2.1**
(`ocpp2.1` over WebSocket). It receives supported observations and transaction
events, discovers capabilities, reads supported state and submits scoped
control requests. A station must support the relevant interface to execute
commands. Ordinary observations are held within their applicable generation.
A new connection or boot requires its own evidence.

Use an operator-controlled, trusted private LAN and an authenticated VPN into
that LAN for remote access. Within that boundary the listener supports plain
`ws://`, including the default bind `0.0.0.0:9000`. A station ID is a routing
identifier. During commissioning, verify access from the intended LAN/VPN
and confirm the listener is outside direct public Internet reach.
[Approved network boundary](docs/specifications/architecture-responsibility.md#approved-ocpp-lan-trust-boundary)

## Installation and configuration

For the published package, use the
[beta.23 HACS installation and rollback instructions](docs/v0.4/release-notes-beta.23.md#hacs-installation-and-rollback).
In Home Assistant, add Wallbox Manager under **Settings → Devices & services**.
Set the listener address and port and the explicit station ID, EVSE and connector.
Each wallbox/connector entry has an independent listener and settings.
Use distinct ports. Configure the matching Stationary client URL as
`ws://HA-address:port/station-id`, for example `ws://192.168.1.20:9000/Wallbox01`
when the listener and station use those values.

Use **Configure** on the integration to map HA source entities and set
regulation and diagnostics. Battery-free PV_SURPLUS needs grid import/export
mappings and OCPP EV power. Battery profiles also use mapped reserve, SoC and,
for BALANCE, paired charge/discharge power. PV_OPTIMUM needs capacity and
remaining-day forecast energy. Configure PV production and total household
load where the profile uses surplus evidence or the FAST restart branch.
NETZ power selection is independent of these sources. A writable reserve and
actual battery SoC enable its optional temporary reserve policy.

## Charging profiles

| Profile | Behavior |
| --- | --- |
| **NETZ** | Fixed nominal point using 230 V per phase, station capabilities, per-mode current caps and the selected power approximation. A 0 A cap disables that phase mode. Power and optional delay/duration are drafts until approved or equivalent under the backend rule. Start creates an absolute schedule, Apply commits changes and Stop retires release. A disconnect does not extend the schedule. |
| **PV_SURPLUS** | Uses measured surplus without a home battery, or a configured valid battery SoC target with a battery. |
| **PV_OPTIMUM** | Plans a battery target from remaining-day PV energy, capacity, reserve and expected household energy. |
| **PV_MAXIMUM** | Uses the lowest valid target allowed by the battery reserve and hysteresis. |

Battery profiles share SoC hysteresis, FAST/BALANCE regulation, technical
operating points and EV response timing. Planning can continue while release
is off. PV Start authorizes an eligible future proposal and a computed zero
pauses while preserving release. A configurable economic import budget
defaults to 50 Wh. This is an approximate private optimization trigger.
Valid same-ID reconnects retain the budget; a valid new transaction starts
with fresh budget on its first eligible cycle.

## Authority and charging actions

- **Take Control** explicitly requests Remote/OCPP current and phase authority.
  Profile changes, hardware enable and reconnect do not acquire it.
- **Wallbox enabled** on the integration/device page controls Stationary
  hardware permission under either authority. The switch shows observed
  hardware state. Enabling in Local can activate the station's own target.
- **Charge release** authorizes the selected WBM profile. Start and Apply
  require observed hardware permission. Stop can revoke an active release
  even if permission becomes disabled or unknown. With eligible Remote
  control, release OFF uses a zero-current proposal and reconciles confirmed
  retained positive offers. Profile change, confirmed authority loss and reload
  retire release. Stationary owns automatic wake-up through its local startup
  configuration.
- For automations, the explicit `wallbox_manager.charge_action` service takes
  an action and the button's current profile, revision and runtime incarnation.
  Generic `button.press` uses the current backend action, which can be Start,
  Apply or Stop.

## Lovelace and Home Assistant entities

The card resource is `/wallbox_manager/wallbox-manager-card.js`.

```yaml
type: custom:wallbox-manager-card
# Optional for a single entry. Bind explicitly with multiple entries.
entry_id: YOUR_EXISTING_CONFIG_ENTRY_ID
```

An `entity` belonging to the intended entry can also bind the card. The card
shows phase/current offer and request, charging status, active warnings,
NETZ timing, planned reserve or PV target and directional grid or battery
power. The NETZ editors share a lease and a 15-second Apply window after editing.
The profile controls and available actions reflect backend state.

The integration/device page provides configuration Numbers, Selects, timing
Texts, action Buttons, permission Switch, measurements, station diagnostics
and session entities. The **PV Target SoC** sensor exposes the canonical
planning target. OptionsFlow and the nine optional regulation Numbers share
the same configuration. The [current requirements baseline](docs/specifications/v0.4-current-requirements.md)
contains the complete inventory and defaults.

## Diagnostics and persistence

Choose diagnostic level 0–3 in OptionsFlow. Level 2 includes contextual NETZ
events and PV decisions. The integration publishes meaningful entity changes
and separates logs from HA Recorder state. [Diagnostics guide](docs/diagnostic-logging.md)

Per-entry HA storage retains profile settings and NETZ approvals, battery
reserve ownership, current PV budget/planning history and schema-2 session
history. Reload retires live release and reconnects with new observations.
Current binding/registry conflicts require operator correction. Historical PV
and session journals are outside the supported import path.
[Current persistence contract](docs/specifications/v0.4-current-requirements.md#persistence-and-restart)

## Development and acceptance

Use Python 3.14 with `requirements-dev.txt` installed and Node.js 24 as in
[CI](.github/workflows/tests.yml). The browser fixture needs Chrome.

```sh
python -m pytest -q
ruff check .
ruff format --check .
node --check custom_components/wallbox_manager/www/wallbox-manager-card.js
node tests/test_wallbox_card.cjs
node tests/test_wallbox_card_layout.cjs
git diff --check
```

The suite includes simulated OCPP and local HA tests. The coordinated
Stationary fixture can use a separate source checkout via `WB_SOURCE_ROOT`.
Real hardware, source timing and sustained HA/Recorder acceptance are tracked
separately. [Requirements index](docs/specifications/README.md) ·
[Development evidence and open acceptance](docs/v0.4/requirements-consolidation-review.md) ·
[Historical v0.4 notes and reports](docs/v0.4/)
