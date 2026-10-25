# v0.4.0-beta.10

Initial hardware-test prerelease of the first executable **NETZ / Grid fixed-power
profile**, integrating the reviewed coordinated implementation and the reviewed
Home Assistant restart/battery-reserve recovery correction.

**Requires the matching Stationary NETZ implementation:** approved Stationary
feature `e9f7e22db70f54c7ca86bef778326f6d0f8680df`, integrated on develop as
`ce823cf52faa511475ef87a776c7a0dce28e1184`. The old Stationary OCPP capability
inventory is **not compatible with the new NETZ execution requirements**.
Publication alone does not deploy either component. Hardware acceptance remains
outstanding; installation and testing will be performed manually and separately.

## Fixed operating point and phase selection

- NETZ resolves the saved `soll_power` to an electrical operating point and submits
  the supported transaction-scoped OCPP 2.1 Absolute TxProfile. Confirmed unchanged
  points are never cyclically retransmitted. Power edits use a one-second
  trailing-edge debounce. Requested power, acknowledgement, confirmed offered
  current/phases/power and physically measured vehicle draw remain distinct.
- Calculate ranges dynamically for eligible 1p/2p/3p modes from advertised station
  capabilities, voltage basis and each mode's own allowed-current limit. For equal
  voltages, `P_min(n)=n×U×MinimumCurrent` and `P_max(n)=n×U×allowed_current_np`.
  The boundary between consecutive eligible modes is
  `(P_max(lower)+P_min(higher))/2`; an exact tie selects the lower phase count.
  The illustrative 4,370 W boundary is not a constant. Valid observed phase
  voltages are used consistently, otherwise nominal 230 V is the planning basis.
- Preserve an ongoing positive offer's confirmed physical phase count while the
  new requested power remains inside that mode's admissible range. Apply
  DOWN/NEAREST/UP whole-ampere approximation inside the selected mode. The old
  percentage deviation setting and measured-vehicle-phase optimization are retired.
- Allowed-current settings accept whole amperes: **0 A completely disables a phase
  mode**, otherwise station minimum through its effective maximum. Preserve legacy
  user limits conservatively; never silently increase them. Show editors only for
  advertised modes. **The installed Stationary supports 1p and 3p only; generic
  2p calculations do not add 2p hardware or switching capability.**
- The coordinated read-only diagnostic maxima are connector-scoped
  **MaximumCurrent** and **MaximumAsymmetryCurrent**, replacing the old phase-specific
  maximum names. MinimumCurrent, CurrentStep and SupportedPhaseModes remain
  EVSE-scoped diagnostics. For 1p/2p, the effective ceiling is the minimum of both
  new maxima; 3p uses MaximumCurrent. Stationary retains final physical authority.

## Lockouts and optional timing

- Stationary distinguishes **PhaseSwitchLockout** and **ReenableLockout**, with
  charging re-enable taking precedence when both apply. Station-owned defaults
  remain 300 s for phase switching and 600 s for positive-current re-enable after
  a confirmed pause. Rejected targets are not queued in Stationary.
- StatusInfo may carry `additionalInfo=remainingSeconds=<integer>`, rounded upward
  from the remaining monotonic duration. WBM retries recognized lockouts after
  that duration **plus 5 seconds**, or after **60 seconds** when the value is missing
  or malformed. Unrelated rejection, busy, unsupported and communication failures
  do not start indefinite lockout retries. Each retry resolves current valid intent
  again; superseded intent cannot replay. The existing offer remains separately
  visible and is not mislabeled as fulfillment of the rejected desired point.
- Optional one-shot start delay and charging duration are restored. Explicit
  **00:00 duration expires immediately**; expiry requests ChargingEnabled OFF even
  without a vehicle transaction. Pending settings and active deadlines retain their
  scheduling/cancellation semantics. A delayed zero-current pause remains subject
  to Stationary's re-enable lockout; finite windows do not bypass that policy.
  No TxProfile is invented without a valid transaction.

## Temporary BYD / home-battery discharge reserve

Only a confirmed NETZ session actually charging may acquire the temporary storage
reserve. The original reserve is saved durably before a write; the temporary value
is the greater of the original and the number-step floor of the lesser of requested
Grid reserve and observed battery SoC, within entity bounds. HA state subscriptions
adjust it without a polling regulator or duplicate OCPP operating-point command.
Missing battery references disable only this adjustment, not NETZ power selection.

Compare-before-restore, numeric service/readback confirmation, external-change
ownership release, persistent journal and restoration/error recovery are preserved.
The reviewed restart correction also restores the original after an authoritative
current-generation Ended transaction or confirmed physical vehicle absence, even
when Remote authority, ChargingEnabled=true and NETZ remain selected. Missing,
unavailable, historical or incomplete observations and a charging pause are not
proof of termination. Legitimate ongoing charging adopts the journal without a
**40→20→40** reserve oscillation. Same-value external writes and final HA service
races cannot provide atomic writer identification.

## Stationary recovery and preserved behavior

Stationary independently restores the persisted desired OCPP current after service
or Raspberry Pi restart, including **0 A**. While running, an observed EVSE read
interruption followed by configured boot-current fallback can trigger guarded
restoration of the last confirmed Remote target. WBM does not periodically resend
an operating point to compensate for EVSE boot current, and reconnect alone does
not authorize unconditional replay.

**Known detection limitation:** the DIN interface supplies no positive reboot
counter/uptime marker. A reset missed between samples or without boot-current
fallback cannot be positively identified. An external writer selecting boot current
after a read interruption is indistinguishable from fallback. Arbitrary current
deviations are not treated as proof of reboot.

Explicit authority takeover, independent charging permission, Local authority,
transaction/session continuity, accounting, metering and automatic vehicle wake-up
behavior remain preserved. Stationary owns current/phase protection, physical relay
feedback and CP safety. WBM does not automatically acquire authority or override
explicit OFF. Zero-current pause remains separate from permission. PV execution
and the broader draft PV V3 remain outside this release.

## Validation, acceptance and publication

The approved WBM feature head is `212afafc7f2a34911d1042d024cf0eaae26f57d0`;
its full specification/implementation/recovery history is retained through the
non-fast-forward develop merge `13eff0ec4ae015d61c56aab461a5a799a18366f9`.
Release preparation changes only manifest metadata and release documentation.
Dependencies and config-entry/storage versions are unchanged. HACS consumes the
annotated tag's tracked GitHub source archive; no separate ZIP asset is required.
See [release validation](release-validation-beta.10.md) for exact gates and results.
CI must pass on the exact final develop release commit and the annotated tag before
publication. The release is **Prerelease: true**, **Latest: false**, **Draft: false**.

Physical paired inventory/readback, EVSE reboot/fallback and 0 A restoration,
1p/3p switching, lockout timings, CP/automatic wake-up, installed HA battery-number
behavior and live CPU acceptance remain outstanding. Simulated peers, fake clocks,
HA fixtures and hardware stubs do not prove physical acceptance or actual EV response.
Both repositories' main branches remain unchanged. Stationary receives no release,
tag or version change. Publication performs no deployment, configuration change,
Home Assistant interaction, production service restart or charging-hardware command.
