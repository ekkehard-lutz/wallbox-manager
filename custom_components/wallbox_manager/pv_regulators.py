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

    Preserve negative balance for ordinary reduction and the shared floor policy.
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
    Ordinary economic ceilings are independent of soft targets. The caller owns
    the exact technical-minimum exception and measured grid-import STOP policy.
    """

    requested: Fraction | None = None
    updated_at: float | None = None
    import_since: float | None = None

    hard_max: Fraction | None = None
    balance_capacity: Fraction | None = None
    evidence: object = None
    consumed: tuple | None = None
    previous_ev: Fraction | None = None
    ev_floor: Fraction | None = None
    command_key: object = None
    command_evidence: tuple | None = None
    command_ev_baseline: Fraction | None = None
    command_offered: Fraction | None = None
    command_delta: Fraction | None = None
    command_since: float | None = None
    dispatch_key: object = None
    dispatch_evidence: tuple | None = None
    dispatch_ev: Fraction | None = None
    dispatch_delta: Fraction | None = None
    excess_since: float | None = None
    excess_generation: object = None
    revision: int = 0
    reason: str = "initial"
    net_import: Fraction = Fraction(0)
    restart_blocked: bool = False
    response_observed: bool = False
    phase_change_pending: bool = False
    increase_authorized: bool = False
    economic_deficit: Fraction = Fraction(0)

    def dispatched(self, point):
        """Freeze physical evidence before the wire write, separately from ACK."""
        self.dispatch_key = (point.charging, point.mode, point.current_a)
        self.dispatch_evidence = self.evidence.times if self.evidence else None
        self.dispatch_ev = self.previous_ev
        self.dispatch_delta = (
            point.offered_power_w - self.command_offered
            if self.command_offered is not None
            else point.offered_power_w - self.previous_ev
            if self.previous_ev is not None
            else None
        )

    def acknowledge(self, point, now):
        """Start response observation at confirmation, never at a later tick."""
        if (
            self.command_since is not None
            and now - self.command_since >= SETTLING_SECONDS
        ):
            self.phase_change_pending = False
        key = None if point is None else (point.charging, point.mode, point.current_a)
        if key != self.command_key:
            if key is None or not key[0]:
                self.phase_change_pending = False
            elif self.command_key and self.command_key[0]:
                self.phase_change_pending |= key[1] != self.command_key[1]
            self.command_key = key
            self.command_since = now
            dispatched = key is not None and key == self.dispatch_key
            self.command_evidence = (
                self.dispatch_evidence
                if dispatched
                else self.evidence.times
                if self.evidence
                else None
            )
            self.command_ev_baseline = self.dispatch_ev if dispatched else None
            self.command_delta = self.dispatch_delta if dispatched else None
            self.command_offered = point.offered_power_w if point is not None else None
            self.response_observed = False
            self.increase_authorized = False
            if point is not None and not point.charging:
                self.requested = Fraction(0)
        if key == self.dispatch_key:
            self.dispatch_key = None
            self.dispatch_evidence = None
            self.dispatch_ev = None
            self.dispatch_delta = None

    def observe_response(self, wallbox, confirmed, *, now, step):
        """Prove fresh physical movement, including genuine power below an offer."""
        self.acknowledge(confirmed, now)
        wallbox = Fraction(str(wallbox))
        offered = confirmed.offered_power_w if confirmed is not None else wallbox
        tolerance = max(GRID_DEADBAND_W, Fraction(str(step)) / 2)
        post_command = self.command_evidence is None or (
            self.evidence is not None
            and all(
                a > b
                for a, b in zip(self.evidence.times, self.command_evidence, strict=True)
            )
        )
        baseline = self.command_ev_baseline
        moved_toward_offer = (
            confirmed is not None
            and confirmed.charging
            and baseline is not None
            and self.command_delta is not None
            and GRID_DEADBAND_W < wallbox <= offered + tolerance
            and abs(wallbox - baseline) > GRID_DEADBAND_W
            and (wallbox - baseline) * self.command_delta > 0
            # Partial power movement is not proof that phase switching settled.
            # Retain the existing phase observation window for this new proof.
            and (
                not self.phase_change_pending
                or now - self.command_since >= SETTLING_SECONDS
            )
        )
        observed = post_command and (
            abs(wallbox - offered) <= tolerance or moved_toward_offer
        )
        # A proved positive response belongs to this command, even if a later
        # under-consuming sample moves away from its offered watts. A new current
        # or phase command resets this proof. OFF still requires fresh no-load.
        if confirmed is not None and confirmed.charging:
            observed |= self.response_observed
        self.response_observed = observed
        if observed:
            self.phase_change_pending = False
        return observed

    def response_settling(self, now):
        """Use the existing ACK observation window for phase selection too."""
        return (
            self.phase_change_pending
            and self.command_since is not None
            and not self.response_observed
            and now - self.command_since < SETTLING_SECONDS
        )

    def executable_max(self, offered, *, energy_desired=False):
        """Bound FAST execution relative to the confirmed, freshly priced offer.

        A retained soft preference is still useful for desired-phase planning,
        but only the ordinary source/response gates can authorize its increase
        over a changed confirmed point. Measured allocation excess must reduce
        the offer even when nominal watts already fit the economic ceiling.
        Each correction uses post-command evidence, never an assumed A-to-W
        vehicle response or a repeated subtraction from the old soft target.
        """
        maximum = None if energy_desired or self.increase_authorized else offered
        if (
            self.economic_deficit > 0
            and self.evidence is not None
            and self.command_evidence is not None
            and all(
                a > b
                for a, b in zip(self.evidence.times, self.command_evidence, strict=True)
            )
        ):
            maximum = max(Fraction(0), offered - self.economic_deficit)
        return maximum

    def update_budget(self, wallbox, discharge, limit, imported, exported, evidence):
        """Same economic-budget evidence tracking for FAST and BALANCE."""
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
        self.economic_deficit = (
            max(Fraction(0), wallbox - self.hard_max)
            if discharge > limit
            else Fraction(0)
        )
        generation = evidence.times
        observed = self.observe_response(wallbox, confirmed, now=now, step=step)
        offered = confirmed.offered_power_w if confirmed is not None else wallbox
        new = self.consumed is None or all(
            a > b for a, b in zip(generation, self.consumed, strict=True)
        )
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
                self.increase_authorized = True
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

    def balance_request(
        self,
        available,
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
        """Follow PV surplus with shared feedback, without spending battery headroom.

        PV/total-load smoothing is an upward preference only. Raw flow bounds
        correct real deficits immediately and cannot credit an EV increase ahead
        of site measurements. Filter lag alone must not cause a downward command.
        """
        if interval <= 0:
            raise ValueError("invalid regulation interval")
        wallbox, discharge, limit, imported, exported = (
            Fraction(str(v)) for v in (wallbox, discharge, limit, imported, exported)
        )
        if min(wallbox, discharge, limit, imported, exported) < 0:
            raise ValueError("negative power measurement")
        self.update_budget(wallbox, discharge, limit, imported, exported, evidence)
        observed = self.observe_response(wallbox, confirmed, now=now, step=step)
        generation = evidence.times
        basis = min(wallbox, self.ev_floor)
        self.balance_capacity = max(
            Fraction(0),
            min(
                evidence.pv - evidence.load + min(basis, evidence.load),
                basis - discharge - self.net_import,
            ),
        )
        target = min(max(0, Fraction(str(available))), self.balance_capacity)
        offered = confirmed.offered_power_w if confirmed is not None else wallbox
        baseline = offered if self.requested is None else self.requested
        requested = min(baseline, self.hard_max, self.balance_capacity)
        self.reason = (
            "hard_budget_reduction"
            if offered > self.hard_max
            else "balance_deficit_reduction"
            if requested < baseline
            else "reused_evidence"
        )
        new = self.consumed is None or all(
            a > b for a, b in zip(generation, self.consumed, strict=True)
        )
        site_crossed_ev = min(generation[1:]) >= generation[0]
        settling = (
            self.command_since is not None
            and not observed
            and now - self.command_since < SETTLING_SECONDS
        )
        if (
            target > baseline
            and new
            and site_crossed_ev
            and self.net_import <= GRID_DEADBAND_W
            and not pending
            and not settling
            and (
                confirmed is None
                or not confirmed.charging
                or self.updated_at is None
                or now - self.updated_at >= interval
            )
        ):
            if observed or self.command_key is None:
                # Reserve half of the as-yet unmeasured EV increment, using PV
                # capacity rather than FAST's configured discharge allocation.
                # Initial PV starts retain their existing allocation and common
                # startup reserve; feedback controls subsequent positive changes.
                increase = (
                    wallbox + max(0, target - wallbox) / 2
                    if confirmed is not None and confirmed.charging
                    else target
                )
                requested = max(requested, min(target, increase))
                self.reason = "new_balance_evidence_increase"
            else:
                self.reason = "unobserved_command_no_increase"
            self.consumed = generation
            self.updated_at = now
            self.revision += 1
        elif requested == baseline and (settling or pending):
            self.reason = "awaiting_physical_response"
        if confirmed is not None and not confirmed.charging and not observed:
            requested = Fraction(0)
            self.reason = "awaiting_pause_response"
        self.requested = Fraction(
            int(min(requested, self.hard_max, self.balance_capacity) * 1000), 1000
        )
        return self.requested

    def minimum_allowed(self, minimum, *, now, actual, continuing=False):
        """Keep physical-response/startup reserves separate from economic STOP.

        The caller must supply the proved exact technical floor. Only an
        ongoing continuation can exceed its ordinary discharge allocation;
        the original startup response reserve and OFF response proof remain.
        """
        watts = minimum.offered_power_w
        if (
            self.command_key is not None
            and not self.command_key[0]
            and not self.response_observed
        ):
            self.reason = "awaiting_pause_response"
            return False
        if self.hard_max is None:
            self.reason = "minimum_response_unavailable"
            return False
        if (
            not continuing
            and actual < watts
            and watts > actual + (self.hard_max - actual) / 2
        ):
            # A floor must not bypass the response reserve used by the upward
            # regulator. Otherwise a start below twice the minimum can produce
            # start/stop cycles as site power arrives before the first EV sample.
            self.reason = "startup_response_reserve"
            return False
        return self.minimum_import_allowed(watts, now=now, actual=actual)

    def minimum_import_allowed(self, watts, *, now, actual):
        """Only the shared verified-energy policy may impose an import STOP."""
        self.reason = "shared_grid_import_policy"
        return True
