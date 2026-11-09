# Passive v0.4 architecture [APPROVED]

Source: explicit user task “Wallbox Manager v0.4 – Complete Clean Rebuild from a
Minimal Passive Core”, 17 October 2026. These decisions govern this milestone, subject to the later overarching
[WBM-ARCH-01–12 approval](architecture-responsibility.md) of 18 October 2026.
The consolidated V3 remains DRAFT overall. Its individually approved decisions,
including PV-ARCH-09, remain valid for future regulation. Historical implementation
reviews/performance reports are evidence, not architectural requirements.

| ID | Approved requirement | Milestone |
| --- | --- | --- |
| V04-01 | One config entry manages exactly one station, EVSE, connector, listener, observation store and settings set. Persist explicit binding; require a complete explicit binding and reject conflicting current profile or registry identities before mutation. | Current |
| V04-02 | Multiple wallboxes use independent entries/endpoints, lifecycles, observations and settings. No global selector, central ownership manager or shared mutable charging state. | Current |
| V04-03 | Implement passive OCPP observations only. No takeover, physical charging/phase commands, regulator, economic bookkeeping or charging timers. Backend control requests must fail explicitly. | Current |
| V04-04 | Preserve current entity unique IDs, HA entity IDs, physical device identifiers, user metadata and associations on reload. Historical registry reconstruction and Recorder/statistics continuity are outside the supported upgrade scope. | Current |
| V04-05 | Native HA structure: one physical wallbox directly under each config entry, no wallbox subentry/add-wallbox flow. Reject historical subentry layouts without mutation or reparenting. Keep existing historical records inert. | Current |
| V04-06 | Retain recognizable German/English card and editing, scoped to one entry; remove multi-wallbox behavior. Take-control and permission buttons visible but disabled; actual permission independently visible. | Current |
| V04-07 | Profile settings remain editable and persistent, with validation and zero physical action. Active charging functionality explicitly unavailable. | Current |
| V04-08 | Canonical observation store → affected entity only. Ordinary measurements last-value-held within generation, no expiry scanning; accurate acquisition metadata stays internal; identical reads do not publish public timestamps (WBM-ARCH-05/12). | Current |
| V04-09 | OCPP2.1 initialization/readbacks are generation-fenced; supported queries only, inventory is not live metering. Retain 20s Ping/Pong and justified five-second permission readback. Document initial reports that must arrive unsolicited. Public initialization diagnostics expose `status` and `missing`. | Current |
| V04-10 | Future regulation implements authority, permission, SoC, forecast, economic, grid and reconnection policy under WBM-ARCH-01–12. Its former manager physical-evidence/phase/current safety gating is explicitly superseded; the station owns physical protection. No speculative implementation now. | Deferred |
| V04-11 | Independent regulators see other EV demand as household load. No fairness/stability guarantee: oscillation/starvation possible. Slow increase/faster decrease may help but is not coordination. True coordinated multi-wallbox energy management belongs to a future independent Energy Manager. | Deferred/outside WBM |
| V04-12 | Target passive average HA Core CPU increase <=0.1 percentage points in longer alternating real enabled/disabled measurements. Unit tests/call counts cannot establish acceptance. beta.18 observation ~1.81% vs ~0.41% disabled is context, not a controlled guarantee. | Live acceptance deferred |

No manifest/version/tag/release/deployment/hardware change is authorized here.
A real HA backup is required before the separately authorized first live beta
installation. beta.18 remains the rollback code reference. No reinstall or manual
entity/statistics recreation is acceptable.
