"""Tests for the EcoNest service launchpad."""


def test_launchpad_is_served(client) -> None:
    response = client.get("/launchpad")

    assert response.status_code == 200
    assert "Open EcoNest." in response.text
    assert "User prompts" in response.text
    assert "Home Assistant" in response.text


def test_root_serves_launchpad(client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "ECONEST / LAUNCHPAD" in response.text
