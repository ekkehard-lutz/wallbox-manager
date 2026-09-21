"""Small validators and exact SI scalar normalization (no ambient math context)."""

from datetime import datetime
from decimal import Decimal
from fractions import Fraction


def scalar(
    value: int | float | Decimal | Fraction, *, positive: bool = False
) -> Fraction:
    """Normalize finite SI values exactly; decimal spelling defines float inputs."""
    if isinstance(value, bool) or not isinstance(
        value, (int, float, Decimal, Fraction)
    ):
        raise ValueError("expected a numeric SI value")
    try:
        result = Fraction(str(value))
    except (ValueError, OverflowError) as exc:
        raise ValueError("value must be finite") from exc
    if result < 0 or (positive and result == 0):
        raise ValueError("value outside permitted range")
    return result


def nonempty(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("expected a nonempty, trimmed identifier")


def generation(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("expected a nonnegative integer")


def timestamp(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("expected a timezone-aware timestamp")
