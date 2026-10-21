# v0.4.0-beta.7

This GitHub **prerelease** packages the independently reviewed active OCPP control
foundation and explicit unconfirmed-takeover recovery from
`03eed10473c462ef9061e0be209dc48c3c06df4f`, based on beta.6/develop
`8c4c031e01f0d0294db537bef8b3cb39a5c528c1`. The feature history is preserved by
non-fast-forward merge `1c0036446c6d9f5aceccd017476a9204968a51f9`.

## Changes

- The existing **Take Control** button explicitly requests station-wide
  `WallboxController.ControlAuthority`, `Actual=OCPP`. A live registered connection
  and a uniquely supported Actual ReadWrite endpoint are required. WBM never
  requests Local; returning to Local remains a Wallbox Stationary operation.
- The existing **ChargingEnabled** switch independently requests
  `WallboxController.ChargingEnabled`, `Actual=true/false`, under observed Remote
  authority and an exact writable endpoint. Permission ON works without a vehicle,
  transaction or active charging profile. OFF withdraws permission independently
  of an economic zero-current pause. No current/phase preparation accompanies it;
  a retained positive station current may become effective after enabling and
  vehicle connection.
- Only these two validated single-variable SetVariables operations are authorized.
  Wire-dispatch guards run after the existing OCPP transport lock, preserving
  station, connection/boot, authority-revision and current-intent fencing.
  Arbitrary SetVariables and other physical control commands remain blocked.
- **Accepted is protocol acknowledgement, not an observed hardware change.**
  Matching response rows are validated; supported canonical GetVariables readback
  or valid authoritative notifications establish the observed outcome. Pending,
  unconfirmed, confirmed, rejected, unsupported, timeout, communication-failure
  and superseded outcomes remain distinguishable. Failed reads retain valid
  observations, and the switch never changes optimistically from an ACK alone.
- For an accepted but unconfirmed takeover, another **explicit** Take Control
  action performs one fenced authority GetVariables read without another write.
  Concurrent recovery callers share that read. Unchanged Local or invalid reads
  do not confirm Remote, and newer authoritative observations retain priority.
- Identical concurrent actions coalesce; already confirmed unchanged requests
  avoid unnecessary writes. Newer user intent supersedes older queued commands;
  late replies cannot overwrite newer intent/observations. Boot and reconnect
  supersede unconfirmed commands without replay. There is no automatic takeover,
  recovery timer or retry loop. Existing five-second permission polling is retained.
- The English/German card enables supported explicit actions and separately labels
  **Last takeover command**, **Observed authority** and current execution
  availability. A historical confirmed takeover does not assert continuing Remote
  authority after external Local takeover or reconnect. Offline authority remains
  unavailable. Diagnostics expose failures and unconfirmed outcomes.

**Charging profiles remain configuration-only. NETZ regulation, PV control,
vehicle wake-up and current/phase control are not included.** Physical/electrical
protection, CP, current/phase safety, interlocks and execution remain station-owned.
PV V3 remains **DRAFT overall**; this publication changes no approval status.

## Persistent data and compatibility

Existing Take Control and permission unique IDs, entity registry associations,
physical device identities, history, cumulative-energy statistics and the existing
session persistence/migrations are retained. No manual entity or statistics
recreation is required. Config-entry/storage versions, dependencies and OCPP 2.1
remain unchanged; independent entries keep independent station control contexts.

The canonical observation store, last-value-held semantics and affected-entity
publication are preserved. No public per-read/command timestamp or Recorder churn
is introduced. Existing beta.6 Idle/session-meter behavior remains included.
No historical control runtime, second state store, regulator or polling loop is
restored. See the [active control requirements](../specifications/active-ocpp-control.md)
and [source, compatibility and hardware assessment](../active-ocpp-control-assessment.md).

## Validation and limits

The complete release candidate is validated with **205 Python tests** and
**11 frontend tests**, Ruff lint/format, JavaScript syntax, Python compilation,
manifest/version checks, Git whitespace and HACS/source-archive scope checks.
Five existing HA/backoff dependency deprecation warnings remain. Publication
requires successful GitHub Actions CI on the exact final develop commit and the
annotated release tag; source-feature CI alone does not satisfy those gates.

The documented develop-versus-foundation benchmark retains zero HA writes and
zero commands for 2,000 identical observations, unchanged normalized voltage,
change-only idle and other-entry activity. Initial sync remains 39 writes/39 state
changes; disconnect/reconnect is 77 writes/52 changes versus 75/52 on beta.6, due
to two bounded lifecycle diagnostic publications. Phase duplicate reports remain
inert; permission readback remains a single five-second task. These are automated
publication-count findings, **not live CPU acceptance**. The <=0.1 percentage-point
average HA Core CPU target requires separate longer alternating measurements.

Hardware acceptance is still outstanding. The station's complete immediate initial
telemetry snapshot is not guaranteed; missing fields remain explicit. Its inspected
interface reports authority loss but provides no guaranteed positive-authority
notification; supported readback confirms takeover. Station rejections and unknown
readbacks are normal outcomes, not evidence of successful physical change.
Automated tests/source review do not establish installed firmware interoperability,
physical switching success or hardware safety. The separate user-controlled
[acceptance sequence](../active-ocpp-control-assessment.md#separate-user-controlled-hardware-acceptance)
checks Local startup, explicit takeover, vehicle-free ON/OFF, external authority
and permission changes, reconnect/no replay, identity continuity and live CPU.

## HACS installation boundary

Take a Home Assistant backup before the separately authorized installation.
Select **v0.4.0-beta.7** through the established HACS prerelease-version workflow.
After the operator performs installation and a Home Assistant restart, verify
integration manifest version **0.4.0-beta.7**. GitHub's tagged source archive is the
HACS integration source; no separate integration ZIP is required. Existing card
resource and entry binding remain unchanged.

This publication does not install files on Home Assistant, restart services,
change live configuration, send live OCPP commands or modify station firmware.
The operator performs installation, hardware acceptance and live CPU measurement
separately. `main`, existing tags and stable releases remain unchanged; this
release is explicitly **Prerelease**, **not Latest**, and **not Draft**.
