"""Tests for the ontology layer."""

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


@pytest.fixture(autouse=True)
def override_deps(admin_user):
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_mysql_session] = AsyncMock
    yield
    app.dependency_overrides.clear()


# ------------------------------------------------------------------
# List ontology
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_ontology(client):
    resp = client.get("/ontology")
    assert resp.status_code == 200
    data = resp.json()
    assert "Room" in data["classes"]
    assert "hasCapability" in data["object_properties"]


# ------------------------------------------------------------------
# Class details
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_class_smartbulb(client):
    resp = client.get("/ontology/classes/SmartBulb")
    assert resp.status_code == 200
    assert resp.json()["inferred_capabilities"] == ["Dimmable"]


@pytest.mark.anyio
async def test_get_class_not_found(client):
    resp = client.get("/ontology/classes/NonExistent")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# Validate
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_validate(client):
    with patch(
        "orchestrator.api.ontology.validate_graph",
        new=AsyncMock(return_value={"valid": True, "errors": [], "error_count": 0}),
    ):
        resp = client.get("/ontology/validate")
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


# ------------------------------------------------------------------
# Reason
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_reason(client):
    with patch(
        "orchestrator.api.ontology.run_reasoner",
        new=AsyncMock(return_value={"inferred": [], "total": 0}),
    ):
        resp = client.post("/ontology/reason")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ------------------------------------------------------------------
# Upload
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_upload_ttl_admin(client):
    with patch(
        "orchestrator.api.ontology.load_ontology",
        new=AsyncMock(return_value={"classes": ["TestClass"]}),
    ):
        resp = client.post(
            "/ontology/upload",
            files={"file": ("test.ttl", b"@prefix : <http://test#> .", "text/turtle")},
        )
    assert resp.status_code == 201


@pytest.mark.anyio
async def test_upload_non_ttl_rejected(client):
    resp = client.post(
        "/ontology/upload",
        files={"file": ("test.xml", b"<xml/>", "text/xml")},
    )
    assert resp.status_code == 400
