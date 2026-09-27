"""Home Assistant weather forecast collection for adaptive recommendations."""

from __future__ import annotations

from datetime import UTC, date, datetime
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
HISTORICAL_WEATHER_SOURCE = "open_meteo_archive"


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


async def fetch_historical_hourly_weather(
    settings: Settings,
    *,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Fetch hourly archived outdoor conditions for the configured home location."""
    if settings.HOME_LATITUDE is None or settings.HOME_LONGITUDE is None:
        raise ValueError("HOME_LATITUDE and HOME_LONGITUDE are required for historical weather")
    if start_date > end_date:
        raise ValueError("historical-weather start date must be on or before end date")
    params = {
        "latitude": settings.HOME_LATITUDE,
        "longitude": settings.HOME_LONGITUDE,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "hourly": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "UTC",
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.get("https://archive-api.open-meteo.com/v1/archive", params=params)
        response.raise_for_status()
    hourly = response.json().get("hourly")
    if not isinstance(hourly, dict):
        raise RuntimeError("Historical weather API did not return hourly data")
    times = hourly.get("time")
    if not isinstance(times, list):
        raise RuntimeError("Historical weather API did not return hourly timestamps")
    rows: list[dict[str, Any]] = []
    for index, value in enumerate(times):
        forecast_at = _forecast_time(value)
        if forecast_at is None:
            continue
        rows.append(
            {
                "datetime": forecast_at,
                "condition": _archive_condition(_item(hourly, "weather_code", index)),
                "temperature": _item(hourly, "temperature_2m", index),
                "humidity": _item(hourly, "relative_humidity_2m", index),
                "precipitation": _item(hourly, "precipitation", index),
                "wind_speed": _item(hourly, "wind_speed_10m", index),
            }
        )
    return rows


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
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _item(hourly: dict[str, Any], name: str, index: int) -> Any:
    """Get one variable from an hourly archive response without trusting its length."""
    values = hourly.get(name)
    return values[index] if isinstance(values, list) and index < len(values) else None


def _archive_condition(value: Any) -> str | None:
    """Keep the archive weather code as transparent, non-invented source context."""
    code = _number(value)
    return f"wmo:{int(code)}" if code is not None else None
