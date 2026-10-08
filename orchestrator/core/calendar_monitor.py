"""Periodic, advisory-only synchronization of connected household calendars."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

CalendarReviewRunner = Callable[[], Awaitable[dict[str, int]]]
logger = logging.getLogger(__name__)


class CalendarContextMonitor:
    """Synchronize calendar context and queue at most one review per event."""

    def __init__(
        self,
        run_reviews: CalendarReviewRunner,
        interval_seconds: int,
        run_on_startup: bool = True,
    ) -> None:
        self.run_reviews = run_reviews
        self.interval_seconds = max(30, interval_seconds)
        self.run_on_startup = run_on_startup
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        """Start the monitor if it is not already running."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run(), name="econest-calendar-context-monitor"
        )

    async def stop(self) -> None:
        """Stop the monitor without affecting connected calendars or devices."""
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        if self.run_on_startup:
            await self._run_once()
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.interval_seconds
                )
            except TimeoutError:
                await self._run_once()

    async def _run_once(self) -> None:
        try:
            await self.run_reviews()
        except Exception:
            logger.exception("Calendar context monitor cycle failed")
