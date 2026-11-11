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

## Draft, implementation evidence and open acceptance

- [PV Regulation V3](pv-regulation.md) remains **DRAFT overall**. Its individually
  **APPROVED** requirements apply with the stated precedence and supersessions.
  Read the detailed PV specification and the relevant operating-point,
  release and diagnostics decisions before changing PV.
- Active NETZ and the three PV profiles are implemented and covered by automated
  tests. The [review and evidence map](../v0.4/requirements-consolidation-review.md)
  lists the exact implementation baseline, unresolved decisions and physical
  acceptance still required. Software tests and simulated OCPP are separate
  from installation evidence.

## History and supersessions

- [Decision history](decision-history.md) records operator decisions and replaced
  designs. Implementation reviews are evidence for their named commits.
- [Automatic wake-up](automatic-vehicle-wakeup.md) records the superseded WBM
  enable switch. Configuration and physical wake-up belong to Stationary.
  [Manual wake-up](manual-vehicle-wakeup.md) records the historical WBM UI scope
  and the station-side compatibility interface.
- [Beta.22 release notes](../v0.4/release-notes-beta.22.md) and
  [release validation](../v0.4/release-validation-beta.22.md) describe the
  published prerelease. Earlier notes and validation are historical records in
  [v0.4 evidence](../v0.4/).

Track requirement IDs, precedence, observed behavior, regression cases and
hardware evidence separately. A change to target behavior requires explicit
approval. Write tasks and technical documentation in English. Keep
implementation, review, merge, publication and physical acceptance separate.
