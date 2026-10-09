# Phase-switch lockout substitute operating point

The OCPP 2.1 adapter previously mapped every rejected SetChargingProfile to
`busy`. Generic rejection cannot safely distinguish a phase-switch lockout
from hardware, authority, restart, or unsupported-operation failures.

## Required station contract

Station firmware must return this response only for a temporarily blocked
physical phase transition:

```json
{"status":"Rejected","statusInfo":{"reasonCode":"PhaseSwitchLockout"}}
```

This uses the existing OCPP 2.1 StatusInfo field (a reasonCode string of up to
20 characters); it is an application-specific code, not a new OCPP action or
standardized reason. No additional field or schema extension is needed.

The station must reject the entire profile atomically, preserve the physical
phase state, and discard the rejected profile without retaining it for later
execution. Other failures must not use this code. A charging restart lockout
must take precedence when applicable. Every subsequent same-phase profile
must still pass station-side restart, authority, hardware and electrical
checks; accepting a profile must not defer an unsafe restart until later.
Zero-current profiles retain immediate pause behavior.

The manager cannot infer restart safety from this code, or implement these
firmware guarantees itself. Station firmware is outside this repository.
Firmware returning generic Rejected remains compatible but gets no fallback.

## Manager behavior

On this exact rejection of a positive target requiring a different phase mode,
the manager calculates one substitute on the currently confirmed physical mode.
The existing approximation direction, voltage basis, device grid and all current
limits apply. For this substitute only, positive-current points are considered:
1700 W on 3 x 230 V selects the minimum 6 A (4140 W offered), not pause. If no
positive point satisfies the direction or limits, no substitute is sent.
Explicit zero targets continue to use the ordinary immediate-pause path.

All original generation, authority, connection, transaction and input fences
remain active. A rejected phase transition never changes phase feedback.
The substitute solver result plus command status describe what was attempted
and whether it was accepted; physical feedback remains independent. No preferred
point is queued, and expiry/telemetry causes no dispatch. A subsequent identical
explicit power request runs the ordinary calculation again.

## PV Optimum reachability

Optimum additionally retains specific phase rejection as scoped runtime evidence.
Energy-desired planning continues across verified modes using the fresh regulator
budget. Only executable planning is constrained to the confirmed mode. Its
minimum-positive policy can raise the soft request to that mode's safe minimum;
it cannot exceed beta.9's independent hard power ceiling or grid-import allowance;
this does not relax DOWN approximation for manual controls or other profiles.
A task-scoped probe at the existing retry cadence may try the preferred transition
again. The restriction remains known during the probe and is not cleared merely
because 60 seconds passed. Successful different-mode application, changed physical
mode, or a new connection/authority context replaces the old evidence. Generic
BUSY/restart rejection never creates phase evidence. Probe permission is carried
only by that serialized command, including its transport dispatch guards.

Each planning cycle samples the regulator once and resolves both the energy-desired
and executable points through the common solver and existing profile policy.
`ManualIntent.energy_desired` exposes the latest planning result; it is never a
saved command replayed at a retry deadline. Probes recalculate both points. If the
fresh desired phase is already the confirmed phase, no phase change is attempted.
Before a phase-changing transport write, the dispatch fence additionally requires
that phase to remain energy-desired. An increased budget alone cannot authorize an
obsolete lower-power phase. Safe current coalescing within the desired phase stays
unchanged; observations after the wire write belong to the next cycle.
A locally superseded phase command is replanned on the next regulation cycle;
it does not impose generic station BUSY backoff on a same-phase correction.
The existing deadline still bounds further probes of a known blocked transition.
