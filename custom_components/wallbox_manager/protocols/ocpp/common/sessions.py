"""Captured session task ownership and cancellation-safe teardown.

Adapted from lbbrhzn/ocpp chargepoint.py at 848407c11ff659ce59779a99ce69984bbb0e3ce1.
Copyright (c) 2021 lbbrhzn. MIT; see ../../../THIRD_PARTY_NOTICES.md.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any


class Session:
    """One immutable socket owner; replacement never changes its transport."""

    def __init__(self, connection, timeout: float = 10) -> None:
        self.connection = connection
        self.timeout = timeout
        self.tasks: set[asyncio.Task] = set()
        self.retirement_tasks: set[asyncio.Task] = set()
        self.cleanup: asyncio.Task | None = None
        self.adapter = None

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        if self.cleanup is not None:
            coro.close()
            raise RuntimeError("session is retiring")
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self._observed)
        return task

    def _observed(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        self.retirement_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _close(self) -> None:
        tasks = tuple(self.tasks)

        async def close() -> None:
            try:
                await self.connection.close()
            finally:
                for task in tasks:
                    task.cancel()

        close_task = asyncio.create_task(close())
        retirement = (*tasks, close_task)
        for task in retirement:
            self.retirement_tasks.add(task)
            task.add_done_callback(self._observed)
        _, pending = await asyncio.wait(retirement, timeout=self.timeout)
        if pending:
            for task in pending:
                task.cancel()
            raise TimeoutError("session retirement timed out")
        close_task.result()

    async def stop(self) -> None:
        """Finish shared cleanup even if an external waiter is cancelled."""
        if (
            self.cleanup is not None
            and self.cleanup.done()
            and not any(not task.done() for task in self.retirement_tasks)
            and (self.cleanup.cancelled() or self.cleanup.exception() is not None)
        ):
            # Keep earlier waiters' outcome intact, but permit a later clean retry.
            self.cleanup = None
        if self.cleanup is None:
            self.cleanup = asyncio.create_task(self._close())
            self.cleanup.add_done_callback(
                lambda t: None if t.cancelled() else t.exception()
            )
        cleanup = self.cleanup
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.wait({cleanup})
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError
