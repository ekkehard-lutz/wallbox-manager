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
