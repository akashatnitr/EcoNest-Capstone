"""Tests for safe graph synchronization startup behavior."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.config import Settings
from orchestrator.core.graph_sync import GraphSyncMonitor


@pytest.mark.asyncio
async def test_initial_watermark_uses_latest_reading_by_default() -> None:
    """Avoid replaying the full sensor-reading history after a restart."""
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.one.return_value = {
        "value": datetime(2026, 5, 4, 16, 44, 25)
    }
    session.execute.return_value = result

    monitor = GraphSyncMonitor(Settings(GRAPH_SYNC_INITIAL_LOOKBACK_SECONDS=0))

    assert await monitor._initial_watermark(session) == "2026-05-04 16:44:25"
