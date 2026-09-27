"""Tests for importing Home Assistant long-term energy statistics."""

from orchestrator.core.ha_statistics import (
    EnergyStatisticSource,
    _analytics_rows,
    _websocket_url,
)


def test_statistics_rows_combine_power_and_energy_delta() -> None:
    """Hourly rows retain power values and turn cumulative sums into deltas."""
    source = EnergyStatisticSource(
        device_id=7,
        room_id=3,
        power_statistic_id="sensor.example_power_minute_average",
        energy_statistic_id="sensor.example_energy_this_month",
    )
    rows = _analytics_rows(
        [source],
        {
            source.power_statistic_id: [
                {"start": 1_774_000_000_000, "mean": 120.0, "max": 180.0},
                {"start": 1_774_003_600_000, "mean": 150.0, "max": 220.0},
            ],
            source.energy_statistic_id: [
                {"start": 1_774_000_000_000, "sum": 10.0},
                {"start": 1_774_003_600_000, "sum": 10.25},
            ],
        },
    )

    assert len(rows) == 2
    assert rows[0]["avg_power_w"] == 120.0
    assert rows[0]["metered_energy_kwh"] is None
    assert rows[1]["peak_power_w"] == 220.0
    assert rows[1]["metered_energy_kwh"] == 0.25


def test_statistics_websocket_url_uses_home_assistant_endpoint() -> None:
    """HTTP and HTTPS Home Assistant endpoints map to the correct WS scheme."""
    assert _websocket_url("http://homeassistant:8123") == "ws://homeassistant:8123/api/websocket"
    assert _websocket_url("https://home.example") == "wss://home.example/api/websocket"
