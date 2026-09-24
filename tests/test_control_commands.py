"""Pure command boundary tests, with no charging protocol implementation."""

import asyncio
import dataclasses
import subprocess
import sys

import pytest

from custom_components.wallbox_manager.control.commands import (
    CommandReason,
    CommandResult,
    CommandStatus,
    ControlArea,
    apply_operating_point,
    command_is_current,
    stale_command_result,
)
from custom_components.wallbox_manager.solver.operating_point import OperatingPoint


class FakeAdapter:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def apply_operating_point(self, point, *, is_current):
        self.calls.append((point, is_current))
        return self.result


@pytest.mark.parametrize("status", list(CommandStatus))
@pytest.mark.parametrize("charging", [True, False])
async def test_forward_point_and_outcome_unchanged(one, status, charging):
    point = (
        OperatingPoint(True, one, 16, 3680, (230,))
        if charging
        else OperatingPoint.off()
    )
    result = CommandResult(status)
    adapter = FakeAdapter(result)

    def current():
        return True

    assert await apply_operating_point(adapter, point, is_current=current) is result
    assert adapter.calls[0][0] is point
    assert adapter.calls[0][1] is current
    if not charging:
        assert point.mode is None and point.current_a is None


@pytest.mark.parametrize("result", [None, True, "applied", {"status": "applied"}])
async def test_invalid_adapter_result(result):
    with pytest.raises(ValueError, match="invalid command result"):
        await apply_operating_point(
            FakeAdapter(result), OperatingPoint.off(), is_current=lambda: True
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "applied"},
        {"area": "current"},
        {"reason": "busy"},
        {"detail": []},
        {"detail": " "},
    ],
)
def test_invalid_result_fields(kwargs):
    with pytest.raises(ValueError):
        CommandResult(**({"status": CommandStatus.APPLIED} | kwargs))


def test_structured_immutable_result():
    result = CommandResult(
        CommandStatus.TEMPORARILY_REJECTED,
        ControlArea.PHASE_MODE,
        CommandReason.PHASE_SWITCH_LOCKOUT,
        "Phase switching is currently locked",
    )
    assert result.status.value == "temporarily_rejected"
    assert result.area.value == "phase_mode"
    assert result.reason.value == "phase_switch_lockout"
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = CommandStatus.FAILED


async def test_stale_before_adapter_call():
    adapter = FakeAdapter(CommandResult(CommandStatus.APPLIED))
    result = await apply_operating_point(
        adapter, OperatingPoint.off(), is_current=lambda: False
    )
    assert result == stale_command_result()
    assert adapter.calls == []


async def test_adapter_rechecks_fence_after_wait():
    waiting, resume = asyncio.Event(), asyncio.Event()
    generation = 1
    dispatched = []

    class QueuedAdapter:
        async def apply_operating_point(self, point, *, is_current):
            waiting.set()
            await resume.wait()
            if not command_is_current(is_current):
                return stale_command_result()
            dispatched.append(point)
            return CommandResult(CommandStatus.APPLIED)

    task = asyncio.create_task(
        apply_operating_point(
            QueuedAdapter(), OperatingPoint.off(), is_current=lambda: generation == 1
        )
    )
    await waiting.wait()
    generation = 2
    resume.set()
    assert await task == stale_command_result()
    assert dispatched == []


@pytest.mark.parametrize("value", [None, 1, "true"])
async def test_invalid_validity(value):
    adapter = FakeAdapter(CommandResult(CommandStatus.APPLIED))
    with pytest.raises(ValueError, match="boolean"):
        await apply_operating_point(
            adapter, OperatingPoint.off(), is_current=lambda: value
        )
    assert adapter.calls == []


async def test_invalid_point():
    adapter = FakeAdapter(CommandResult(CommandStatus.APPLIED))
    with pytest.raises(ValueError, match="resolved operating point"):
        await apply_operating_point(adapter, None, is_current=lambda: True)
    assert adapter.calls == []


async def test_internal_exception_propagates():
    class BrokenAdapter:
        async def apply_operating_point(self, point, *, is_current):
            raise RuntimeError("internal defect")

    with pytest.raises(RuntimeError, match="internal defect"):
        await apply_operating_point(
            BrokenAdapter(), OperatingPoint.off(), is_current=lambda: True
        )


def test_import_without_ha_or_ocpp():
    subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys; "
            "import custom_components.wallbox_manager.control.commands; "
            "assert not any(n.split('.')[0] in ('homeassistant', 'ocpp') "
            "for n in sys.modules)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
