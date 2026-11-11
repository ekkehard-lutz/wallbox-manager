# Passive v0.4 architecture [APPROVED]

**HISTORICAL milestone with retained requirements.** The 17 October 2026
passive-core decision established the entry, identity, observation and
bootstrap boundaries. V04-03, the passive action limits in V04-06 and the
execution prohibition in V04-07 were superseded by [active control](active-ocpp-control.md),
[charge release](charge-release.md) and the active profile contracts.
WBM-ARCH-01–12 govern the current responsibility split. PV-ARCH-09 remains
applicable within the active PV scope.

| ID | Approved requirement | Milestone |
| --- | --- | --- |
| V04-01 | One config entry manages exactly one station, EVSE, connector, listener, observation store and settings set. Persist explicit binding; require a complete explicit binding and reject conflicting current profile or registry identities before mutation. | Current |
| V04-02 | Multiple wallboxes use independent entries/endpoints, lifecycles, observations and settings. No global selector, central ownership manager or shared mutable charging state. | Current |
| V04-03 | Implement passive OCPP observations only. No takeover, physical charging/phase commands, regulator, economic bookkeeping or charging timers. Backend control requests must fail explicitly. | SUPERSEDED as described above |
| V04-04 | Preserve current entity unique IDs, HA entity IDs, physical device identifiers, user metadata and associations on reload. Historical registry reconstruction and Recorder/statistics continuity are outside the supported upgrade scope. | Current |
| V04-05 | Native HA structure: one physical wallbox directly under each config entry, no wallbox subentry/add-wallbox flow. Reject historical subentry layouts without mutation or reparenting. Keep existing historical records inert. | Current |
| V04-06 | Retain recognizable German/English card and editing, scoped to one entry; remove multi-wallbox behavior. Take-control and permission buttons visible but disabled; actual permission independently visible. | SUPERSEDED as described above |
| V04-07 | Profile settings remain editable and persistent, with validation and zero physical action. Active charging functionality explicitly unavailable. | SUPERSEDED as described above |
| V04-08 | Canonical observation store → affected entity only. Ordinary measurements last-value-held within generation, no expiry scanning; accurate acquisition metadata stays internal; identical reads do not publish public timestamps (WBM-ARCH-05/12). | Current |
| V04-09 | OCPP2.1 session attribution requires successful registration in the current adapter generation. Pre-registration TransactionEvents receive the ordinary response without mutating transaction history or attributing measurements. Initialization/readbacks are generation-fenced; supported queries only, inventory is not live metering. Retain 20s Ping/Pong and justified five-second permission readback. Document initial reports that must arrive unsolicited. Public initialization diagnostics expose `status` and `missing`. | Current |
| V04-10 | Active regulation implements authority, permission, SoC, forecast, economic, grid and reconnection policy under WBM-ARCH-01–12. Its former manager physical-evidence/phase/current safety gating is explicitly superseded; the station owns physical protection. Detailed profile requirements govern implementation. | Current under WBM-ARCH |
| V04-11 | Independent regulators see other EV demand as household load. No fairness/stability guarantee: oscillation/starvation possible. Slow increase/faster decrease may help but is not coordination. True coordinated multi-wallbox energy management belongs to a future independent Energy Manager. | Deferred/outside WBM |
| V04-12 | Target passive average HA Core CPU increase <=0.1 percentage points in longer alternating real enabled/disabled measurements. Unit tests/call counts cannot establish acceptance. beta.18 observation ~1.81% vs ~0.41% disabled is context, not a controlled guarantee. | Live acceptance deferred |

See the [current baseline](v0.4-current-requirements.md) for the implemented scope and acceptance boundaries.
