"""Tests for the browser command console."""


def test_command_page_is_served(client) -> None:
    response = client.get("/command")

    assert response.status_code == 200
    assert "EcoNest Command Console" in response.text
    assert "/mcp/task" in response.text
    assert "/auth/register" in response.text
