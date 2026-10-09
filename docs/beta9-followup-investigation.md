# beta.9 follow-up investigation — policy clarification pending

The existing uncommitted review implementation and branch were inspected and
preserved. No production code, test expectations or configuration has been
changed by this investigation. The existing 1,910/129 test results remain the
prior review results, not validation of a corrected regulator.

## Five blocked restart cases

All five final observations at simulation second 359 have EV 0 W, battery
1,700 W, raw PV 0 W, total household/site load 1,700 W and net grid 0 W.
The acquisition epoch is 2026-10-09 00:00:00 UTC. All five site channels
(discharge, import, export, PV, total load) have acquisition second 355.
The EV acquisition seconds are listed below; the simulation supplies exact
source acquisition times, not merely HA report times.

Both raw budget equations yield 1,800 W; subtracting the explicit 100 W reserve
leaves B_hard = 1,700 W. The reachable 1p/6 A minimum is 1,380 W. The soft
half-headroom request is 850 W. The extra minimum-response guard demands
2 × 1,380 = 2,760 W and returns minimum_response_budget before evaluating the
remaining import gate.

| Case | EV source second | Site source second | Import restart latch | Final veto |
|---|---:|---:|---|---|
| load_up/3/8 | 350 | 355 | set | minimum_response_budget |
| load_up/3/8/offset3 | 353 | 355 | clear | minimum_response_budget |
| load_up/1/15 | 350 | 355 | set | minimum_response_budget |
| load_up/1/15/offset3 | 353 | 355 | set | minimum_response_budget |
| load_up/variable/offset3 | 353 | 355 | set | minimum_response_budget |

In the four latched cases, the existing import restart calculation would also
project 0 + 1,380 = 1,380 W against the 690 W minimum allowance (590 W recovery
threshold). It credits none of the valid measured battery headroom. Removing
only the double-minimum guard would therefore not resolve these four cases.
A corrected projection must account for supported incremental battery supply
without treating a reduction caused only by switching EV off as new headroom.

All five steady-state snapshots support a controlled minimum start under the
existing power-flow model: the minimum fits the independently established
1,700 W budget. The idealized plant can support 1,610 W EV / 3,310 W battery.
That admission fact is distinct from proving safe continuation through the next
incomplete measurement generation.

## The 470 W steady-state utilization gap

For the representative constant case, measured EV = 2,530 W, battery = 3,030 W,
household = 500 W, PV = 0 W and net grid = 0 W. Both raw bounds are 3,000 W;
the explicit reserve leaves an EV hard budget of 2,900 W. At this stationary
boundary E_low equals EV: no asynchronous lower-basis reserve remains.

The half-headroom rule returns 2,530 + (2,900 − 2,530)/2 = 2,715 W. Downward
1 A quantization selects 11 A / 2,530 W again. Fresh generations continue to
arrive, but the same arithmetic cannot reach 12 A / 2,760 W.

Of the 470 W unused battery capacity, 100 W is explicit reserve, 140 W is the
unavoidable gap from 2,900 W budget to 2,760 W at 12 A, and the extra 230 W is
avoidable underutilization caused by half-headroom plus quantization. The next
13 A point is 2,990 W, above the 2,900 W hard budget. A stable 12 A result would
use 3,260 W battery (93.14%) with 240 W unused capacity. No phase, demand,
generation gate or steady grid constraint prohibits that step.

## Why a direct removal is insufficient

An isolated in-memory experiment removed the double-minimum condition and set
the upward target to B_hard, leaving other behavior unchanged. It did not edit
production files. At 3 s ACK / 8 s EV response, the constant case then produced
24 commands and 23 reversals, compared with 3 commands and zero reversals in
the review implementation. Its final minute alternated 1,380 / 2,760 W.
Both 3/8 load-increase offsets also cycled between 1,380 and 1,610 W. The
slower 1/15 and variable-latency load-increase variants settled at 1,610 W.
This is not an acceptable correction and has not been installed.

The source-order ambiguity is concrete. After a 1,380 W start, an EV source
still reporting its previous 0 W and newer site channels reporting D=C=3,080 W,
PV=G=0 are compatible with both:

1. EV physically drawing 1,380 W and household drawing 1,700 W;
2. EV still drawing zero and household having increased to 3,080 W.

In the first world continuing at minimum is safe. In the second, applying the
same minimum is predicted to exceed the configured discharge limit. The latest
conservative equations yield only 320 W EV budget in both worlds. An ACK does
not distinguish them; neither does the source metadata before a new EV sample.
Selecting/retaining the minimum by adding the acknowledged increment to EV
would silently substitute command intent for measurement evidence.

## Required clarification

The pending question asks whether the last complete established budget may be
retained for at most the existing 20 s response window while newer incomplete
sources imply a smaller worst-case budget. A measured battery-limit breach would
still force immediate reduction; this does not eliminate the ambiguity about an
unobserved EV response plus simultaneous household changes below that threshold.
Such retention therefore needs an explicit revised uncertainty/holding policy.

If the current conservative boundary must remain unchanged, the implementation
must continue to pause when those worlds cannot be distinguished. A universally
stable safe restart under these asynchronous traces cannot be established merely
by removing the two soft reserves. Stronger synchronized EV/site evidence or an
independent household measurement could resolve the ambiguity, but neither may
be invented from these simulations.

This clarification follows the user's follow-up section 12: “Stop and request
clarification if a safety-relevant requirement cannot be satisfied without
changing the agreed control policy.” No commit, merge, tag, release or deployment
has been made. The original metric artifact is preserved unchanged.
