# Wallbox Manager v0.4.0-beta.24

This prerelease is for controlled field testing on a private installation.
The beta.23 field record covers ordinary NETZ operation and prior
battery-equipped PV operation. The beta.24 fixes and HA restart recovery
still need physical acceptance on the installed equipment.
[Operator field acceptance register](https://github.com/ekkehard-lutz/wallbox-manager/blob/a4d5d9cc19a833c8246d4921caab6b8da1a544f0/docs/v0.4/field-acceptance-beta.23.md)

| Finding | Change |
| --- | --- |
| F01 · Session duration | Lovelace reads the duration sensor's exposed Home Assistant unit and converts it before formatting hours and minutes. |
| F02a · Stationary reconnect | NETZ rechecks the operating point using fresh, scoped Stationary evidence. A matching offer needs no repeat write. Missing confirmation remains visible. |
| F02b · Battery reserve | A running, authorized charge keeps its owned temporary reserve while point confirmation is incomplete. Genuine charge retirement still restores the owned setting, and external changes take precedence. |
| F03 · Local authority | NETZ Start and Apply require observed Remote authority and hardware permission. Draft editing remains available in Local and Stop can retire the release while dispatch is unavailable. |
| FR-01 · Home Assistant restart | A durably approved NETZ schedule keeps its original absolute deadlines and can resume with sufficient current Stationary evidence. Ordinary integration reload still retires manager release. |
| FR-02 · NETZ power | Fixed nearest selection uses nominal 230 V per phase and retains the confirmed phase within its legal range. The configurable approximation Select was removed. Existing PV regulation is expected to behave as before. |

The first historical reserve restoration preceded the recorded Stationary boot.
The exact trigger for each F02 restoration remains open.
[Implementation handoff](beta.24-candidate.md)

Only the operator's explicit Take Control action requests Remote authority.
Stationary retains responsibility for electrical protection and physical
interlocks. Wallbox Manager can reconcile an elapsed deadline once Home
Assistant is back and Stationary evidence permits it. Deadline enforcement
by Wallbox Manager is unavailable while Home Assistant is offline.
[Responsibility boundary](../specifications/architecture-responsibility.md) ·
[Timing requirements](../specifications/operational-display-and-timing.md)

The existing RR-01 session journal limitation still applies. Lost or
unresolved journal history can leave replay protection incomplete.
NETZ recovery relies on valid transaction evidence. Review the audit
before upgrading an installation with unresolved journal damage.
[Readiness audit](release-readiness-audit.md#16-prioritized-findings-and-operator-decisions)

## HACS installation and rollback

1. Create a complete, restorable Home Assistant backup. Retain the existing
   `.storage/wallbox_manager.*` records and any quarantine evidence. Record the
   currently installed version, Stationary version, and entry and entity IDs.
2. With charging idle for the initial check, use the Wallbox Manager entry in
   HACS to select **v0.4.0-beta.24** from the available versions. HACS uses the
   standard tagged GitHub source archive. Follow HACS's restart prompt in the
   operator's controlled test window.
3. Verify that the installed manifest reads **0.4.0-beta.24**, Stationary
   reconnects, and existing entity IDs remain present apart from the retired
   Select. Check the observed authority, manager release, and command history
   for unexpected charging authorization or commands before starting a test.
4. Keep the backup until physical acceptance is complete. For rollback,
   select the previously recorded HACS version. Restore the complete
   pre-upgrade backup if you need to recover the prior storage and registry
   state.

[Release validation and physical retest checklist](release-validation-beta.24.md)
