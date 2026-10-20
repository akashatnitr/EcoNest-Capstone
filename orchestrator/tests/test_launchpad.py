"""Tests for the EcoNest service launchpad."""


def test_launchpad_is_served(client) -> None:
    response = client.get("/launchpad")

    assert response.status_code == 200
    assert "Dashboards, home data, and developer tools" in response.text
    assert "User prompts" in response.text
    assert "Benchmark report" in response.text
    assert "MCP activity" in response.text
    assert "MySQL Explorer" in response.text
    assert "Home Assistant" in response.text
    assert "Household context" in response.text
    assert 'data-page="/integrations"' in response.text
    assert "Google Calendar</strong>" not in response.text
    assert "Gmail</strong>" not in response.text
    assert 'id="contentFrame"' in response.text
    assert 'allow="microphone"' in response.text
    assert "Open separately" in response.text
    assert "cannot be embedded safely" in response.text
    assert 'data-page="/simulation"' in response.text
    assert 'data-simulation-only="true"' in response.text
    assert ".nav-link[hidden], .nav-group[hidden] { display: none; }" in response.text
    assert ">Demo<" not in response.text
    assert 'id="navCollapse"' in response.text
    assert "econest_launchpad_navigation_collapsed" in response.text


def test_root_serves_launchpad(client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "ECONEST / LAUNCHPAD" in response.text


def test_household_context_workspace_is_served(client) -> None:
    response = client.get("/integrations")

    assert response.status_code == 200
    assert "Household context" in response.text
    assert "Google Calendar" in response.text
    assert "Gmail" in response.text
    assert "Weather" in response.text


def test_simulated_household_command_page_supports_voice_review(client) -> None:
    """Simulated households use the same safe record-review-interpret flow."""
    response = client.get("/simulation/command")

    assert response.status_code == 200
    assert 'id="voiceInput"' in response.text
    assert "Voice transcript ready" in response.text
    assert "/command/transcribe" in response.text
    assert "No simulated action has been sent." in response.text
