# Wallbox Manager specifications

1. [**Wallbox Manager v0.4 — Current Requirements Baseline**](v0.4-current-requirements.md)
   is the entry point for the v0.4 Lastenheft, implemented product scope,
   approval status and acceptance boundaries.

## Approved supporting requirements

| Scope | Source and applicability |
| --- | --- |
| Responsibility, observations, private LAN and VPN | [WBM-ARCH-01–12 and LAN decision](architecture-responsibility.md) take precedence over conflicting older material. |
| Entry binding, identities and initialization | [V04 milestone](passive-v0.4.md) retains the applicable entry and observation clauses. Its passive-only scope is historical. |
| Explicit takeover, supported writes and truthful outcomes | [WBM-CTRL](active-ocpp-control.md) and the later [release and hardware permission decisions](charge-release.md). |
| NETZ nominal point, timing and reserve | [WBM-GRID](grid-profile.md), [WBM-TIME](operational-display-and-timing.md) and [WBM-NETZ-ACTION](charge-release.md). |
| PV operating-point selection, EV response and phase restriction | [PV-STEP, PV-RESP, PV-TUNE and PV-LOCK](pv-operating-point-response.md) carry their individual decisions and open clauses. |
| HA and Lovelace presentation | [Display and messages](operational-display-and-timing.md), [WBM-HS](lovelace-home-battery-display.md), [WBM-PV-TARGET](pv-target-soc-sensor.md) and [PV-DIAG](pv-decision-diagnostics.md). |

## Draft and evidence

[PV Regulation V3](pv-regulation.md) remains a draft overall. Its individual
approved decisions have their stated scope. The
[decision history](decision-history.md) records supersession and provenance.
For current implementation evidence and field boundaries, see the
[stable validation](../v0.4/release-validation-v0.4.0.md) and
[field acceptance](../v0.4/field-acceptance.md). Stationary owns automatic
vehicle wake-up configuration and execution under WBM-RELEASE-14.
