"""Tests for the human-readable energy analytics UI and API validation."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from orchestrator.core.energy_analytics import rebuild_energy_analytics


def test_analytics_page_is_served(client):
    response = client.get("/analytics")

    assert response.status_code == 200
    assert "Analytics dashboard" in response.text
    assert "Refresh insights" in response.text


def test_timeline_rejects_excessive_range(client, override_mysql_session):
    response = client.get("/analytics/api/timeline?hours=2161")

    assert response.status_code == 422


def test_rebuild_rejects_excessive_window(client, override_mysql_session):
    response = client.post("/analytics/api/rebuild?days=91")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_rebuild_segments_cumulative_meters_at_their_reset_marker():
    session = AsyncMock()
    insert_result = AsyncMock()
    insert_result.rowcount = 12
    session.execute.side_effect = [AsyncMock(), insert_result, AsyncMock(), AsyncMock()]

    result = await rebuild_energy_analytics(
        session,
        days=7,
        now=datetime(2026, 9, 23, 12, 0, 0),
    )

    insert_sql = str(session.execute.call_args_list[1].args[0])
    assert "last_reset" in insert_sql
    assert result["rows_rebuilt"] == 12
    session.commit.assert_awaited_once()
