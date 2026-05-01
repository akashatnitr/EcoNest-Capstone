"""Tests for the auth system."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
)
from orchestrator.main import app


@pytest.fixture
def client():
    """Return a TestClient for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def mock_session():
    """Return a mocked async SQLAlchemy session."""
    session = AsyncMock(spec=AsyncSession)
    return session


@pytest.fixture(autouse=True)
def override_get_mysql_session(mock_session):
    """Override the get_mysql_session dependency with a mock."""
    from orchestrator.core.database import get_mysql_session

    async def _override():
        return mock_session

    app.dependency_overrides[get_mysql_session] = _override
    yield
    app.dependency_overrides.clear()


def _mock_result(row: dict | None):
    """Helper to build a mocked SQLAlchemy result."""
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    result.scalar.return_value = row.get("id") if row else None
    result.lastrowid = row.get("id") if row else 1
    return result


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_register_success(client, mock_session):
    mock_session.execute.return_value = _mock_result(None)
    resp = client.post(
        "/auth/register", json={"email": "test@example.com", "password": "secret"}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "test@example.com"
    assert data["role"] == "homeowner"


@pytest.mark.anyio
async def test_register_duplicate_email(client, mock_session):
    mock_session.execute.return_value = _mock_result({"id": 1})
    resp = client.post(
        "/auth/register", json={"email": "dup@example.com", "password": "secret"}
    )
    assert resp.status_code == 400
    assert "already registered" in resp.json()["detail"]


@pytest.mark.anyio
async def test_register_non_homeowner_rejected(client, mock_session):
    resp = client.post(
        "/auth/register",
        json={"email": "guest@example.com", "password": "secret", "role": "guest"},
    )
    assert resp.status_code == 403


# ------------------------------------------------------------------
# Login
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_login_success(client, mock_session):
    hashed = hash_password("secret")
    mock_session.execute.return_value = _mock_result(
        {
            "id": 1,
            "email": "test@example.com",
            "hashed_password": hashed,
            "role": "homeowner",
            "is_active": True,
        }
    )
    resp = client.post(
        "/auth/login", json={"email": "test@example.com", "password": "secret"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


@pytest.mark.anyio
async def test_login_invalid_credentials(client, mock_session):
    mock_session.execute.return_value = _mock_result(None)
    resp = client.post(
        "/auth/login", json={"email": "bad@example.com", "password": "wrong"}
    )
    assert resp.status_code == 401


# ------------------------------------------------------------------
# Token refresh
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_refresh_success(client, mock_session):
    refresh = create_refresh_token({"sub": "1"})
    mock_session.execute.side_effect = [
        _mock_result({"id": 99}),  # session exists
        _mock_result({"role": "homeowner"}),  # user lookup
    ]
    resp = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data


@pytest.mark.anyio
async def test_refresh_invalid_token(client, mock_session):
    resp = client.post("/auth/refresh", json={"refresh_token": "invalid.token.here"})
    assert resp.status_code == 401


# ------------------------------------------------------------------
# Logout
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_logout_success(client, mock_session):
    resp = client.post("/auth/logout", json={"refresh_token": "some_token"})
    assert resp.status_code == 204


# ------------------------------------------------------------------
# Current user / me
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_me_success(client, mock_session):
    token = create_access_token({"sub": "1", "role": "homeowner"})
    mock_session.execute.return_value = _mock_result(
        {
            "id": 1,
            "email": "me@example.com",
            "role": "homeowner",
            "household_id": None,
            "is_active": True,
        }
    )
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "me@example.com"


@pytest.mark.anyio
async def test_me_no_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


# ------------------------------------------------------------------
# Permission enforcement
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_users_admin_only(client, mock_session):
    # Guest token
    token = create_access_token({"sub": "2", "role": "guest"})
    mock_session.execute.return_value = _mock_result(
        {
            "id": 2,
            "email": "guest@example.com",
            "role": "guest",
            "household_id": None,
            "is_active": True,
        }
    )
    resp = client.get("/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
