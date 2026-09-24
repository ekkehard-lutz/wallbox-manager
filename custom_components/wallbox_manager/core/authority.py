"""Observed control ownership, separate from desired permission and capabilities."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .models import StationId
from .values import nonempty, timestamp


class ControlAuthority(StrEnum):
    UNKNOWN = "unknown"
    LOCAL = "local"
    REMOTE = "remote"


@dataclass(frozen=True)
class AuthorityObservation:
    scope: StationId
    authority: ControlAuthority
    observed_at: datetime
    source: str

    def __post_init__(self):
        if not isinstance(self.scope, StationId) or not isinstance(
            self.authority, ControlAuthority
        ):
            raise ValueError("invalid authority observation")
        timestamp(self.observed_at)
        nonempty(self.source)
