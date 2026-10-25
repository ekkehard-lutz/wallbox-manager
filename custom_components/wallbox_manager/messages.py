"""Bounded current warning conditions, separate from complete diagnostic logs."""

import time

TEXT = {
    "connection_lost": "OCPP communication lost.",
    "connection_failed": "OCPP initialization failed.",
    "control_failed": "Wallbox control command failed.",
    "grid_failed": "Charging command failed.",
    "phase_lockout": "Phase switching is temporarily blocked.",
    "reenable_lockout": "Charging re-enable is temporarily blocked.",
    "capabilities": "NETZ capabilities are unavailable or invalid.",
    "power_limited": "Requested power is limited by the current phase mode.",
    "settings_save": "Settings could not be saved.",
    "settings_value": "The requested setting was rejected.",
    "battery_reserve": "Battery reserve operation failed.",
    "wallbox_fault": "The wallbox reports a fault.",
}


class ActiveMessages:
    def __init__(self, state):
        self.state = state
        self.conditions = {}
        self.sequence = 0
        self.wall_time = time.time

    @property
    def key(self):
        return "message", self.state.binding, "active"

    @property
    def latest(self):
        current = max(
            self.conditions.values(), key=lambda m: m["sequence"], default=None
        )
        return (
            {key: value for key, value in current.items() if key != "signature"}
            if current
            else None
        )

    def set(
        self,
        identity,
        *,
        source,
        severity,
        code,
        detail=None,
        generation=None,
        token=None,
    ):
        if generation is not None and not self.state.current(generation):
            return
        before = self.latest
        signature = (source, severity, code, detail, generation, token)
        old = self.conditions.get(identity)
        if old and old["signature"] == signature:
            return
        self.sequence += 1
        self.conditions[identity] = {
            "id": identity,
            "source": source,
            "severity": severity,
            "code": code,
            "text": TEXT[code],
            "detail": detail,
            "sequence": self.sequence,
            "raised_at": self.wall_time(),
            "runtime": self.state.runtime_id,
            "generation": generation,
            "signature": signature,
        }
        if self.latest != before:
            self.state.publish(self.key)

    def clear(self, identity, *, generation=None):
        if generation is not None and not self.state.current(generation):
            return
        before = self.latest
        self.conditions.pop(identity, None)
        if self.latest != before:
            self.state.publish(self.key)

    def reset_generation(self):
        # Runtime-local HA/storage warnings survive an unrelated transport loss.
        self.conditions = {
            key: message
            for key, message in self.conditions.items()
            if message["generation"] is None
        }
