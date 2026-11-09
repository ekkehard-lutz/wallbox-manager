# F5 implementation review

The fix is based on `6762ace2bd23e3e674a40d6ef2b8345ee88ed5e9`, the exact
`develop` merge with F1, F3 and the F2/F5 documentation.
[Baseline GitHub Actions run](https://github.com/ekkehard-lutz/wallbox-manager/actions/runs/42585250624)

## Runtime scope

[OCPP observation intake](../custom_components/wallbox_manager/ocpp.py)
notifies Grid only when the canonical store accepts a new
`StatusNotification(Faulted)` value for the selected station, EVSE and
connector in the current observation generation. Store scope, ordering and
timestamp rules still decide acceptance. A duplicate held value or a
transition to another status has no F5 effect. The diagnostic message
remains a projection of the observation.

[Grid execution](../custom_components/wallbox_manager/grid.py) advances its
existing command epoch, retires the pending positive request and result
context, and cancels the associated retry. The fault leaves the separate
NETZ business timer armed and emits no OCPP request. Explicit zero-current
work can cross a fault edge. Other lifecycle changes still fence it. Grid
retains prior physical evidence and the existing uncertainty of a write
that may have reached Stationary. The next ordinary NETZ action or PV tick
uses the same policy, authority, permission, release and transaction rules.
Normal F1 handling still applies to new ambiguous rejections.

[PV regulation](../custom_components/wallbox_manager/pv.py) uses the command
epoch after journal persistence and before proposal submission. The fault
retires pending positive proposal and restart authorization, while retaining
the economic budget. A delayed ACK cannot commit the obsolete restart.
An ACK already processed before the fault remains a historical outcome.
A later ACK records protocol acceptance and a valid scoped readback is still
needed for applied-point confirmation.

The event creates no physical current estimate, recovery delay or control
loop. Stationary retains exclusive responsibility for physical and electrical
safety, including the disposition of proposals already sent over the wire.

## Simulated evidence

| Boundary | Reproduction and fixed outcome |
| --- | --- |
| Before the SetChargingProfile wire guard | The archived baseline sent the queued 18 A proposal after the fault. The corrected executor refuses it before transmission. |
| Outstanding SetChargingProfile and economic restart | The fake peer holds the response while sending the fault. The eventual ACK cannot advance command state or clear the durable economic STOP. |
| ACK received before WBM consumes it | The old ACK cannot become current or start its confirmation path. |
| Composite readback received before WBM consumes it | The archived baseline advanced the stored 20 A point to 18 A. The corrected executor retains the historical point and suppresses stale confirmation. |
| PV persistence before submission | The superseded proposal never reaches the wire. The next ordinary tick can independently calculate and submit a new one. |
| Recognized lockout retry | Fault invalidation retires the NETZ retry and PV phase retry context. An already queued NETZ retry callback fails its epoch guard. An in-flight timing approval cannot restore a retry saved before the fault. The NETZ expiry still executes. |

The permanent [F5 replay suite](../tests/test_critical_fault_intent.py)
covers all 15 orderings from the assessment. It also exercises duplicate
and stale reports, wrong connector and prior generation, reconnect and
transaction replacement, clear and unavailable reports, ordinary positive
NETZ and PV proposals during held fault, acceptance without readback,
ambiguous rejection, and operator, SoC and economic STOP. Existing
F1, F3 and NETZ/PV integration tests retain their assertions.

## Source cross-check and limits

- WBM `v0.3.2-beta.18` at `87963014c9af73416c438807f523a3da2a98bd22`.
  `docs/phase-lockout-protocol.md` separates recognized Stationary
  rejection reasons, manager retry eligibility and actual feedback.
- Stationary `279287c3c1d8863e803144eccf773ab3f10889a5`.
  `ocpp_runtime/station.py` lines 75–86 and 499–608 show fault mapping,
  transaction checks, unavailable composite readback under fault and
  independent disposition of incoming proposals.
- openWB `849340ec8d91f4e7cca08a7664cf9ec0187fb496`.
  `packages/control/chargepoint/chargepoint.py` lines 126–188 separate
  charging eligibility from the OCPP transaction-stop path. The Stationary
  interface supplies WBM's actual connector-status contract.
  [Pinned openWB source](https://github.com/openWB/core/blob/849340ec8d91f4e7cca08a7664cf9ec0187fb496/packages/control/chargepoint/chargepoint.py#L126-L188)

Tests simulate OCPP ordering and WBM policy. They exercise no real hardware or
installation. An already transmitted proposal may have had physical effects
before or after its manager result context was superseded.

## Validation

The complete Python suite passed all 1,095 tests, both directly and under
coverage. Integration line coverage was 94% overall, with 97% for Grid,
94% for OCPP and 96% for PV. Both runs reported five dependency deprecation
warnings.

The focused fault, lifecycle, F1, F3, phase-lockout, NETZ/PV integration and
PV diagnostic suites passed all 377 tests. Two timing-sensitive tests
reproduced intermittent failures on the exact baseline under coverage.
Both now wait for the preceding simulated Stop to settle before inspecting
the following behavior. Their original assertions remain in place.

Ruff lint and formatting, JavaScript syntax, all 95 frontend tests and
the headless Chrome fixture passed. Manifest and translation checks
verified valid JSON, consistent localization keys and byte equality to
the baseline. The documentation check resolved 54 local links and anchors.
Git whitespace checks passed.
