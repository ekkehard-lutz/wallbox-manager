"""Validated local preferences in the existing, losslessly preserved profile store."""

import asyncio
import json
import math
import re
import time
from fractions import Fraction

from homeassistant.core import CoreState

# Public roles -> legacy storage field, default, minimum, maximum, step, unit.
NUMBERS = {
    "desired_charging_power": ("target_w", 0, 0, 100000, 100, "W"),
    **{
        f"allowed_current_{n}p": (f"allowed_current_{n}p", None, 0, 100000, 1, "A")
        for n in (1, 2, 3)
    },
    "soll_power": ("power_kw", None, 0, 100, 0.1, "kW"),
    "min_soc": ("min_soc", 20, 0, 100, 1, "%"),
    "soll_soc_speicher": ("soll_soc_speicher", 95, 0, 99, 1, "%"),
    "soc_hysterese": ("soc_hysterese", 2, 0, 50, 1, "%"),
    "optimum_lower_soc": ("optimum_lower_soc", 20, 0, 100, 1, "%"),
    "optimum_upper_soc": ("optimum_upper_soc", 80, 0, 100, 1, "%"),
    "optimum_max_discharge_w": ("optimum_max_discharge_w", 0, 0, 100000, 100, "W"),
    "estimated_daily_house_consumption_kwh": (
        "estimated_daily_house_consumption_kwh",
        0,
        0,
        1000,
        0.1,
        "kWh",
    ),
    "regulation_interval": ("regulation_interval", 5, 1, 300, 1, "s"),
    "pv_start_delay": ("pv_start_delay", 0, 0, 3600, 1, "s"),
    "pv_stop_delay": ("pv_stop_delay", 90, 0, 3600, 1, "s"),
}
SELECTS = {
    "charging_profile": ("profile", ["NETZ", "PV_SURPLUS", "PV_OPTIMUM", "PV_MAXIMUM"]),
    "pv_approximation": ("approximation", ["up", "down"]),
    "power_approximation": ("direction", ["up", "down", "nearest"]),
}
EDITOR_LEASE_SECONDS = 60
TIMES = ("grid_start_delay", "grid_duration")
GRID_FIELDS = ("power_kw", "power_phases", *TIMES)
REGULATION = ("soc_hysterese", "regulation_interval", "pv_start_delay", "pv_stop_delay")


def duration_seconds(value):
    if value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2,}:[0-5][0-9]", value):
        raise ValueError("Duration must be hh:mm or empty")
    hours, minutes = map(int, value.split(":"))
    return hours * 3600 + minutes * 60


def duration_text(value):
    return "" if value is None else f"{value // 3600:02d}:{value % 3600 // 60:02d}"


class Settings:
    def __init__(self, hass, entry, binding, store, profiles):
        self.hass, self.entry, self.store = hass, entry, store
        self.profiles = profiles
        self.key = json.dumps(list(binding))
        saved = profiles.get(self.key, {})
        self.values = {spec[0]: spec[1] for spec in NUMBERS.values()}
        self.values.update({spec[0]: spec[1][0] for spec in SELECTS.values()})
        self.values.update(approximation="down", direction="nearest")
        self.values.update(dict.fromkeys(TIMES))
        self.values["power_phases"] = None
        self.values.update(saved)
        # Restore caps before any connection/control task can use default maxima.
        # Entities are added only after capability discovery, which is too late
        # to make their RestoreEntity callback the sole migration boundary.
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers import restore_state

        from .const import DOMAIN
        from .entity import unique_id

        registry = er.async_get(hass)
        restored = restore_state.async_get(hass).last_states
        for mode in (1, 2, 3):
            role = f"allowed_current_{mode}p"
            if role in saved:
                continue
            entity_id = registry.async_get_entity_id(
                "number", DOMAIN, unique_id(entry.entry_id, "control", binding, role)
            )
            previous = restored.get(entity_id)
            if previous and previous.state.state not in ("unknown", "unavailable"):
                self.values[role] = (
                    previous.state.attributes.get("exact_current_limit_a")
                    or previous.state.state
                )
        # Old central options are authoritative for these fields. Preserve seconds-
        # based smoothing unchanged; future regulator must migrate its units.
        for key in REGULATION:
            if key in entry.options:
                self.values[key] = entry.options[key]
        # Runtime reload never resumes a previously granted release.
        self.values["charge_release"] = False
        self.values["grid_request"] = None
        self.values["grid_timing_edit"] = None
        self.values.setdefault("grid_timing_revision", 0)
        self.timing_lease = None
        self.wall_time = time.time
        self.state = None
        self.listeners = {}
        self.lock = asyncio.Lock()
        self.values.setdefault("grid_draft_revision", 0)
        self.values.setdefault("grid_approved", {**self.grid_draft(), "revision": 0})
        self.grid_equivalent = lambda values: False
        self.grid_point = self.resolve_draft
        self.approval_error = None

    async def initialize_grid(self):
        """Migrate active beta.10 settings once, before entities/transport start."""
        if "grid_approved" not in self.profiles.get(self.key, {}):
            profiles = {
                **self.profiles,
                self.key: {
                    **self.profiles.get(self.key, {}),
                    "grid_draft_revision": self.values["grid_draft_revision"],
                    "grid_approved": self.values["grid_approved"],
                },
            }
            await self.persist(profiles)
            self.profiles = profiles

    def grid_draft(self, values=None):
        values = self.values if values is None else values
        return {key: values.get(key) for key in GRID_FIELDS}

    def approved(self, role):
        key = NUMBERS[role][0] if role in NUMBERS else role
        return self.values["grid_approved"].get(key)

    def approval_attributes(self):
        approved = self.values.get("grid_approved", self.grid_draft())
        pending = self.grid_draft() != {key: approved.get(key) for key in GRID_FIELDS}
        try:
            self.validate("soll_power", self.get("soll_power"))
            for role in TIMES:
                self.validate(role, duration_text(self.get(role)))
            ready = True
        except ValueError, TypeError, OverflowError:
            ready = False
        return {
            "grid_draft_revision": self.values["grid_draft_revision"],
            "grid_timing_revision": self.values["grid_timing_revision"],
            "grid_timing_edit": self.values.get("grid_timing_edit"),
            "grid_set_required": pending,
            "grid_set_ready": ready,
            "grid_approved_power_kw": approved["power_kw"],
            "grid_approved_start_delay": approved.get("grid_start_delay"),
            "grid_approved_duration": approved.get("grid_duration"),
            "grid_approved_revision": approved.get("revision", 0),
            "grid_approval_error": self.approval_error,
        }

    def equivalent_grid_draft(self, values):
        # Capability initialization is not a user edit, even if its new minimum
        # happens to resolve to the recalculated approved operating point.
        if values.get("grid_power_initialized"):
            return False
        if (values["power_kw"], values.get("power_phases")) == (
            self.approved("soll_power"),
            self.approved("power_phases"),
        ) or any(values.get(k) != self.approved(k) for k in TIMES):
            return False
        try:
            self.validate("soll_power", values["power_kw"])
            return self.grid_equivalent(values)
        except ValueError, TypeError, OverflowError:
            return False

    async def approve_equivalent_grid(self):
        """Recheck the latest draft/context after an affected decision settles."""
        async with self.lock:
            if not self.equivalent_grid_draft(self.values):
                return
            updated = dict(self.values)
            self.approve_values(updated)
            try:
                await self._save(updated)
            except (ValueError, TypeError, OSError) as exc:
                self.approval_error = str(exc)
                self.notify_grid()
                return
            self.approval_error = None
            self.notify("grid_equivalent")
            self.notify_grid()

    def approve_values(self, updated):
        previous = self.values["grid_approved"]
        updated["grid_power_initialized"] = False
        updated["grid_approved"] = {
            **self.grid_draft(updated),
            "revision": previous["revision"] + 1,
        }

    async def _approve_release(
        self, *, start=False, notify=True, now=None, current=lambda: True
    ):
        async with self.lock:
            if not start and not self.approval_attributes()["grid_set_required"]:
                return True, True
            try:
                if not current():
                    raise ValueError("Superseded charge action")
                self.validate("soll_power", self.get("soll_power"))
                for role in TIMES:
                    self.validate(role, duration_text(self.get(role)))
                previous = dict(self.values)
                updated = dict(self.values)
                accepted_at = now() if callable(now) else now
                if accepted_at is None:
                    accepted_at = self.wall_time()
                power_changed = (updated["power_kw"], updated.get("power_phases")) != (
                    self.approved("soll_power"),
                    self.approved("power_phases"),
                )
                equivalent = not power_changed or self.grid_equivalent(updated)
                self.approve_values(updated)
                updated["charge_release"] = start or self.get("charge_release")
                if start:
                    updated["grid_request"] = self.schedule_values(updated, accepted_at)
                elif updated["charge_release"] and any(
                    updated.get(key) != previous["grid_approved"].get(key)
                    for key in TIMES
                ):
                    request = previous.get("grid_request")
                    if request is None or updated["grid_start_delay"] != previous[
                        "grid_approved"
                    ].get("grid_start_delay"):
                        updated["grid_request"] = self.schedule_values(
                            updated, accepted_at
                        )
                    else:
                        duration = updated["grid_duration"]
                        updated["grid_request"] = {
                            **request,
                            "revision": updated["grid_approved"]["revision"],
                            "end_at": request["start_at"] + duration
                            if duration is not None
                            else None,
                        }
                updated["grid_timing_edit"] = None
                updated["grid_timing_revision"] += 1
                request = updated.get("grid_request")
                if (
                    request
                    and request.get("end_at") is not None
                    and request["end_at"] <= accepted_at
                ):
                    updated["charge_release"] = False
                    updated["grid_request"] = None
                await self._save(updated)
                if not current():
                    self.values["charge_release"] = False
                    await self._save(
                        {
                            **self.values,
                            "charge_release": False,
                            "grid_request": None,
                            "grid_approved": previous["grid_approved"],
                            "grid_timing_edit": None,
                            "grid_power_initialized": previous.get(
                                "grid_power_initialized", False
                            ),
                        }
                    )
                    raise ValueError("Superseded charge action")
            except (ValueError, TypeError, OSError) as exc:
                self.approval_error = str(exc)
                self.notify_grid()
                raise
            self.approval_error = None
            if notify:
                self.notify(
                    "grid_timing"
                    if not power_changed
                    else "grid_equivalent"
                    if equivalent
                    else "grid_approved"
                )
            self.notify_grid()
            return equivalent, not power_changed

    def notify(self, role):
        for listener in tuple(self.listeners.get(role, ())):
            listener()

    def notify_grid(self):
        for role in ("soll_power", *TIMES, "charge_action"):
            self.notify(role)

    def watch(self, role, listener):
        self.listeners.setdefault(role, set()).add(listener)
        return lambda: self.listeners[role].discard(listener)

    def get(self, role):
        field = (
            NUMBERS[role][0]
            if role in NUMBERS
            else SELECTS[role][0]
            if role in SELECTS
            else role
        )
        return self.values.get(field)

    def validate(self, role, value):
        if role in NUMBERS:
            _, _, minimum, maximum, _, _ = NUMBERS[role]
            if isinstance(value, bool):
                raise ValueError("Number required")
            value = float(Fraction(value))
            if not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError("Number outside supported range")
            if role == "soll_power":
                from .grid_point import envelope, rounded_kw

                value = rounded_kw(value)

                try:
                    bounds = envelope(self.state, self).power_bounds()
                except ValueError, TypeError, AttributeError:
                    # Unknown capabilities allow draft storage only. The editor
                    # and action remain unavailable until the range is known.
                    bounds = None
                    known = False
                else:
                    known = True
                if known and (bounds is None or not bounds[0] <= value <= bounds[1]):
                    raise ValueError("Power outside positive technical range")
            if role == "soll_soc_speicher" and not value.is_integer():
                raise ValueError("Target SoC must be a whole percent")
            if role.startswith("allowed_current_"):
                from .grid_point import capabilities

                if not value.is_integer():
                    raise ValueError("Allowed current must be whole amperes")
                if self.state is None:
                    raise ValueError("Station capabilities unavailable")
                caps = capabilities(self.state)
                mode = int(role[-2])
                if mode not in caps["modes"]:
                    raise ValueError("Unsupported phase mode")
                maximum = (
                    caps["maximum"]
                    if mode == 3
                    else min(caps["maximum"], caps["asymmetry"])
                )
                if value != 0 and not caps["minimum"] <= value <= maximum:
                    raise ValueError("Allowed current outside station range")
                value = int(value)
            settings = {**self.values, NUMBERS[role][0]: value}
            if role in (
                "soll_soc_speicher",
                "soc_hysterese",
                "optimum_lower_soc",
                "optimum_upper_soc",
            ):
                reserve = self.hass.states.get(
                    self.entry.options.get("min_soc_speicher", "")
                )
                raw = (
                    float(reserve.state)
                    if reserve and reserve.state not in ("unknown", "unavailable")
                    else 0
                )
                lower, upper = (
                    raw + settings["soc_hysterese"],
                    100 - settings["soc_hysterese"],
                )
                if (
                    not lower
                    <= settings["optimum_lower_soc"]
                    <= settings["optimum_upper_soc"]
                    <= upper
                    or not lower <= settings["soll_soc_speicher"] <= upper
                ):
                    raise ValueError("Inconsistent reserve, hysteresis or SoC bounds")
        elif role in SELECTS:
            if value not in SELECTS[role][1]:
                raise ValueError("Unsupported selection")
        elif role in TIMES:
            value = duration_seconds(value)
        else:
            raise ValueError("Unknown setting")
        return value

    async def set(self, role, value):
        if role == "charging_profile" and self.validate_setting(
            role, value
        ) != self.get(role):
            self.values["charge_release"] = False
            self.notify("charge_release")
        async with self.lock:
            await self._set(role, value)

    async def _set(self, role, value, *, selected=None, editor=None):
        value = self.validate_setting(role, value)
        field = (
            NUMBERS[role][0]
            if role in NUMBERS
            else SELECTS[role][0]
            if role in SELECTS
            else role
        )
        if self.values.get(field) == value and (
            role != "soll_power"
            or (
                self.values.get("power_phases") == selected
                and not self.values.get("grid_power_initialized")
            )
        ):
            return
        updated = {**self.values, field: value}
        if role == "soll_power":
            updated["power_phases"] = selected
            updated["grid_power_initialized"] = False
        if role in ("soll_power", *TIMES):
            updated["grid_draft_revision"] = self.values["grid_draft_revision"] + 1
        if role in TIMES:
            updated["grid_timing_revision"] += 1
            self.edit_window_values(updated, editor, active=editor is not None)
        if role == "charging_profile":
            # set() already fenced wire dispatch before acquiring this lock.
            updated["charge_release"] = False
            updated["grid_request"] = None
            updated["grid_timing_edit"] = None
            updated["grid_timing_revision"] += 1
        equivalent = role in ("soll_power", *TIMES) and self.equivalent_grid_draft(
            updated
        )
        if equivalent:
            self.approve_values(updated)
        await self._save(updated)
        if role in REGULATION:
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, role: value}
            )
        if role in ("soll_power", *TIMES):
            self.approval_error = None
            if equivalent:
                self.notify("grid_equivalent")
            self.notify_grid()
            if role in TIMES:
                self.notify("grid_timing_edit")
        else:
            self.notify(role)

    def edit_window_values(self, updated, owner, *, active):
        request = updated.get("grid_request")
        pending = any(
            updated.get(key) != updated["grid_approved"].get(key) for key in TIMES
        )
        updated["grid_timing_edit"] = (
            {
                "owner": owner,
                "active": active,
                "revision": updated["grid_timing_revision"],
                "schedule_revision": request["revision"],
                "expires_at": None if active else self.wall_time() + 15,
            }
            if updated.get("charge_release") and request and (active or pending)
            else None
        )

    def timing_edit_deadline(self):
        edit = self.values.get("grid_timing_edit")
        if not edit:
            return None
        if not edit["active"]:
            return edit["expires_at"]
        if self.timing_lease and self.timing_lease[:2] == (
            edit["owner"],
            edit["revision"],
        ):
            return self.timing_lease[2]
        return None

    def recover_timing_editor(self):
        """Called under Settings.lock; abandonment changes ownership, not policy."""
        edit = self.values.get("grid_timing_edit")
        deadline = self.timing_edit_deadline()
        if (
            not edit
            or not edit["active"]
            or deadline is None
            or deadline > self.wall_time()
        ):
            return
        revision = self.values["grid_timing_revision"] + 1
        pending = any(self.get(key) != self.approved(key) for key in TIMES)
        self.values = {
            **self.values,
            "grid_timing_revision": revision,
            "grid_timing_edit": {
                **edit,
                "owner": None,
                "active": False,
                "revision": revision,
                "expires_at": deadline + 15,
            }
            if pending
            else None,
        }
        self.timing_lease = None
        self.notify_grid()
        self.notify("grid_timing_edit")

    async def edit_timing(self, role, action, owner, revision, value=None):
        """One revision-fenced editing area for both Text entities."""
        async with self.lock:
            self.recover_timing_editor()
            if role not in TIMES or action not in ("begin", "update", "end", "renew"):
                raise ValueError("Unsupported timing edit")
            if (
                type(revision) is not int
                or revision != self.values["grid_timing_revision"]
            ):
                raise ValueError("Superseded timing revision")
            if not isinstance(owner, str) or not 1 <= len(owner) <= 128:
                raise ValueError("Timing editor identity required")
            current = self.values.get("grid_timing_edit")
            if (
                action != "begin"
                and current
                and (not current["active"] or current["owner"] != owner)
            ):
                raise ValueError("Superseded timing editor")
            if action == "renew":
                if (
                    not current
                    or not current["active"]
                    or self.timing_edit_deadline() is None
                ):
                    raise ValueError("Expired timing editor")
                self.timing_lease = (
                    owner,
                    revision,
                    self.wall_time() + EDITOR_LEASE_SECONDS,
                )
                self.notify("grid_timing_lease")
                return
            if action == "update":
                await self._set(role, value, editor=owner)
                return
            updated = {**self.values, "grid_timing_revision": revision + 1}
            self.edit_window_values(updated, owner, active=action == "begin")
            await self._save(updated)
            self.notify_grid()
            self.notify("grid_timing_edit")

    async def expire_timing_edit(self, revision, schedule_revision):
        """Rollback only timing drafts, never power, approval or an ended run."""
        async with self.lock:
            edit = self.values.get("grid_timing_edit")
            request = self.values.get("grid_request")
            now = self.wall_time()
            if (
                not self.get("charge_release")
                or not edit
                or not request
                or edit["revision"] != revision
                or edit["schedule_revision"] != schedule_revision
                or request["revision"] != schedule_revision
                or self.timing_edit_deadline() is None
                or self.timing_edit_deadline() > now
                or (request.get("end_at") is not None and request["end_at"] <= now)
            ):
                return
            if edit["active"]:
                self.recover_timing_editor()
                return  # The existing scheduler now owns the new grace revision.
            previous = self.values
            updated = {
                **self.values,
                **{key: self.approved(key) for key in TIMES},
                "grid_draft_revision": self.values["grid_draft_revision"] + 1,
                "grid_timing_revision": self.values["grid_timing_revision"] + 1,
                "grid_timing_edit": None,
            }
            await self._save(updated)
            # Stop/expiry can retire release immediately while storage is
            # awaiting I/O under this lock. Such retirement wins over rollback.
            if not previous.get("charge_release") or (
                request.get("end_at") is not None
                and request["end_at"] <= self.wall_time()
            ):
                self.values = {
                    **previous,
                    "charge_release": False,
                    "grid_request": None,
                    "grid_timing_edit": None,
                }
                self.notify("charge_release")
                await self._save(dict(self.values))
            self.notify_grid()
            self.notify("grid_timing_edit")

    def resolve_draft(self, values):
        from .grid_point import envelope, number, rounded_kw

        return envelope(self.state, self).resolve(
            number(rounded_kw(values["power_kw"])) * 1000,
            self.get("power_approximation"),
            selected=values.get("power_phases"),
        )

    def validate_setting(self, role, value):
        identity = "setting:" + (
            role if role in (*NUMBERS, *SELECTS, *TIMES) else "request"
        )
        try:
            result = self.validate(role, value)
        except (ValueError, TypeError, OverflowError) as exc:
            if self.state:
                self.state.messages.set(
                    identity,
                    source="settings",
                    severity="error",
                    code="settings_value",
                    detail=str(exc),
                )
            raise
        if self.state:
            self.state.messages.clear(identity)
        return result

    def power_attributes(self):
        from .grid_point import envelope, number, rounded_kw

        result = {
            "draft_phase_hint": self.get("power_phases"),
            "selected_phase_count": None,
            "power_step_up": None,
            "power_step_down": None,
        }
        try:
            ranges = envelope(self.state, self)
            point = self.grid_point(self.values)
            result["selected_phase_count"] = point.phases
            watts = (
                point.display_watts
                if point.amps > 0
                else number(rounded_kw(self.get("soll_power"))) * 1000
            )
            for name, direction in (("power_step_up", 1), ("power_step_down", -1)):
                step = ranges.navigate(watts, point.phases, direction)
                result[name] = step.selection() if step else None
        except ValueError, TypeError, OverflowError:
            pass
        return result

    async def step_power(self, direction, revision):
        from .grid_point import envelope, number, rounded_kw

        async with self.lock:
            if (
                type(revision) is not int
                or revision != self.values["grid_draft_revision"]
            ):
                raise ValueError("Superseded NETZ draft revision")
            point = self.grid_point(self.values)
            step = envelope(self.state, self).navigate(
                point.display_watts
                if point.amps > 0
                else number(rounded_kw(self.get("soll_power"))) * 1000,
                point.phases,
                direction,
            )
            if step is not None:
                await self._set(
                    "soll_power", step.selection()["power_kw"], selected=step.phases
                )

    async def initialize_power(self):
        """Initialize invalid drafts once capabilities exist, without dispatch."""
        from .grid_point import envelope, rounded_kw

        async with self.lock:
            try:
                try:
                    ranges = envelope(self.state, self)
                except ValueError, TypeError, OverflowError:
                    return
                bounds = ranges.power_bounds()
                if bounds is None:
                    return
                try:
                    value = rounded_kw(self.get("soll_power"))
                    if bounds[0] <= value <= bounds[1]:
                        return
                except ValueError, TypeError, OverflowError:
                    pass
                point = min(
                    (p for mode in ranges.ranges for p in ranges.points(mode)),
                    key=lambda p: (p.display_watts, p.phases),
                )
                updated = {
                    **self.values,
                    **point.selection(),
                    "grid_draft_revision": self.values["grid_draft_revision"] + 1,
                    "grid_power_initialized": True,
                }
                await self._save(updated)
                self.approval_error = None
            except (ValueError, TypeError, OverflowError, OSError) as exc:
                self.approval_error = str(exc)
            self.notify_grid()

    async def persist(self, profiles):
        # HA Store logs and swallows WriteError. Its shutdown-deferred data and
        # cached loads cannot establish that a new approval reached disk.
        if self.hass.state is CoreState.stopping:
            raise OSError("Cannot approve settings while Home Assistant is stopping")
        await self.store.async_save(profiles)

        def verify():
            with open(self.store.path, encoding="utf-8") as handle:
                stored = json.load(handle)
            if stored.get("key") != self.store.key or stored.get("data") != profiles:
                raise OSError("Profile settings were not durably stored")

        await self.hass.async_add_executor_job(verify)

    async def _save(self, updated):
        # Keep drafts, approval and foreign profile data in one atomic document.
        profiles = {
            **self.profiles,
            self.key: {
                **self.profiles.get(self.key, {}),
                **{k: v for k, v in updated.items() if k not in REGULATION},
            },
        }
        edit = updated.get("grid_timing_edit")
        lease = self.timing_lease
        if edit and edit["active"]:
            identity = (edit["owner"], edit["revision"])
            if not lease or lease[:2] != identity:
                lease = (*identity, self.wall_time() + EDITOR_LEASE_SECONDS)
        else:
            lease = None
        try:
            await self.persist(profiles)
        except OSError as exc:
            if self.state:
                self.state.messages.set(
                    "settings:storage",
                    source="settings",
                    severity="error",
                    code="settings_save",
                    detail=str(exc),
                )
            raise
        self.profiles, self.values = profiles, updated
        # Slow storage cannot grant a fresh lease at its eventual completion.
        self.timing_lease = lease
        if self.state:
            self.state.messages.clear("settings:storage")

    def schedule_values(self, updated, now):
        """One absolute business schedule, independent of vehicle/transaction."""
        start = now + (updated["grid_start_delay"] or 0)
        duration = updated["grid_duration"]
        return {
            "epoch_at": now,
            "revision": updated["grid_approved"]["revision"],
            "start_at": start,
            "end_at": start + duration if duration is not None else None,
        }

    async def save_runtime(self, fields):
        """Persist scheduling state without modifying approved/draft configuration."""
        async with self.lock:
            await self._save({**self.values, **fields})
            for role in fields:
                self.notify(role)
