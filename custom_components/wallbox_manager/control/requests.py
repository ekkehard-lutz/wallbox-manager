"""Charging intent, independent of energy-source policy and wire protocols."""

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from ..core.values import scalar


class Direction(StrEnum):
    """Power rounding direction, never energy-flow direction."""

    DOWN = "down"
    NEAREST = "nearest"
    UP = "up"


@dataclass(frozen=True)
class PowerRequest:
    target_w: Fraction
    direction: Direction
    allowed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_w", scalar(self.target_w))
        if not isinstance(self.direction, Direction):
            raise ValueError("invalid rounding direction")
        if type(self.allowed) is not bool:
            raise ValueError("allowed must be boolean")
