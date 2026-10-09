"""Power-only regulation primitives; callers own SoC policy and input freshness."""

from dataclasses import dataclass
from fractions import Fraction

from .pv_budget import (
    IMPORT_CONFIRM_SECONDS,
    SETTLING_SECONDS,
    battery_budget,
)
from .pv_input_diagnostics import diagnostic_fast_request

GRID_DEADBAND_W = 100
IMPORT_GRACE_SECONDS = 3


def pv_balance(pv_power, consumer_power, wallbox_power):
    """Available watts; consumer measurement includes the controlled wallbox.

    Preserve negative balance for the caller's existing pause/delay policy.
    Approximation and electrical operating points belong to the common solver.
    """
    return pv_power - consumer_power + wallbox_power


def fast_discharge(
    wallbox_power,
    battery_discharge,
    max_discharge,
    grid_import,
    grid_export,
    *,
    deadband,
    increase_fraction,
    previous_request=None,
    graced_import=0,
):
    """Request watts with gradual increases and immediate deficit correction.

    Inputs are fresh nonnegative watts, with battery discharge excluding charging.
    The caller supplies tuning; no profile targets or timing state live here.
    Meaningful import suppresses *all* increases, including battery headroom.
    Discharge above the policy limit also reduces the request immediately.
    """
    values = tuple(
        Fraction(str(value))
        for value in (
            wallbox_power,
            battery_discharge,
            max_discharge,
            grid_import,
            grid_export,
            deadband,
            increase_fraction,
        )
    )
    wallbox, discharge, limit, imported, exported, band, increase = values
    if any(value < 0 for value in values) or not 0 <= increase <= 1:
        raise ValueError("invalid regulation measurement or limit")
    net_import = imported - exported
    deficit = net_import if net_import > band else Fraction(0)
    excess_discharge = max(Fraction(0), discharge - limit)
    grace = Fraction(str(graced_import))
    if not 0 <= grace <= max(0, limit - discharge):
        raise ValueError("invalid transient import allowance")
    if deficit or excess_discharge:
        return max(Fraction(0), wallbox - max(0, deficit - grace) - excess_discharge)
    export = -net_import if net_import < -band else Fraction(0)
    headroom = max(Fraction(0), limit - discharge)
    ceiling = wallbox + export + headroom
    previous = wallbox if previous_request is None else Fraction(str(previous_request))
    if previous < 0:
        raise ValueError("negative previous request")
    baseline = min(ceiling, max(wallbox, previous))
    return baseline + increase * (ceiling - baseline)


@dataclass
class FastDischargeRegulator:
    """One evidence-driven power controller; the caller retains SoC and phase policy.

    Source generation and physical response are separate from an acknowledgement.
    Hard budgets are independent of soft targets and the minimum-import allowance.
    """

    requested: Fraction | None = None
    updated_at: float | None = None
    import_since: float | None = None

    hard_max: Fraction | None = None
    evidence: object = None
    consumed: tuple | None = None
    previous_ev: Fraction | None = None
    ev_floor: Fraction | None = None
    command_key: object = None
    command_evidence: tuple | None = None
    command_since: float | None = None
    excess_since: float | None = None
    excess_generation: object = None
    revision: int = 0
    reason: str = "initial"
    net_import: Fraction = Fraction(0)
    restart_blocked: bool = False
    response_observed: bool = False

    def acknowledge(self, point, now):
        """Start response observation at confirmation, never at a later tick."""
        key = None if point is None else (point.charging, point.mode, point.current_a)
        if key != self.command_key:
            self.command_key = key
            self.command_since = now
            self.command_evidence = self.evidence.times if self.evidence else None
            self.response_observed = False
            if point is not None and not point.charging:
                self.requested = Fraction(0)

    def update_budget(self, wallbox, discharge, limit, imported, exported, evidence):
        """Same hard-budget evidence tracking for FAST and BALANCE."""
        self.net_import = imported - exported
        if self.evidence is None or evidence.times[0] != self.evidence.times[0]:
            self.ev_floor = min(
                wallbox, wallbox if self.previous_ev is None else self.previous_ev
            )
            self.previous_ev = wallbox
        # Deliberately not a timestamp-nearness test: until every site channel
        # has crossed the EV acquisition boundary, retain the lower EV basis.
        if min(evidence.times[1:]) >= evidence.times[0]:
            self.ev_floor = wallbox
        self.hard_max = battery_budget(
            wallbox,
            discharge,
            limit,
            imported,
            exported,
            evidence=evidence,
            ev_floor=min(wallbox, self.ev_floor),
        )
        self.evidence = evidence
        if (
            self.command_key is not None
            and not self.command_key[0]
            and self.command_evidence is not None
            and all(
                a > b
                for a, b in zip(evidence.times, self.command_evidence, strict=True)
            )
            and wallbox <= GRID_DEADBAND_W
        ):
            self.response_observed = True

    @diagnostic_fast_request
    def request(
        self,
        wallbox,
        discharge,
        limit,
        imported,
        exported,
        *,
        now,
        interval,
        evidence,
        confirmed=None,
        pending=False,
        step=230,
    ):
        """Consume each complete source generation once; ACK is not EV response."""
        if interval <= 0:
            raise ValueError("invalid regulation interval")
        wallbox, discharge, limit, imported, exported = (
            Fraction(str(v)) for v in (wallbox, discharge, limit, imported, exported)
        )
        if min(wallbox, discharge, limit, imported, exported) < 0:
            raise ValueError("negative power measurement")
        self.update_budget(wallbox, discharge, limit, imported, exported, evidence)
        generation = evidence.times
        self.acknowledge(confirmed, now)
        offered = confirmed.offered_power_w if confirmed is not None else wallbox
        new = self.consumed is None or all(
            a > b for a, b in zip(generation, self.consumed, strict=True)
        )
        post_command = self.command_evidence is None or all(
            a > b for a, b in zip(generation, self.command_evidence, strict=True)
        )
        observed = post_command and abs(wallbox - offered) <= max(100, step / 2)
        self.response_observed = observed
        settling = (
            self.command_since is not None
            and not observed
            and (now - self.command_since < SETTLING_SECONDS)
        )
        baseline = wallbox if self.requested is None else self.requested
        if self.net_import > GRID_DEADBAND_W:
            if self.import_since is None:
                self.import_since = now
        else:
            self.import_since = None
        self.reason = "reused_evidence"
        # Hard reductions always bypass settling and generation/interval gates.
        requested = min(baseline, self.hard_max)
        if offered > self.hard_max:
            self.reason = "hard_budget_reduction"
        if (
            new
            and not pending
            and not settling
            and (
                self.net_import > GRID_DEADBAND_W
                or self.updated_at is None
                or now - self.updated_at >= interval
            )
        ):
            if self.net_import > GRID_DEADBAND_W:
                if (
                    self.command_since is not None
                    and now - self.command_since < SETTLING_SECONDS
                    and now - self.import_since < IMPORT_CONFIRM_SECONDS
                ):
                    self.reason = "grid_response_settling"
                else:
                    requested = max(0, wallbox - self.net_import)
                    self.reason = "zero_import_correction"
            elif observed or self.command_key is None:
                # Reserve the unobserved half of a step response: a newer site
                # sample may already include it while the EV source still lags.
                # Do not accumulate that same headroom across stable EV samples.
                requested = wallbox + max(0, self.hard_max - wallbox) / 2
                self.reason = "new_evidence_increase"
            else:
                # A timeout permits deficit correction, not speculative rises
                # while the EV has still not followed the accepted setpoint.
                self.reason = "unobserved_command_no_increase"
            self.consumed = generation
            self.updated_at = now
            self.revision += 1
        elif settling or pending:
            self.reason = "awaiting_physical_response"
        if confirmed is not None and not confirmed.charging and not observed:
            requested = Fraction(0)
            self.reason = "awaiting_pause_response"
        self.requested = Fraction(int(min(requested, self.hard_max) * 1000), 1000)
        return self.requested

    def minimum_allowed(self, minimum, *, now, actual):
        """Bounded site-import exception, never an additional battery allowance."""
        watts = minimum.offered_power_w
        if (
            self.command_key is not None
            and not self.command_key[0]
            and not self.response_observed
        ):
            self.reason = "awaiting_pause_response"
            return False
        if self.hard_max is None or watts > self.hard_max:
            self.reason = "minimum_exceeds_hard_budget"
            return False
        if actual < watts and watts > actual + (self.hard_max - actual) / 2:
            # A floor must not bypass the response reserve used by the upward
            # regulator. Otherwise a start below twice the minimum can produce
            # start/stop cycles as site power arrives before the first EV sample.
            self.reason = "minimum_response_budget"
            return False
        # The exception is measured total site import, never EV-only import.
        # After a pause require enough projected headroom to avoid immediate
        # pause/restart cycling when removing EV load alone cleared the import.
        projected = self.net_import
        if self.restart_blocked:
            projected += max(0, watts - actual)
        threshold = watts / 2
        generation = self.evidence.generation[2:4]
        if projected > threshold:
            if self.excess_since is None:
                self.excess_since, self.excess_generation = now, generation
            if now - self.excess_since >= IMPORT_CONFIRM_SECONDS:
                self.restart_blocked = True
                self.reason = "minimum_import_exceeded"
                return False
        elif projected <= threshold - GRID_DEADBAND_W:
            self.excess_since = None
            self.excess_generation = None
            self.restart_blocked = False
        if self.restart_blocked:
            self.reason = "minimum_restart_hysteresis"
            return False
        self.reason = "minimum_import_allowance"
        return True
