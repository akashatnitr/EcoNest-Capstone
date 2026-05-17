"""Tests for user management API routes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.security import create_access_token
from orchestrator.main import app


@pytest.fixture
def client():
    """Return a TestClient for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def mock_session():
    """Return a mocked async SQLAlchemy session."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture(autouse=True)
def override_get_mysql_session(mock_session):
    """Override the get_mysql_session dependency with a mock."""
    from orchestrator.core.database import get_mysql_session

    async def _override():
        return mock_session

    app.dependency_overrides[get_mysql_session] = _override
    yield
    app.dependency_overrides.clear()


def _mock_result(row: dict | None = None, rows: list[dict] | None = None):
    """Build a mocked SQLAlchemy result."""
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    result.mappings.return_value.all.return_value = rows or ([] if row is None else [row])
    result.scalar.return_value = row.get("id") if row else None
    return result


def _token(user_id: int, role: str) -> str:
    return create_access_token({"sub": str(user_id), "role": role})


@pytest.mark.anyio
async def test_list_users_admin_only_success(client, mock_session):
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
        _mock_result(
            rows=[
                {
                    "id": 1,
                    "email": "admin@example.com",
                    "role": "superadmin",
                    "household_id": None,
                    "is_active": True,
                },
                {
                    "id": 2,
                    "email": "user@example.com",
                    "role": "homeowner",
                    "household_id": 1,
                    "is_active": True,
                },
            ]
        ),
    ]

    resp = client.get("/users", headers={"Authorization": f"Bearer {_token(1, 'superadmin')}"})

    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.anyio
async def test_get_user_self_success(client, mock_session):
    user = {
        "id": 2,
        "email": "user@example.com",
        "role": "homeowner",
        "household_id": 1,
        "is_active": True,
    }
    mock_session.execute.side_effect = [_mock_result(user), _mock_result(user)]

    resp = client.get("/users/2", headers={"Authorization": f"Bearer {_token(2, 'homeowner')}"})

    assert resp.status_code == 200
    assert resp.json()["email"] == "user@example.com"


@pytest.mark.anyio
async def test_get_user_other_requires_admin(client, mock_session):
    mock_session.execute.return_value = _mock_result(
        {
            "id": 2,
            "email": "user@example.com",
            "role": "homeowner",
            "household_id": 1,
            "is_active": True,
        }
    )

    resp = client.get("/users/3", headers={"Authorization": f"Bearer {_token(2, 'homeowner')}"})

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_update_self_email_success(client, mock_session):
    before = {
        "id": 2,
        "email": "old@example.com",
        "role": "homeowner",
        "household_id": 1,
        "is_active": True,
    }
    after = {**before, "email": "new@example.com"}
    mock_session.execute.side_effect = [_mock_result(before), _mock_result(None), _mock_result(after)]

    resp = client.put(
        "/users/2",
        headers={"Authorization": f"Bearer {_token(2, 'homeowner')}"},
        json={"email": "new@example.com"},
    )

    assert resp.status_code == 200
    assert resp.json()["email"] == "new@example.com"
    assert mock_session.commit.await_count == 1


@pytest.mark.anyio
async def test_update_other_user_requires_admin(client, mock_session):
    mock_session.execute.return_value = _mock_result(
        {
            "id": 2,
            "email": "user@example.com",
            "role": "homeowner",
            "household_id": 1,
            "is_active": True,
        }
    )

    resp = client.put(
        "/users/3",
        headers={"Authorization": f"Bearer {_token(2, 'homeowner')}"},
        json={"email": "other@example.com"},
    )

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_non_admin_cannot_change_active_status(client, mock_session):
    mock_session.execute.return_value = _mock_result(
        {
            "id": 2,
            "email": "user@example.com",
            "role": "homeowner",
            "household_id": 1,
            "is_active": True,
        }
    )

    resp = client.put(
        "/users/2",
        headers={"Authorization": f"Bearer {_token(2, 'homeowner')}"},
        json={"is_active": False},
    )

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_deactivate_user_success(client, mock_session):
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
        _mock_result({"id": 2}),
        _mock_result(None),
    ]

    resp = client.delete("/users/2", headers={"Authorization": f"Bearer {_token(1, 'superadmin')}"})

    assert resp.status_code == 204
    assert mock_session.commit.await_count == 1


@pytest.mark.anyio
async def test_grant_device_access_success(client, mock_session):
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
        _mock_result({"id": 2}),
        _mock_result({"id": 99}),
        _mock_result(None),
    ]

    with patch("orchestrator.api.users.grant_access_to_graph", new=AsyncMock()) as grant:
        resp = client.post(
            "/users/2/grant-access",
            headers={"Authorization": f"Bearer {_token(1, 'superadmin')}"},
            json={"device_id": 99, "permission": "device:read"},
        )

    assert resp.status_code == 204
    assert mock_session.commit.await_count == 1
    grant.assert_awaited_once_with(
        mysql_session=mock_session,
        user_id=2,
        room_id=None,
        device_id=99,
        permission="device:read",
        allowed_start_hour=None,
        allowed_end_hour=None,
    )


@pytest.mark.anyio
async def test_grant_access_requires_room_or_device(client, mock_session):
    mock_session.execute.return_value = _mock_result(
        {
            "id": 1,
            "email": "admin@example.com",
            "role": "superadmin",
            "household_id": None,
            "is_active": True,
        }
    )

    resp = client.post(
        "/users/2/grant-access",
        headers={"Authorization": f"Bearer {_token(1, 'superadmin')}"},
        json={},
    )

    assert resp.status_code == 400
