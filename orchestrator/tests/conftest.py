"""Shared pytest fixtures for the orchestrator test suite."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.database import get_mysql_session
from orchestrator.core.security import create_access_token
from orchestrator.main import app


@pytest.fixture
def client():
    """Return a TestClient for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def test_user():
    """Return a standard test user profile."""
    return UserProfile(
        id=1,
        email="test@example.com",
        role="homeowner",
        household_id=1,
        is_active=True,
    )


@pytest.fixture
def admin_user():
    """Return an admin test user profile."""
    return UserProfile(
        id=1,
        email="admin@example.com",
        role="superadmin",
        household_id=None,
        is_active=True,
    )


@pytest.fixture
def guest_user():
    """Return a guest test user profile."""
    return UserProfile(
        id=2,
        email="guest@example.com",
        role="guest",
        household_id=None,
        is_active=True,
    )


@pytest.fixture
def auth_headers(test_user):
    """Return Authorization headers for the test user."""
    token = create_access_token({"sub": str(test_user.id), "role": test_user.role})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(admin_user):
    """Return Authorization headers for the admin user."""
    token = create_access_token({"sub": str(admin_user.id), "role": admin_user.role})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_mysql_session():
    """Return a mocked async SQLAlchemy session."""
    session = AsyncMock()
    return session


@pytest.fixture
def override_mysql_session(mock_mysql_session):
    """Override get_mysql_session dependency with a mock."""
    app.dependency_overrides[get_mysql_session] = lambda: mock_mysql_session
    yield mock_mysql_session
    app.dependency_overrides.pop(get_mysql_session, None)


@pytest.fixture
def override_current_user(test_user):
    """Override get_current_user dependency with the test user."""
    app.dependency_overrides[get_current_user] = lambda: test_user
    yield test_user
    app.dependency_overrides.pop(get_current_user, None)
