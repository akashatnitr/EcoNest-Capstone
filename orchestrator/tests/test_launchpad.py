"""Tests for the EcoNest service launchpad."""


def test_launchpad_is_served(client) -> None:
    response = client.get("/launchpad")

    assert response.status_code == 200
    assert "Dashboards, home data, and developer tools" in response.text
    assert "User prompts" in response.text
    assert "Benchmark report" in response.text
    assert "Home Assistant" in response.text
    assert 'id="contentFrame"' in response.text
    assert "Open separately" in response.text
    assert "Home Assistant prevents other pages from embedding" in response.text
    assert ">Demo<" not in response.text
    assert 'id="navCollapse"' in response.text
    assert "econest_launchpad_navigation_collapsed" in response.text


def test_root_serves_launchpad(client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "ECONEST / LAUNCHPAD" in response.text
