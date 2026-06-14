"""Tests for sensor reading ingestion routes."""

from unittest.mock import Mock


def _device_result(row: dict | None) -> Mock:
    result = Mock()
    result.mappings.return_value.first.return_value = row
    return result


def test_add_single_reading(
    client,
    override_current_user,
    override_mysql_session,
    mock_mysql_session,
):
    mock_mysql_session.execute.side_effect = [
        _device_result({"room_id": 1, "device_type": "sound_sensor", "is_active": True}),
        Mock(),
    ]

    response = client.post(
        "/readings/add",
        json={"device_id": 28, "data": {"sound_level": 42}},
    )

    assert response.status_code == 201
    assert response.json()["inserted"] == 1
    mock_mysql_session.commit.assert_awaited_once()


def test_add_reading_rejects_invalid_device(
    client,
    override_current_user,
    override_mysql_session,
    mock_mysql_session,
):
    mock_mysql_session.execute.return_value = _device_result(None)

    response = client.post(
        "/readings/add",
        json={"device_id": 999, "data": {"sound_level": 42}},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["inserted"] == 0
    mock_mysql_session.rollback.assert_awaited_once()
