"""Periodic, idempotent MySQL-to-ArcadeDB graph synchronization."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from orchestrator.config import Settings
from orchestrator.core.database import mysql_session_context
from orchestrator.graph.builder import incremental_sync
from orchestrator.graph.relationships import sync_graph_relationships

logger = logging.getLogger(__name__)


class GraphSyncMonitor:
    """Keep relational graph records current without invoking HA bootstrap."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.interval_seconds = max(60, settings.GRAPH_SYNC_INTERVAL_SECONDS)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.last_success_at: str | None = None
        self.last_error: str | None = None
        self.last_result: dict[str, Any] | None = None
        self._watermark = "1970-01-01 00:00:00"

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="econest-graph-sync")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def status(self) -> dict[str, Any]:
        return {"enabled": self.settings.GRAPH_SYNC_ENABLED, "running": self._task is not None and not self._task.done(), "interval_seconds": self.interval_seconds, "last_success_at": self.last_success_at, "last_error": self.last_error, "last_result": self.last_result}

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                async with mysql_session_context() as session:
                    result = await incremental_sync(session, self._watermark)
                    relationships = await sync_graph_relationships(session)
                    latest = await session.execute(text("SELECT MAX(timestamp) AS value FROM sensor_readings"))
                    value = latest.mappings().one()["value"]
                    if value is not None:
                        self._watermark = value.strftime("%Y-%m-%d %H:%M:%S")
                    result["relationships"] = relationships.model_dump()
                    self.last_result = result
                    self.last_success_at = datetime.now(UTC).isoformat()
                    self.last_error = None
            except Exception as exc:
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                logger.exception("Scheduled graph sync failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass
