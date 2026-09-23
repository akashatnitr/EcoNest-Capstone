"""Tests for the local schema visualization page."""


def test_schema_page_is_served(client):
    response = client.get("/schema")

    assert response.status_code == 200
    assert "EcoNest MySQL relationship map" in response.text
    assert "sensor_readings" in response.text
    assert "user_device_access" in response.text
