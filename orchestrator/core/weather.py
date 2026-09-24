"""Home Assistant weather forecast collection for adaptive recommendations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import Settings

WEATHER_FORECAST_SCHEMA = """
CREATE TABLE IF NOT EXISTS weather_forecasts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_entity_id VARCHAR(255) NOT NULL,
    forecast_at TIMESTAMP NOT NULL,
    fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    condition_name VARCHAR(64), temperature_f FLOAT, humidity_percent FLOAT,
    precipitation_in FLOAT, wind_speed_mph FLOAT,
    UNIQUE KEY unique_weather_forecast (source_entity_id, forecast_at),
    INDEX idx_weather_forecast_time (forecast_at)
)
"""


async def ensure_weather_forecast_schema(session: AsyncSession) -> None:
    """Create forecast storage for both fresh and existing databases."""
    await session.execute(text(WEATHER_FORECAST_SCHEMA))
    await session.commit()


async def fetch_hourly_forecast(settings: Settings) -> tuple[str, list[dict[str, Any]]]:
    """Read Home Assistant's hourly forecast service without altering any device."""
    if not settings.HA_TOKEN:
        return "", []
    headers = {"Authorization": f"Bearer {settings.HA_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            states = await client.get(f"{settings.HA_URL.rstrip('/')}/api/states", headers=headers)
            states.raise_for_status()
            weather = next(
                (item for item in states.json() if str(item.get("entity_id", "")).startswith("weather.")),
                None,
            )
            if not isinstance(weather, dict):
                return "", []
            entity_id = str(weather["entity_id"])
            response = await client.post(
                f"{settings.HA_URL.rstrip('/')}/api/services/weather/get_forecasts?return_response",
                headers={**headers, "Content-Type": "application/json"},
                json={"entity_id": entity_id, "type": "hourly"},
            )
            response.raise_for_status()
    except httpx.HTTPError:
        return "", []
    payload = response.json()
    forecasts = (
        payload.get("service_response", {}).get(entity_id, {}).get("forecast", [])
        if isinstance(payload, dict)
        else []
    )
    return entity_id, [item for item in forecasts if isinstance(item, dict)]


async def store_hourly_forecast(
    session: AsyncSession, source_entity_id: str, forecasts: list[dict[str, Any]]
) -> int:
    """Upsert compact hourly forecast facts used in decision evidence."""
    stored = 0
    for item in forecasts:
        forecast_at = _forecast_time(item.get("datetime"))
        if forecast_at is None:
            continue
        await session.execute(
            text(
                """INSERT INTO weather_forecasts (source_entity_id, forecast_at, condition_name,
                temperature_f, humidity_percent, precipitation_in, wind_speed_mph)
                VALUES (:source, :forecast_at, :condition, :temperature, :humidity, :precipitation, :wind_speed)
                ON DUPLICATE KEY UPDATE fetched_at = CURRENT_TIMESTAMP, condition_name = VALUES(condition_name),
                temperature_f = VALUES(temperature_f), humidity_percent = VALUES(humidity_percent),
                precipitation_in = VALUES(precipitation_in), wind_speed_mph = VALUES(wind_speed_mph)"""
            ),
            {"source": source_entity_id, "forecast_at": forecast_at, "condition": item.get("condition"),
             "temperature": _number(item.get("temperature")), "humidity": _number(item.get("humidity")),
             "precipitation": _number(item.get("precipitation")), "wind_speed": _number(item.get("wind_speed"))},
        )
        stored += 1
    return stored


def _forecast_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
