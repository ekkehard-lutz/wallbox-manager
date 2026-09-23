"""Confirmed hardware permission, never persistent desired control state."""

from dataclasses import dataclass
from datetime import datetime

from .models import ConnectorId
from .values import timestamp


@dataclass(frozen=True)
class EnabledObservation:
    scope: ConnectorId
    enabled: bool | None
    observed_at: datetime
    valid_until: datetime
    revision: int = 0

    def __post_init__(self):
        if not isinstance(self.scope, ConnectorId):
            raise ValueError("invalid enabled scope")
        if self.enabled is not None and type(self.enabled) is not bool:
            raise ValueError("invalid enabled state")
        timestamp(self.observed_at)
        timestamp(self.valid_until)
        if self.valid_until <= self.observed_at:
            raise ValueError("invalid enabled deadline")
