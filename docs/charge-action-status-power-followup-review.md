# PV Start acknowledgement and status power display follow-up

Operator-approved follow-up, 31 October 2026. Continued on
`fix/charge-action-state-and-reserve-preview` from
`e61a16d4c4c3fe671a2ccb2086c48c0b76979219`. Remote refs were fetched and local /
upstream heads verified equal before implementation. The eight unrelated local
untracked files were retained. This report supplements the
[original implementation report](charge-action-state-reserve-preview-review.md);
its historical layout claims are superseded only as explicitly documented below.

Implementation and local simulated validation are authorized; independent human
review, merge, tag, release, deployment, live HA restart/reload and hardware
operations remain separate. No live hardware validation is claimed.

## Review correction

The shared `Grid._action()` preparatory PV path already validated the current
context, durably saved release and revalidated lifecycle fencing. It then fell
through to a generic electrical-task wait and a check of `grid.status`. With a
valid transaction, PV launch intentionally does not create an immediate point,
so an older `rejected`, `communication_failure` or `unsupported` result could
remain and falsely fail the successful Start service call.

The preparatory branch now returns after durable acceptance and notification.
It neither waits for a previous electrical task nor reinterprets that task's
status as the result of manager authorization. It does not clear an old status,
fabricate successful electrical execution or issue an immediate PV point. The
shared preparatory path uses this acknowledgement for both the explicit service
and the retained compatibility button; the latter remains a legacy toggle.

NETZ still follows the existing electrical-task/result handling. Explicit
Stop/Apply, failed validation/storage, stale revision/profile/runtime and
post-persistence supersession retain their existing behavior. Source comparison
shows no other Grid behavior changed. In particular, the existing durable
rollback, release publication and all action fences are retained.

## Card layout and power data

The new **Wallbox Status / Wallbox status** section contains:

| Area | Left | Right |
| --- | --- | --- |
| Upper row 1 | Connection status | Charging state |
| Upper row 2 | Session energy | EV Leistung / EV power |
| Upper row 3 | Duration | Wallbox parameters |
| Lower blocks | Home battery heading, small **Ladung Ist → Soll / Charge actual → target**, actual SoC → canonical target/planned reserve | Directional source power |

Messages remain below the status section. At narrow card widths the lower blocks
stack, with the home battery first. The removed redundant visible battery caption
“Ladezustand” remains absent; it still correctly labels the separate upper charging
state and identifies actual battery SoC in accessibility text. The new caption is
an explicitly approved change to the earlier caption-free layout.

Power is read directly from existing mapped HA entities:

| Profile | Red semantic direction | Green semantic direction |
| --- | --- | --- |
| PV_SURPLUS / PV_OPTIMUM / PV_MAXIMUM | Storage discharge positive and storage charge valid zero: **Entladeleistung / Discharging power** | Storage charge positive and storage discharge valid zero: **Ladeleistung / Charging power** |
| NETZ | Grid import positive and grid export valid zero: **Netzbezugsleistung / Grid import power** | Grid export positive and grid import valid zero: **Netzeinspeiseleistung / Grid export power** |

Both-zero, either unavailable/invalid/restored/missing, unsupported units or
simultaneously positive observations show neutral **—**, under **Speicherleistung /
Battery power** or **Netzleistung / Grid power**. Missing opposite-direction data
is never interpreted as zero. No net or signed power is guessed from an ambiguous
pair. Supported finite nonnegative W/kW/MW states are converted to kW and displayed
with exactly two locale-aware decimals. Actual source precision stays intact.

Colors use `--error-color`, `--success-color` and `--secondary-text-color`. Direction
also appears in text, independently of color. Tooltip and accessible name include
both reported directions, their available values, and the unavailable/ambiguous/
no-direction reason where applicable. Direction changes reset label, value and
color; multiple cards and mapping changes cannot retain another source's value.

The existing charging-profile Select gains only four stable mapping attributes:
`storage_charge_power_entity_id`, `storage_discharge_power_entity_id`,
`grid_import_power_entity_id`, `grid_export_power_entity_id`. Its existing options
listener now watches these mapping identities too. Source power observations are
not copied onto WBM entities. The card reads its existing `hass.states` snapshot;
no polling, additional timer, WBM sensor, control path, source write or journal
change was introduced. All source changes continue to be delivered by HA.

Canonical PV targets under STOP, release OFF, unplugged and unavailable OCPP
remain unchanged. NETZ still shows the planned reserve, with the actually observed
reserve separately identified in tooltip/accessibility text. Neither reserve
ownership nor physical override/restoration behavior changes.

## Normative decisions and traceability

| ID | Approved change / evidence |
| --- | --- |
| WBM-ACTION-SAFE-09 | Preparatory PV Start succeeds on durable release, not an older electrical result. Nine profile/status service cases verify actual persisted release, Stop action publication and no new OCPP point. |
| WBM-RELEASE-PREP-06 | No wait for an older electrical task; failure and lifecycle fences retained. Tests cover a deliberately blocked prior task, real storage failure, actual NETZ rejection, plus the existing 24 safe-action regressions. |
| WBM-HS-LAYOUT-01–04 | New status heading, approved upper/lower placement and small battery caption, responsive layout and preserved target/preview. Frontend tests and real Chrome geometry verify placement and accessibility across 96 combinations. |
| WBM-HS-POWER-01/02 | PV charge/discharge and NETZ import/export directions. Tests cover all four profiles in German and English, both positive directions and profile/mapping changes. |
| WBM-HS-POWER-03 | Semantic theme colors with textual direction. Unit tests verify CSS variables; Chrome verifies custom theme colors resolve in the rendered card. |
| WBM-HS-POWER-04/05 | Neutral invalid/ambiguous/zero presentation, source-unit conversion and two decimals. Tests cover unavailable opposite direction, missing mappings/states, restored states, negative/nonfinite/blank/non-string data, unsupported units and W/kW/MW. |
| WBM-HS-POWER-06 | Stable mapping projection and direct HA rendering. A real HA test verifies one mapping publication, zero Select writes over 300 source-update rounds, and one later remapping publication. |

The dated amendments in `charge-release.md`,
`lovelace-home-battery-display.md`, `operational-display-and-timing.md` and the
specification index preserve prior decisions as history and explicitly supersede
only WBM-HS-UI-01/02/13/15 layout/alignment and the 26 October caption-free
refinement. WBM-HS-UI-SYNC-01–04, WBM-HS-UI-NETZ-01–06, WBM-GRID-15,
WBM-TIME-01–24 and the direction-safe action model remain in force. WBM-ARCH-01–12
continues to govern ownership, held observations and change-only publication.

## Source review and invariance

Inspected actual openWB/core revision
`4e6b2224420fc25ab87aa7534201fcf9e250cdfa`,
`packages/main.py:HandlerAlgorithm.handler10Sec()` and
`packages/control/bat_all.py:get_charging_power_left_diff()`. Its clocked control
and storage-priority handling remain comparison evidence, not imported code.
openWB's signed storage-power convention is not applied to WBM's two separately
mapped nonnegative HA sources; this display must preserve their observed meanings.

Reviewed historical WBM `v0.3.2-beta.18`, peeled
`87963014c9af73416c438807f523a3da2a98bd22`, `sensor.py` and
`pv_diagnostics.py`: observation projection and read-only diagnostics are useful
precedents; no legacy runtime, freshness manager or control logic was restored.
The existing review-branch card's direct HA battery-source projection is the
implementation model for the new display.

Thirteen exact Git blob comparisons against the review base confirm unchanged
`pv.py`, `pv_core.py`, `pv_soc.py`, `pv_plan.py`, `pv_economics.py`, `grid_point.py`,
`control.py`, `ocpp.py`, `sensor.py`, `settings.py`, `battery.py`, `grid_battery.py`
and `manifest.json`. FAST/BALANCE, planning, SoC hysteresis, economic STOP,
regulation cadence, NETZ electrical execution and reserve ownership/restoration
are unchanged. The manifest stays `0.4.0-beta.20`; Stationary and Fronius sources
and live configuration were untouched.

## Validation

All final checks passed on the frozen implementation:

| Check | Exact result |
| --- | --- |
| Complete Python suite | **895 passed, 0 skipped**, five existing dependency deprecation warnings; **186.66 s** |
| Statement coverage | **93.58%**, 3,894 / 4,161 statements covered, 267 missing |
| Focused backend and existing action races | **37 passed**: 13 new follow-up cases plus the existing 24 direction/preview cases |
| Complete frontend suite | **92 passed**, zero failures/skips |
| Real headless Chrome | **1 passed**, containing **96 combinations**: two languages × four widths × four profiles × three direction states |
| Ruff lint / format | Both passed |
| JavaScript syntax | Production card and layout test syntax checks passed |
| Manifest | Valid JSON/domain; version unchanged at `0.4.0-beta.20` |
| Translation parity | English/German/strings leaf-key parity passed; card strings follow the existing inline bilingual style |
| Documentation | **45 local links/anchors passed** in changed specification documents and this report |
| Whitespace | `git diff --check` passed |
| Source invariance | **13 exact Git blob comparisons passed** against the review base, as listed above |

Commands used: `.venv/bin/pytest --cov=custom_components.wallbox_manager
--cov-report=term-missing --cov-report=json:/tmp/wbm-followup-coverage.json -q`,
`.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`,
`node --check custom_components/wallbox_manager/www/wallbox-manager-card.js`,
`node --check tests/test_wallbox_card_layout.cjs`,
`node tests/test_wallbox_card.cjs`, `node tests/test_wallbox_card_layout.cjs`,
manifest/translation/local Markdown assertions and `git diff --check`.
Localhost integration peers and Chrome ran with the required environment network/
process permission; none contacted a live Home Assistant or physical device.

Recorder evidence: one changed mapping document produces one Select write;
**300 source-update rounds (four mapped powers each) produce zero Select writes**;
one subsequent remapping produces one write. The full suite also retains the
previous 300 unchanged STOP-cycle target/Select/action deduplication and reserve
preview checks. No new WBM source-power sensor or cyclic diagnostic stream exists.

All 882 tests from the review base remain represented; the 13 new backend cases
bring the total to 895. Frontend coverage grows from 83 to 92 tests. Earlier
caption/layout assertions were deliberately updated to the approved new contract,
while target, reserve, service errors, race fencing and control behavior remain
covered. GitHub Actions validation is checked after the feature push and reported
with the exact final commit in the completion response.

## Remaining acceptance

Software tests use local HA harnesses, simulated OCPP peers and real headless
Chrome. They do not establish actual installed HA resource caching, physical
battery/grid source timing or hardware behavior. Later separately authorized
operator acceptance can confirm the layout in the site's theme and browser,
observe charge/discharge/import/export and ambiguous transitions, and Start under
SoC STOP following an earlier electrical rejection. No such hardware work was
performed by this task.

## Changed files

- `custom_components/wallbox_manager/grid.py`
- `custom_components/wallbox_manager/select.py`
- `custom_components/wallbox_manager/www/wallbox-manager-card.js`
- `docs/charge-action-status-power-followup-review.md`
- `docs/specifications/README.md`
- `docs/specifications/charge-release.md`
- `docs/specifications/lovelace-home-battery-display.md`
- `docs/specifications/operational-display-and-timing.md`
- `tests/test_pv_start_status_followup.py`
- `tests/test_wallbox_card.cjs`
- `tests/test_wallbox_card_layout.cjs`
