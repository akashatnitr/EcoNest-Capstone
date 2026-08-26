"""Tests for the read-only monitor API guards."""


def test_monitor_page_is_served(client):
    response = client.get("/monitor")

    assert response.status_code == 200
    assert "EcoNest Data Explorer" in response.text
    assert "Quick queries" in response.text
    assert "Latest readings" in response.text


def test_monitor_rejects_mutating_sql(client, override_mysql_session):

    response = client.post(
        "/monitor/api/query",
        json={"query": "DELETE FROM sensor_readings"},
    )

    assert response.status_code == 400


def test_monitor_rejects_session_table_query(client, override_mysql_session):

    response = client.post(
        "/monitor/api/query",
        json={"query": "SELECT refresh_token FROM user_sessions"},
    )

    assert response.status_code == 400
