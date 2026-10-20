"""Tests for the auth system."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
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


def _mock_rows(rows: list[dict]):
    """Build a mock result for a query returning several rows."""
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    return result


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_register_success(client, mock_session):

    mock_session.execute.return_value = _mock_result(None)

    resp = client.post(
        "/auth/register",
        json={
            "email": "test@example.com",
            "password": "secret",
            "household_profile": "city_apartment",
        },
    )

    assert resp.status_code == 201

    data = resp.json()

    assert data["email"] == "test@example.com"
    assert data["role"] == "homeowner"
    assert data["household_id"] == 1
    assert data["household_name"] == "City Apartment Simulation"


@pytest.mark.anyio
async def test_connected_home_registration_reuses_live_household(client, mock_session):
    """A connected-home registration must not duplicate the HA household."""

    mock_session.execute.side_effect = [
        _mock_result(None),
        _mock_result({"id": 9, "name": "Connected Home"}),
        _mock_result({"id": 12}),
    ]

    resp = client.post(
        "/auth/register",
        json={"email": "live@example.com", "password": "secret"},
    )

    assert resp.status_code == 201
    assert resp.json()["household_id"] == 9
    sql_statements = [str(call.args[0]) for call in mock_session.execute.call_args_list]
    assert not any("INSERT INTO households" in statement for statement in sql_statements)


@pytest.mark.anyio
async def test_register_duplicate_email(client, mock_session):

    mock_session.execute.return_value = _mock_result({"id": 1})

    resp = client.post(
        "/auth/register",
        json={
            "email": "dup@example.com",
            "password": "secret",
        },
    )

    assert resp.status_code == 400

    assert "already registered" in resp.json()["detail"]


@pytest.mark.anyio
async def test_register_non_homeowner_rejected(client, mock_session):

    resp = client.post(
        "/auth/register",
        json={
            "email": "guest@example.com",
            "password": "secret",
            "role": "guest",
        },
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
        "/auth/login",
        data={
            "username": "test@example.com",
            "password": "secret",
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    assert resp.status_code == 200

    data = resp.json()

    assert "access_token" in data
    assert "refresh_token" in data


@pytest.mark.anyio
async def test_login_invalid_credentials(client, mock_session):

    mock_session.execute.return_value = _mock_result(None)

    resp = client.post(
        "/auth/login",
        data={
            "username": "bad@example.com",
            "password": "wrong",
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    assert resp.status_code == 401


# ------------------------------------------------------------------
# Token refresh
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_refresh_success(client, mock_session):

    refresh = create_refresh_token({"sub": "1"})

    mock_session.execute.side_effect = [
        _mock_result(
            {
                "refresh_token": hash_refresh_token(refresh),
            }
        ),
        _mock_result(
            {
                "role": "homeowner",
            }
        ),
    ]

    resp = client.post(
        "/auth/refresh",
        json={
            "refresh_token": refresh,
        },
    )

    assert resp.status_code == 200

    data = resp.json()

    assert "access_token" in data


@pytest.mark.anyio
async def test_refresh_invalid_token(client, mock_session):

    resp = client.post(
        "/auth/refresh",
        json={
            "refresh_token": "invalid.token.here",
        },
    )

    assert resp.status_code == 401


# ------------------------------------------------------------------
# Logout
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_logout_success(client, mock_session):

    resp = client.post(
        "/auth/logout",
        json={
            "refresh_token": "some_token",
        },
    )

    assert resp.status_code == 204


# ------------------------------------------------------------------
# Current user / me
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_me_success(client, mock_session):

    token = create_access_token(
        {
            "sub": "1",
            "role": "homeowner",
        }
    )

    mock_session.execute.return_value = _mock_result(
        {
            "id": 1,
            "email": "me@example.com",
            "role": "homeowner",
            "household_id": None,
            "is_active": True,
        }
    )

    resp = client.get(
        "/auth/me",
        headers={
            "Authorization": f"Bearer {token}",
        },
    )

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

    token = create_access_token(
        {
            "sub": "2",
            "role": "guest",
        }
    )

    mock_session.execute.return_value = _mock_result(
        {
            "id": 2,
            "email": "guest@example.com",
            "role": "guest",
            "household_id": None,
            "is_active": True,
        }
    )

    resp = client.get(
        "/users",
        headers={
            "Authorization": f"Bearer {token}",
        },
    )

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_update_user_rejects_unknown_role(client, mock_session):

    token = create_access_token(
        {
            "sub": "1",
            "role": "superadmin",
        }
    )

    mock_session.execute.return_value = _mock_result(
        {
            "id": 1,
            "email": "admin@example.com",
            "role": "superadmin",
            "household_id": None,
            "is_active": True,
        }
    )

    resp = client.put(
        "/users/7",
        headers={
            "Authorization": f"Bearer {token}",
        },
        json={
            "role": "unknown",
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid role"


@pytest.mark.anyio
async def test_grant_room_access_writes_access_table(
    client,
    mock_session,
):

    token = create_access_token(
        {
            "sub": "1",
            "role": "superadmin",
        }
    )

    mock_session.execute.side_effect = [
        _mock_result(
            {
                "id": 1,
                "email": "admin@example.com",
                "role": "superadmin",
                "household_id": None,
                "is_active": True,
            }
        ),
        _mock_result(None),
    ]

    with patch(
        "orchestrator.api.users.grant_access_to_graph",
        new=AsyncMock(),
    ) as graph_grant:

        resp = client.post(
            "/users/7/grant-access",
            headers={
                "Authorization": f"Bearer {token}",
            },
            json={
                "room_id": 10,
                "permission": "room:read",
            },
        )

    assert resp.status_code == 204

    assert mock_session.execute.await_count == 2
    assert mock_session.commit.await_count == 1

    graph_grant.assert_awaited_once_with(
        mysql_session=mock_session,
        user_id=7,
        room_id=10,
        device_id=None,
        permission="room:read",
        allowed_start_hour=None,
        allowed_end_hour=None,
    )


def test_superadmin_can_list_all_household_contexts(client, mock_session):
    """The household switcher exposes every household only to an administrator."""
    token = create_access_token({"sub": "1", "role": "superadmin"})
    mock_session.execute.side_effect = [
        _mock_result(
            {
                "id": 1,
                "email": "admin@example.com",
                "role": "superadmin",
                "household_id": None,
                "is_active": True,
            }
        ),
        _mock_rows(
            [
                {"id": 2, "name": "City Apartment Simulation"},
                {"id": 3, "name": "Family Home Simulation"},
            ]
        ),
    ]

    response = client.get("/auth/households", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == [
        {"id": 2, "name": "City Apartment Simulation"},
        {"id": 3, "name": "Family Home Simulation"},
    ]


def test_homeowner_cannot_select_another_household(client, mock_session):
    """The selection API enforces the homeowner household boundary."""
    token = create_access_token({"sub": "2", "role": "homeowner"})
    mock_session.execute.side_effect = [
        _mock_result(
            {
                "id": 2,
                "email": "owner@example.com",
                "role": "homeowner",
                "household_id": 4,
                "household_name": "Connected Home",
                "is_active": True,
            }
        ),
        _mock_rows([{"id": 4, "name": "Connected Home"}]),
    ]

    response = client.post(
        "/auth/active-household",
        headers={"Authorization": f"Bearer {token}"},
        json={"household_id": 5},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have access to that household."
