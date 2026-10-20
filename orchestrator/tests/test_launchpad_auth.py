"""Browser-session protection for the Launchpad workspace."""

from datetime import timedelta

from orchestrator import main
from orchestrator.core.security import create_access_token


def test_launchpad_redirects_anonymous_browser_to_login(client, monkeypatch) -> None:
    """The workspace shell cannot be opened without a browser session."""
    monkeypatch.setattr(main.settings, "LAUNCHPAD_AUTH_REQUIRED", True)

    response = client.get("/launchpad", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/launchpad"


def test_launchpad_accepts_a_valid_access_cookie(client, monkeypatch) -> None:
    """A signed-in browser can load the shell and its embedded routes."""
    monkeypatch.setattr(main.settings, "LAUNCHPAD_AUTH_REQUIRED", True)
    token = create_access_token({"sub": "1", "role": "homeowner"}, expires_delta=timedelta(minutes=5))
    client.cookies.set(main.settings.AUTH_SESSION_COOKIE_NAME, token)

    response = client.get("/launchpad")

    assert response.status_code == 200
    assert "EcoNest Launchpad" in response.text
    assert 'id="signOut"' in response.text
    assert 'id="activeHousehold"' in response.text
    assert 'fetch("/auth/households"' in response.text
    assert 'fetch("/auth/logout"' in response.text


def test_login_page_is_available_without_a_session(client, monkeypatch) -> None:
    """Anonymous users need a public route through which to establish a session."""
    monkeypatch.setattr(main.settings, "LAUNCHPAD_AUTH_REQUIRED", True)

    response = client.get("/login")

    assert response.status_code == 200
    assert "Create homeowner account" in response.text
    assert "Household profile" in response.text
    assert "City apartment simulation" in response.text
    assert 'id="togglePassword"' in response.text
    assert "Show password" in response.text
