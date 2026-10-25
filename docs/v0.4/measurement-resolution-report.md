# Electrical measurement resolution and beta.3

Implemented on `fix/transient-observation-availability`, continuing from
`a1c50e8eb4dfa5b532a44e86fed400d0e71ceff2`. The transient-phase fix is preserved:
`store.py` and `ocpp.py` are byte-for-byte unchanged by this resolution change.
The base release remains beta.2 until the separate release-version commit.

## Normalization and compatibility

`meter.samples()` now applies deterministic `Decimal.quantize(ROUND_HALF_UP)`
after validating the source value and converting its unit/multiplier to the
canonical unit. Conflict validation occurs before quantization. Values yielded
by the normalizer are already normalized before `State.observe()` or session
power accounting receives them.

| Metering quantity | Resolution | Canonical unit |
| --- | --- | --- |
| All phase voltages | 0.1 | V |
| All phase currents | 0.001 | A |
| All phase and aggregate active import power | 0.1 | W |
| Cumulative and transaction energy registers | No new rounding | kWh |

Examples: 231.43782 V → 231.4 V; 8.236728 A → 8.237 A; 1906.347 W →
1906.3 W; 1.23456 kW → 1234.6 W. Exact half steps round upward for accepted
nonnegative values, e.g. 231.45 V → 231.5 V and 8.2365 A → 8.237 A.

Electrical conversion uses a local Decimal precision of 64, sufficient for the
accepted source digit/multiplier ranges. This prevents intermediate binary-float
or Decimal-context rounding from moving a half-step boundary. Final electrical
quantization is explicit HALF_UP. Energy retains the previous conversion context
and float conversion semantics. HA-compatible floats are yielded only after
rounding electrical quantities; energy is not quantized.

The existing per-message `found` dictionary checks unrounded converted electrical
values by channel and acquisition timestamp. Contradictory values such as
231.41 and 231.42 V at the same timestamp remain invalid even though each alone
would normalize to 231.4 V. Equivalent values expressed in different supported
units remain valid duplicates; a conflict cannot be undone by a later duplicate.
No persistent unrounded cache is introduced.

Supported V/mV/kV, A/mA, W/kW/MW conversions, multipliers, accepted numeric range,
finite/type checks, invalid values, unsupported channels/contexts and missing
sample semantics remain intact. Unsupported inputs never fabricate zero.

**Intentional public compatibility change:** published voltage/current/power
values now use the resolution above. This is also the canonical value stored for
future internal regulation, rather than entity-only presentation rounding. There
is no second rounding in entities, additional reporting interval, minimum-change
hysteresis, raw-value store, coordinator or timer. Real normalized changes publish
immediately, including oscillation across a rounding boundary.

Lifetime energy, transaction begin/end registers, session energy, reset/rollover
validation and Energy Dashboard values retain their existing conversion and
statistics behavior. The exact cumulative-energy benchmark is separate from the
now intentionally coarser voltage benchmark. No 30-second energy update schedule
or other energy reporting delay is added.

No normalization is applied to commanded current, current-limit readbacks,
capabilities/steps, physical phase evidence, charging permission, authority,
profile preferences, SoC thresholds, session identity/timestamps or generations.
Existing entity IDs, unique IDs, devices, canonical units and statistics metadata
are unchanged. Public acquisition timestamp attributes remain absent; accepted
new source timestamps still refresh internally even when the value normalizes
to the same number. WARNING-based diagnostics remain unchanged.

## Before/after measurement

Same instrumented workload, same OCPP adapter and real HA entities, before and
after the resolution change. Initial observations and source/unit attributes are
settled before counting. Columns show HA writes / `state_changed` / unchanged
`state_reported` / estimated Recorder rows.

| Workload | Before | After |
| --- | --- | --- |
| 2,000 distinct voltage values within the same 0.1 V bin | 2000 / 2000 / 0 / 2000 | 0 / 0 / 0 / 0 |
| One voltage change crossing a half-step boundary | 1 / 1 / 0 / 1 | 1 / 1 / 0 / 1 |
| 1,000 cumulative-energy changes of 0.00000001 kWh | 1000 / 1000 / 0 / 1000 | 1000 / 1000 / 0 / 1000 |

All 2,000 same-bin reports are accepted, and the internally stored acquisition
time equals the last supplied source timestamp. The final voltage is 230.0 V;
230.05 V immediately changes it to 230.1 V. The final energy is 100.00001 kWh
in both runs. [Raw counts and acquisition timestamps](measurement-resolution-benchmark.json).
Recorder rows are estimates from changes, not live DB measurements.

The maintained passive benchmark now passes voltage and energy workloads through
the OCPP normalizer rather than bypassing it with direct store writes. It asserts
zero same-bin voltage writes, immediate boundary publication and all 1,000 exact
energy changes. Repeated identical measurements/phases still produce zero
changes, and active duration still produces 60 updates per simulated hour.
The transient phase sequences retain their tested 0-change single-invalid
recovery and two necessary transitions after two distinct invalid samples.

## Validation and size

All **91 Python tests** and **8 JavaScript tests** pass. The previous 54 tests remain
covered; added parameter cases cover phases, aggregate power, all supported
conversion families, positive/negative multipliers, half steps, large accepted
Decimal inputs, raw conflicts, invalid/missing/non-finite inputs, exact energy,
transaction/ordinary session power consistency and unaffected safety/settings.
Ruff lint and formatting, JavaScript syntax, Python compilation and whitespace
checks pass. Five upstream HA/backoff deprecation warnings remain.

Only `meter.py` changes in production for this feature: **24 lines added,
6 removed, net +18 physical Python lines and +917 bytes** compared with the
phase-fix commit. Combined with that fix, beta.3 production Python is **18 modules,
2,789 lines, 101,234 bytes**, versus beta.2's 18 modules / 2,718 lines / 98,231 bytes
(net +71 lines / +3,003 bytes). The card remains 350 lines / 26,928 bytes.
No production module, cache, timer framework or external dependency is added.

## Remaining limitations

The integration is passive: no charging commands, ownership takeover or PV/grid
regulation. The five-second physical phase deadline and immediate invalidation
on the first invalid phase report remain safety-relevant even while last-observed
values remain visible. Duplicate/out-of-order and generation fencing are intact.
The station-side lock-contention hypothesis behind the phase incidents still
lacks a raw-message capture.

Real normalized changes still create Recorder history, and raw noise near a
rounding boundary can still cross it. Existing Recorder history is neither
rewritten nor deleted. Live CPU and DB-growth effects require installation-time
verification; the measurements here are synthetic HA event counts. The station,
its OCPP reporting intervals and live Home Assistant configuration are unchanged.
