"""Tests for the graph layer."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.database import get_mysql_session
from orchestrator.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_user():
    return UserProfile(
        id=1,
        email="admin@example.com",
        role="superadmin",
        household_id=None,
        is_active=True,
    )


@pytest.fixture
def guest_user():
    return UserProfile(
        id=2, email="guest@example.com", role="guest", household_id=None, is_active=True
    )


@pytest.fixture(autouse=True)
def override_deps(admin_user):
    """Override auth and db dependencies for all graph tests."""
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_mysql_session] = AsyncMock
    yield
    app.dependency_overrides.clear()


def _mock_arcadedb_result(result_data: dict):
    """Helper to patch arcadedb_query in api.graph module."""
    return patch(
        "orchestrator.api.graph.arcadedb_query",
        new=AsyncMock(return_value=result_data),
    )


def _mock_queries_arcadedb(result_data: dict):
    """Helper to patch arcadedb_query in graph.queries module."""
    return patch(
        "orchestrator.graph.queries.arcadedb_query",
        new=AsyncMock(return_value=result_data),
    )


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_health(client):
    with patch(
        "orchestrator.api.graph.healthcheck_arcadedb",
        new=AsyncMock(return_value=True),
    ):
        resp = client.get("/graph/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ------------------------------------------------------------------
# Rooms
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_rooms(client):
    with _mock_arcadedb_result(
        {
            "result": [
                {"room": {"name": "Kitchen"}, "count": 3},
                {"room": {"name": "Garage"}, "count": 1},
            ]
        }
    ):
        resp = client.get("/graph/rooms")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["name"] == "Kitchen"
    assert data[0]["device_count"] == 3


# ------------------------------------------------------------------
# Room devices
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_room_devices(client):
    with _mock_queries_arcadedb(
        {"result": [{"name": ["Washer"], "device_type": ["SmartPlug"]}]}
    ):
        resp = client.get("/graph/rooms/%231:0/devices")
    assert resp.status_code == 200
    assert resp.json()["devices"][0]["name"] == ["Washer"]


# ------------------------------------------------------------------
# Device neighbors
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_device_neighbors(client):
    with _mock_arcadedb_result({"result": [{"name": ["Kitchen"]}]}):
        resp = client.get("/graph/devices/%232:0/neighbors")
    assert resp.status_code == 200
    assert len(resp.json()["neighbors"]) == 1


# ------------------------------------------------------------------
# Raw query (admin only)
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_raw_query_admin(client):
    with _mock_arcadedb_result({"result": [{"name": ["Test"]}]}):
        resp = client.post(
            "/graph/query",
            json={"query": "g.V().hasLabel('Room').valueMap()"},
        )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_raw_query_forbidden_words(client):
    resp = client.post(
        "/graph/query",
        json={"query": "g.V().drop()"},
    )
    assert resp.status_code == 400
    assert "Destructive" in resp.json()["detail"]


@pytest.mark.anyio
async def test_raw_query_non_admin(client, guest_user):
    app.dependency_overrides[get_current_user] = lambda: guest_user
    resp = client.post(
        "/graph/query",
        json={"query": "g.V().valueMap()"},
    )
    assert resp.status_code == 403
