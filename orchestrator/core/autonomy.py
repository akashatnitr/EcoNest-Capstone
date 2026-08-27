"""Background autonomous monitoring loop for long-term test data."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from orchestrator.core.audit import write_audit_event, write_audit_event_async

FeedbackCollector = Callable[[], Awaitable[dict[str, Any]]]
ActionRecommender = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]
ActionExecutor = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class AutonomousMonitor:
    """Periodically collect passive household feedback without a browser."""

    def __init__(
        self,
        collect_feedback: FeedbackCollector,
        interval_seconds: int,
        run_on_startup: bool = True,
        action_recommender: ActionRecommender | None = None,
        action_executor: ActionExecutor | None = None,
        action_confidence_threshold: float = 0.85,
        actions_enabled: bool = False,
    ) -> None:
        self.collect_feedback = collect_feedback
        self.action_recommender = action_recommender
        self.action_executor = action_executor
        self.action_confidence_threshold = action_confidence_threshold
        self.actions_enabled = actions_enabled
        self.interval_seconds = max(15, interval_seconds)
        self.run_on_startup = run_on_startup
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self.started_at: str | None = None
        self.last_run_at: str | None = None
        self.last_success_at: str | None = None
        self.last_failure_at: str | None = None
        self.last_error: str | None = None
        self.run_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.action_recommendation_count = 0
        self.action_execution_count = 0
        self.action_skip_count = 0

    def start(self) -> None:
        """Start the monitor loop if it is not already running."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self.started_at = _utc_now()
        self._task = asyncio.create_task(self._run(), name="econest-autonomous-monitor")
        write_audit_event(
            "autonomy.monitor.started",
            {
                "interval_seconds": self.interval_seconds,
                "run_on_startup": self.run_on_startup,
            },
        )

    async def stop(self) -> None:
        """Stop the monitor loop and wait for shutdown."""
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        write_audit_event("autonomy.monitor.stopped", {})

    def status(self) -> dict[str, Any]:
        """Return read-only monitor runtime status."""
        return {
            "enabled": True,
            "running": self._task is not None and not self._task.done(),
            "interval_seconds": self.interval_seconds,
            "run_on_startup": self.run_on_startup,
            "started_at": self.started_at,
            "last_run_at": self.last_run_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_error": self.last_error,
            "run_count": self.run_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "action_confidence_threshold": self.action_confidence_threshold,
            "actions_enabled": self.actions_enabled,
            "action_recommendation_count": self.action_recommendation_count,
            "action_execution_count": self.action_execution_count,
            "action_skip_count": self.action_skip_count,
        }

    async def _run(self) -> None:
        if self.run_on_startup:
            await self.run_once()

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.interval_seconds,
                )
            except TimeoutError:
                await self.run_once()

    async def run_once(self) -> dict[str, Any] | None:
        """Collect one feedback sample and record success or failure."""
        self.run_count += 1
        self.last_run_at = _utc_now()
        try:
            result = await self.collect_feedback()
        except Exception as exc:
            self.failure_count += 1
            self.last_failure_at = _utc_now()
            self.last_error = str(exc)
            await write_audit_event_async(
                "autonomy.monitor.failed",
                {
                    "success": False,
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                },
            )
            return None

        self.success_count += 1
        self.last_success_at = _utc_now()
        self.last_error = None
        await write_audit_event_async(
            "autonomy.monitor.completed",
            {
                "success": True,
                "source": result.get("source"),
                "suggestion_count": len(result.get("suggestions", [])),
                "occupancy_status": result.get("snapshot", {}).get("occupancy_status"),
            },
        )
        await self._maybe_execute_action(result)
        return result

    async def _maybe_execute_action(self, feedback: dict[str, Any]) -> None:
        if self.action_recommender is None:
            return

        recommendation = await self.action_recommender(feedback)
        if not recommendation:
            return

        self.action_recommendation_count += 1
        await write_audit_event_async(
            "autonomy.action.recommended",
            {
                "recommendation": recommendation,
                "confidence": recommendation.get("confidence"),
                "entity_id": recommendation.get("entity_id"),
                "action": recommendation.get("action"),
            },
        )

        confidence = _float_or_zero(recommendation.get("confidence"))
        if confidence < self.action_confidence_threshold:
            self.action_skip_count += 1
            await write_audit_event_async(
                "autonomy.action.skipped",
                {
                    "reason": "confidence_below_threshold",
                    "confidence": confidence,
                    "threshold": self.action_confidence_threshold,
                    "recommendation": recommendation,
                },
            )
            return

        if not self.actions_enabled:
            self.action_skip_count += 1
            await write_audit_event_async(
                "autonomy.action.skipped",
                {
                    "reason": "actions_disabled",
                    "recommendation": recommendation,
                },
            )
            return

        if self.action_executor is None:
            self.action_skip_count += 1
            await write_audit_event_async(
                "autonomy.action.skipped",
                {
                    "reason": "no_action_executor",
                    "recommendation": recommendation,
                },
            )
            return

        result = await self.action_executor(recommendation)
        self.action_execution_count += 1
        await write_audit_event_async(
            "autonomy.action.executed",
            {
                "success": bool(result.get("success")),
                "recommendation": recommendation,
                "result": result,
            },
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
