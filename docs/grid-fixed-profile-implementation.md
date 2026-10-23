# NETZ fixed operating point implementation

Implementation of WBM-GRID-01–16 on `feature/grid-fixed-profile`, based exactly
on approved documentation commit `f1e24d69cf350cd93b2627521591d46360fccc2b`.
Implementation, independent review, merge, release and deployment remain separate.
No versions, installed HA configuration, services or charging hardware were changed.

## Control and observations

`grid_point.py` calculates eligible whole-ampere envelopes from the coordinated
Stationary diagnostics. Zero removes a mode; invalid legacy caps are preserved
verbatim and conservatively rounded down/capped (or disabled below minimum), with
`legacy_limit_migration` diagnostics. Missing caps use the advertised maximum.
Legacy RestoreEntity caps are acquired before OCPP tasks start, preventing a
default-maximum race during discovery. The calculation uses all three valid observed voltages or a consistent nominal
230 V basis. Consecutive eligible midpoint ties select the lower count. For
non-monotonic boundaries, the first ascending pair satisfying its boundary wins;
otherwise the highest eligible mode wins. Disjoint ranges use the same rule.
Quantization clamps to the chosen mode's positive envelope, including requests
outside every range. A running positive confirmed offer retains its physical
mode throughout that mode's admissible power interval. There is no percentage
setting, measured-phase optimizer, periodic regulator or periodic setpoint replay.

`grid.py` extends the existing adapter/serialized OCPP transport with a complete
immediate Absolute/TxProfile, stack level zero, one A-unit schedule and period,
EVSE and matching current-generation transaction. Zero omits numberPhases and
never changes ChargingEnabled. Authority/permission/profile/intent/transaction and
connection/boot fences are checked at the actual wire boundary. Power edits use
one-second trailing-edge debounce; caps and approximation create new decisions.
Local/explicit OFF cannot be overridden. Accepted, confirmed offered point and
measured vehicle power are separate. The card displays the confirmed offer and
measured power independently, with rejected/pending reasons.

GetCompositeSchedule and scoped PhaseRotation readback reconcile the offered
point. An existing matching point is adopted without SetChargingProfile. Missing
readback on reconnect alone cannot authorize blind replay; an explicit new intent
or new transaction may dispatch without mandatory pre-command physical proof.
Acknowledgement without matching readback remains unconfirmed. Physical phase
notifications can clarify an unconfirmed result through readback only.

Only exact PhaseSwitchLockout/ReenableLockout reasons schedule retries. A schema
valid `additionalInfo=remainingSeconds=<unsigned decimal integer>` schedules
remaining+5 seconds; missing/malformed information uses 60 seconds. Other errors
have no retry timer. Every retry resolves the live desired point again. No
substitute is dispatched; the existing actual offer remains separately visible.
Business start/duration deadlines use one-shot timers and the canonical profile
Store; explicit 00:00 duration expires immediately. Duration expiry requests
permission OFF even without a vehicle/transaction. Delay holds a zero-current
point while a transaction exists; no TxProfile is invented without one. Pending
settings are consumed when armed. Explicit OFF and profile changes consume active
schedules; reconnect reconciles existing deadlines without rearming a new delay.

## Home battery

The historical battery journal/readback/compare-before-restore algorithm is
retained in `battery.py`, with the old age-expiry requirement removed under
WBM-ARCH-05/06. `grid_battery.py` subscribes to HA reserve/SoC and canonical
charging/session/authority events, independently of OCPP point decisions. Only
current-generation confirmed positive NETZ offers with observed charging and
positive session power authorize a temporary reserve. The original is saved
before writes; the step-anchored floor of min(requested reserve, SoC) never lowers
that original. Missing mappings disable only battery adjustment. External changes
release ownership for the episode. Reload preserves the journal and waits for
current observations before adopting/restoring; no 40→20→40 startup oscillation.
Changed mappings restore the journaled old entity before acquiring a new reserve.
Failed restoration can resume when the reserve entity becomes available again.
HA number services provide numeric readback, not atomic compare-and-set or writer
identity; same-value external writes and final service-dispatch races remain
inherently indistinguishable. There is no reserve polling loop.

## Sources and compatibility

Historical reference: `v0.3.2-beta.18`, commit
`4c9a13b812792aa3eb245f4e2aa9c8c6cd29c090`; inspected `docs/grid-profile.md`,
`custom_components/wallbox_manager/{profiles,grid_timing,battery}.py` and the
historical grid/reserve test inventory. Only the independent reserve lifecycle
was reused; the old control runtime, timer manager and phase optimizer were not.

Actual openWB source inspected at `openWB/core`
`ed5f7038c5c9be525e05429d3a82ec7571680bf7`:
`packages/modules/common/evse.py`, `packages/control/phase_switch.py`,
`packages/control/auto_phase_switch_test.py`, `runs/evse_read_modbus.py`.
It distinguishes EVSE register-1000 offered current from measured draw, compares
before current writes, and keeps physical switching in the chargepoint module.
Its periodic/vehicle-phase optimization was not imported. The inspected EVSE
adapter supplies no reboot counter usable by this integration.

The paired Stationary change replaces connector-scoped MaximumCurrent1Phase /
MaximumCurrent3Phase with MaximumCurrent / MaximumAsymmetryCurrent. EVSE-scoped
MinimumCurrent, CurrentStep, SupportedPhaseModes remain. Legacy wire maxima do
not authorize new NETZ execution. Entity identities are preserved for unchanged
roles; unsupported allowed-current editors are never created. Wallbox01 supports
1p and 3p only. Generic 2p is covered in the pure planner, not claimed as installed
hardware or a newly implemented Stationary switching mode.

## Validation and review boundary

Real HA fixtures, local schema-valid OCPP peers, fake clocks and hardware stubs
cover dynamic envelopes/ties/disabled modes/migration/retention; complete dispatch,
confirmation/non-repetition, debounce, lockout delays/cancellation, transaction/
permission/reconnect fences; reserve adjustment/restoration/external change/
journal restart/readback; scheduling and duration expiry; entity/card behavior.
The full maintained pytest suite, Ruff lint/format, JavaScript syntax/tests,
manifest JSON, translation-key parity and Git whitespace checks are recorded in
this branch's delivery report. These are simulated software checks.

Hardware acceptance remains outstanding: paired inventory/readbacks, DIN reboot
behavior (including 0 A), physical relay/current confirmation, lockout timings,
automatic wake-up and the real home-battery number service. NETZ cannot identify
DIN resets that produce no observable interruption; Stationary's correlated
outage+boot-current recovery limitation is documented in its companion report.
Independent review should focus on event ordering/late replies, schedule ownership,
and HA number-service races. No additional safety manager or measurement cache
was introduced; all ordinary measurements remain in State.
