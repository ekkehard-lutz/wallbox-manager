"""One exact, measured-import economic budget, independent of command execution.

The caller validates ownership, source freshness and technical threshold proof.
Distinct samples close supported intervals; merely reading this policy never
advances time. Source times establish ordering/coverage, while first monotonic
acceptance places causal threshold and confirmed charging-lifecycle changes.
"""

from collections.abc import Hashable
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

POLICY_VERSION = 2
LIMIT_WS = Fraction(180000)
MAX_INTERVAL_SECONDS = Fraction(10)
RESET_SECONDS = Fraction(15)
ECONOMIC_REASON = "grid_import_energy_limit"
SAMPLE_CONVENTION = "previous_sample_closed_first_acceptance"
RECENT_GENERATIONS = 32
RESTART_REFERENCE_VERSION = 1
RESTART_MODEL = "signed_capacity_minus_technical_minimum"


def _fraction(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not an economic measurement")
    try:
        return value if isinstance(value, Fraction) else Fraction(str(value))
    except (ValueError, ZeroDivisionError, TypeError, OverflowError) as error:
        raise ValueError("invalid exact economic value") from error


def _exact_record(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def _restore_exact(record, *, positive=False, signed=False):
    if not isinstance(record, dict):
        raise ValueError("missing exact economic value")
    numerator, denominator = record.get("numerator"), record.get("denominator")
    if (
        type(numerator) is not int
        or type(denominator) is not int
        or denominator <= 0
        or (not signed and numerator < 0)
        or (positive and numerator <= 0)
    ):
        raise ValueError("invalid exact economic value")
    return Fraction(numerator, denominator)


def restart_capacity(
    ev_w,
    imported_w,
    exported_w,
    *,
    reserve_w,
    discharge_w=None,
    allowed_discharge_w=None,
    pv_w=None,
    load_w=None,
):
    """Signed existing-model EV capacity, independent of regulator history.

    The caller supplies the existing reserve and intended battery allocation
    (configured FAST allocation, zero in BALANCE). Direct measured EV power and
    fresh OFF-response/source proof are prerequisites supplied by the caller.
    Partial battery inputs are invalid, never a no-battery fallback.
    """
    ev, imported, exported, reserve = (
        _fraction(value) for value in (ev_w, imported_w, exported_w, reserve_w)
    )
    if min(ev, imported, exported, reserve) < 0:
        raise ValueError("invalid restart-capacity measurement")
    battery_inputs = (discharge_w, allowed_discharge_w, pv_w, load_w)
    grid = imported - exported
    if all(value is None for value in battery_inputs):
        return ev - grid
    if any(value is None for value in battery_inputs):
        raise ValueError("incomplete battery restart capacity")
    discharge, allocation, pv, load = map(_fraction, battery_inputs)
    if min(discharge, allocation, pv, load) < 0:
        raise ValueError("invalid battery restart-capacity measurement")
    return (
        min(ev + allocation - discharge - grid, ev + allocation + pv - load) - reserve
    )


@dataclass(frozen=True)
class RestartReference:
    """Exhausted-episode capacity retained through its required economic restart."""

    capacity_w: Fraction
    minimum_w: Fraction

    def __post_init__(self):
        for name in ("capacity_w", "minimum_w"):
            object.__setattr__(self, name, _fraction(getattr(self, name)))
        if self.minimum_w <= 0:
            raise ValueError("restart reference requires a positive technical minimum")

    @property
    def score_w(self):
        return self.capacity_w - self.minimum_w

    def serialize(self):
        return {
            "version": RESTART_REFERENCE_VERSION,
            "model": RESTART_MODEL,
            "capacity_w": _exact_record(self.capacity_w),
            "minimum_w": _exact_record(self.minimum_w),
        }

    @classmethod
    def restore(cls, record):
        if (
            not isinstance(record, dict)
            or type(record.get("version")) is not int
            or record.get("version") != RESTART_REFERENCE_VERSION
            or record.get("model") != RESTART_MODEL
        ):
            raise ValueError("invalid economic restart reference")
        return cls(
            _restore_exact(record.get("capacity_w"), signed=True),
            _restore_exact(record.get("minimum_w"), positive=True),
        )


def restart_improved(reference, capacity_w, candidate_w, *, deadband_w):
    """A relative anti-cycle gate; existing start/response fences stay external."""
    capacity, candidate, deadband = map(
        _fraction, (capacity_w, candidate_w, deadband_w)
    )
    if candidate <= 0 or deadband < 0:
        raise ValueError("invalid economic restart comparison")
    if reference is None:
        return False
    if not isinstance(reference, RestartReference):
        raise ValueError("invalid economic restart reference")
    return capacity - candidate > reference.score_w + deadband


@dataclass(frozen=True)
class MeterSample:
    generation: Hashable
    source_at: Fraction
    accepted_at: Fraction
    import_w: Fraction

    def __post_init__(self):
        try:
            hash(self.generation)
        except TypeError as error:
            raise ValueError("unhashable meter generation") from error
        for name in ("source_at", "accepted_at", "import_w"):
            object.__setattr__(self, name, _fraction(getattr(self, name)))
        if self.import_w < 0:
            raise ValueError("negative measured import")


class EconomicState(StrEnum):
    CLEAR = "clear"
    EXCEEDING = "exceeding"
    BELOW_RESET_PENDING = "below_reset_pending"
    BUDGET_EXHAUSTED = "budget_exhausted"
    OFF_PENDING = "off_pending"
    ECONOMIC_PAUSED = "economic_paused"
    SUSPENDED_UNKNOWN = "suspended_unknown"


@dataclass(frozen=True)
class EconomicSnapshot:
    energy_ws: Fraction
    threshold_w: Fraction | None
    import_w: Fraction | None
    below_seconds: Fraction
    active: bool
    off_pending: bool
    off_in_flight: bool
    paused: bool
    restart_required: bool
    pause_reason: str | None
    state: EconomicState
    coverage_reason: str | None
    restart_reference: RestartReference | None

    @property
    def exhausted(self):
        return self.energy_ws >= LIMIT_WS

    @property
    def energy_wh(self):
        return self.energy_ws / 3600


@dataclass(frozen=True)
class _Event:
    at: Fraction
    threshold_w: Fraction | None = None
    active: bool | None = None


class GridImportPolicy:
    """Session-bound proved energy; commands and restart permission stay external."""

    def __init__(
        self,
        energy_ws=Fraction(0),
        *,
        active=False,
        off_pending=False,
        off_in_flight=False,
        paused=False,
        restart_required=None,
        pause_reason=None,
        restart_reference=None,
    ):
        energy_ws = _fraction(energy_ws)
        if energy_ws < 0:
            raise ValueError("negative economic budget")
        if restart_required is None:
            restart_required = paused
        if any(
            type(value) is not bool
            for value in (active, off_pending, off_in_flight, paused, restart_required)
        ):
            raise ValueError("invalid economic lifecycle flag")
        if pause_reason not in (None, ECONOMIC_REASON):
            raise ValueError("unrecognized economic pause reason")
        if off_in_flight and not off_pending:
            raise ValueError("dispatched economic OFF requires its pending cause")
        if (
            off_pending
            and energy_ws < LIMIT_WS
            and not (off_in_flight or restart_required)
        ):
            raise ValueError("pending economic OFF requires exhaustion or dispatch")
        if (
            (restart_required or off_in_flight)
            and energy_ws < LIMIT_WS
            and pause_reason != ECONOMIC_REASON
        ):
            raise ValueError("economic pause or dispatch requires its cause")
        if paused and not restart_required:
            raise ValueError("confirmed pause requires economic restart provenance")
        if paused and (active or off_pending or off_in_flight):
            raise ValueError("contradictory confirmed economic pause")
        if restart_reference is not None and not isinstance(
            restart_reference, RestartReference
        ):
            raise ValueError("invalid economic restart reference")
        self.restart_reference = restart_reference
        self.energy_ws = energy_ws
        self.active = active
        self.off_pending = off_pending
        self.off_in_flight = off_in_flight
        self.paused = paused
        self.restart_required = restart_required
        self.pause_reason = ECONOMIC_REASON if energy_ws >= LIMIT_WS else pause_reason
        self._anchor = None
        self._anchor_threshold = None
        self._anchor_active = active
        self._threshold = None
        self._below_seconds = Fraction(0)
        self._events = []
        self._generations = {}
        self._source_high_water = None
        self._acceptance_high_water = None
        self.coverage_reason = "no_measurement_anchor"

    @property
    def exhausted(self):
        return self.energy_ws >= LIMIT_WS

    @property
    def energy_wh(self):
        return self.energy_ws / 3600

    @property
    def threshold_w(self):
        return self._threshold

    @property
    def last_import_w(self):
        return None if self._anchor is None else self._anchor.import_w

    @property
    def below_seconds(self):
        if (
            self._anchor is None
            or self._threshold is None
            or self._anchor.import_w > self._threshold
        ):
            return Fraction(0)
        return self._below_seconds

    @property
    def state(self):
        if self.paused:
            return EconomicState.ECONOMIC_PAUSED
        if self.off_pending:
            return EconomicState.OFF_PENDING
        if self.exhausted:
            return EconomicState.BUDGET_EXHAUSTED
        if self._anchor is None:
            return EconomicState.SUSPENDED_UNKNOWN
        if self._anchor.import_w > self._threshold:
            return EconomicState.EXCEEDING if self.active else EconomicState.CLEAR
        if self.energy_ws:
            return EconomicState.BELOW_RESET_PENDING
        return EconomicState.CLEAR

    @property
    def snapshot(self):
        return EconomicSnapshot(
            self.energy_ws,
            self._threshold,
            self.last_import_w,
            self.below_seconds,
            self.active,
            self.off_pending,
            self.off_in_flight,
            self.paused,
            self.restart_required,
            self.pause_reason,
            self.state,
            self.coverage_reason,
            self.restart_reference,
        )

    def invalidate(self, reason="unsupported_interval"):
        """Discard an unclosed tail at an input/authorization uncertainty boundary."""
        self._anchor = None
        self._anchor_threshold = None
        self._anchor_active = self.active
        self._events.clear()
        self._below_seconds = Fraction(0)
        self.coverage_reason = reason
        return self.snapshot

    def _event(self, event):
        if self._anchor is None:
            return
        if event.at < self._anchor.accepted_at or (
            self._events and event.at < self._events[-1].at
        ):
            self.invalidate("out_of_order_policy_event")
            return
        self._events.append(event)

    def set_threshold(self, threshold_w, *, at):
        threshold_w, at = _fraction(threshold_w), _fraction(at)
        if threshold_w <= 0:
            raise ValueError("technical threshold must be positive")
        if threshold_w != self._threshold:
            self._event(_Event(at, threshold_w=threshold_w))
            self._threshold = threshold_w
        return self.snapshot

    def set_active(self, active, *, at):
        if type(active) is not bool:
            raise ValueError("invalid accounting applicability")
        if active != self.active:
            self._event(_Event(_fraction(at), active=active))
            self.active = active
        if active and self.paused:
            # Physical positive readback is not an authorized restart ACK.
            self.paused = False
        return self.snapshot

    def mark_off_pending(self):
        if not (self.exhausted or self.off_in_flight or self.restart_required):
            raise ValueError("economic OFF requires an exhausted budget")
        self.off_pending = True
        self.paused = False
        self.pause_reason = ECONOMIC_REASON
        return self.snapshot

    def mark_off_in_flight(self):
        """Record actual guarded wire dispatch, never just queueing or solving."""
        if not self.off_pending or self.paused or self.pause_reason != ECONOMIC_REASON:
            raise ValueError("economic dispatch requires a live pending cause")
        self.off_in_flight = True
        return self.snapshot

    def mark_off_not_applied(self):
        """Known BUSY/unsent stale removes wire uncertainty, preserving proved E."""
        self.off_in_flight = False
        if not self.restart_required and not self.exhausted:
            self.off_pending = False
            self.pause_reason = None
            self.restart_reference = None
        return self.snapshot

    def confirm_off(self, *, at):
        self.set_active(False, at=at)
        self.off_pending = False
        self.off_in_flight = False
        self.paused = True
        self.restart_required = True
        self.pause_reason = ECONOMIC_REASON
        return self.snapshot

    def capture_restart_reference(self, capacity_w, minimum_w):
        """Capture once per exhausted episode or required economic restart."""
        if not self.exhausted:
            raise ValueError("restart reference requires an exhausted budget")
        if self.restart_reference is None:
            self.restart_reference = RestartReference(capacity_w, minimum_w)
        return self.restart_reference

    def confirm_active(self, *, at):
        # A fresh authorized response may have already ended the old pause and
        # begun a new episode before this positive command's protocol ACK.
        # The late ACK cannot cancel that newer verified economic STOP.
        new_stop = self.exhausted or (self.off_pending and not self.restart_required)
        self.restart_required = False
        self.paused = False
        if not new_stop:
            self.restart_reference = None
            self.off_pending = False
            self.off_in_flight = False
            self.pause_reason = None
        self.set_active(True, at=at)
        return self.snapshot

    def _segment(self, seconds, imported, threshold, active):
        if seconds == 0:
            return
        if imported > threshold:
            self._below_seconds = Fraction(0)
            if active:
                self.energy_ws += imported * seconds
                if self.exhausted:
                    self.pause_reason = ECONOMIC_REASON
            return
        self._below_seconds += seconds
        if self._below_seconds > RESET_SECONDS:
            self.energy_ws = Fraction(0)
            if not self.restart_required and not self.off_in_flight:
                self.off_pending = False
                self.pause_reason = None
                # Only an unconfirmed, unsent/known-rejected episode is canceled.
                # Reasserting a historical confirmed pause or observing physical
                # charging cannot erase its economic restart provenance.
                self.restart_reference = None

    def observe(self, sample, *, threshold_w, source_now=None):
        """Close one distinct supported interval, then install its new endpoint."""
        if not isinstance(sample, MeterSample):
            raise ValueError("expected a validated meter sample")
        threshold_w = _fraction(threshold_w)
        if threshold_w <= 0:
            raise ValueError("technical threshold must be positive")
        if source_now is not None and sample.source_at > _fraction(source_now):
            return self.invalidate("future_source_timestamp")
        fingerprint = sample.source_at, sample.import_w
        prior = self._generations.get(sample.generation)
        if prior is not None:
            if prior != fingerprint:
                return self.invalidate("conflicting_meter_generation")
            return self.snapshot
        if self._source_high_water is not None and (
            sample.source_at <= self._source_high_water
        ):
            return self.invalidate("out_of_order_source_timestamp")
        if self._acceptance_high_water is not None and (
            sample.accepted_at < self._acceptance_high_water
        ):
            return self.invalidate("out_of_order_acceptance")
        if self._events and sample.accepted_at < self._events[-1].at:
            return self.invalidate("out_of_order_acceptance")

        if self._anchor is not None:
            accepted_gap = sample.accepted_at - self._anchor.accepted_at
            source_gap = sample.source_at - self._anchor.source_at
            if max(accepted_gap, source_gap) > MAX_INTERVAL_SECONDS:
                self.invalidate("measurement_gap")
            else:
                cursor = self._anchor.accepted_at
                threshold = self._anchor_threshold
                active = self._anchor_active
                for event in self._events:
                    self._segment(
                        event.at - cursor, self._anchor.import_w, threshold, active
                    )
                    cursor = event.at
                    if event.threshold_w is not None:
                        threshold = event.threshold_w
                    if event.active is not None:
                        active = event.active
                self._segment(
                    sample.accepted_at - cursor,
                    self._anchor.import_w,
                    threshold,
                    active,
                )

        self._generations[sample.generation] = fingerprint
        # The caller's canonical generation includes its source timestamp. The
        # source high-water mark prevents re-crediting any older generation;
        # bounded recent fingerprints additionally catch conflicting duplicates.
        if len(self._generations) > RECENT_GENERATIONS:
            del self._generations[next(iter(self._generations))]
        self._source_high_water = sample.source_at
        self._acceptance_high_water = sample.accepted_at
        self._anchor = sample
        self._threshold = threshold_w
        self._anchor_threshold = threshold_w
        self._anchor_active = self.active
        self._events.clear()
        self.coverage_reason = None
        if sample.import_w > threshold_w:
            self._below_seconds = Fraction(0)
        return self.snapshot

    def clear_scope(self):
        """End an explicit PV attempt/terminated external transaction completely."""
        self.__init__()
        return self.snapshot

    def serialize(self):
        """Only proved data; no process-local timing or partial reset proof."""
        return {
            "version": POLICY_VERSION,
            "energy_ws": _exact_record(self.energy_ws),
            "active": self.active,
            "off_pending": self.off_pending,
            "off_in_flight": self.off_in_flight,
            "paused": self.paused,
            "restart_required": self.restart_required,
            "pause_reason": self.pause_reason,
            "restart_reference": (
                None
                if self.restart_reference is None
                else self.restart_reference.serialize()
            ),
        }

    @classmethod
    def restore(cls, record):
        if not isinstance(record, dict) or type(record.get("version")) is not int:
            raise ValueError("invalid economic record")
        if record["version"] != POLICY_VERSION:
            raise ValueError("unsupported economic policy version")
        if type(record.get("restart_required")) is not bool:
            raise ValueError("invalid economic restart provenance")
        energy = _restore_exact(record.get("energy_ws"))
        reference = record.get("restart_reference")
        reference = None if reference is None else RestartReference.restore(reference)
        return cls(
            energy,
            active=record.get("active"),
            off_pending=record.get("off_pending"),
            off_in_flight=record.get("off_in_flight"),
            paused=record.get("paused"),
            restart_required=record.get("restart_required"),
            pause_reason=record.get("pause_reason"),
            restart_reference=reference,
        )
