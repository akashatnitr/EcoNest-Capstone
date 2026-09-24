"""Hourly energy analytics derived from retained Home Assistant readings."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ENERGY_ANALYTICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS energy_hourly_analytics (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    device_id INT NOT NULL,
    room_id INT NOT NULL,
    hour_start DATETIME NOT NULL,
    sample_count INT UNSIGNED NOT NULL DEFAULT 0,
    avg_power_w FLOAT NULL,
    peak_power_w FLOAT NULL,
    metered_energy_kwh FLOAT NULL,
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_energy_hourly_device (device_id, hour_start),
    INDEX idx_energy_hourly_room_time (room_id, hour_start),
    INDEX idx_energy_hourly_time (hour_start),
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
)
"""


async def ensure_energy_analytics_schema(session: AsyncSession) -> None:
    """Create the idempotent hourly analytics cache for existing installations."""
    await session.execute(text(ENERGY_ANALYTICS_SCHEMA))
    await session.commit()


async def rebuild_energy_analytics(
    session: AsyncSession,
    *,
    days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Recompute bounded hourly power and cumulative-meter deltas from readings.

    Energy values are only reported when Home Assistant supplied a cumulative
    energy meter in kWh/Wh.  Power readings are summarized independently, so a
    sparse state-change stream never becomes a fabricated kWh estimate.
    """
    if not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")

    end = now or datetime.now()
    start = end - timedelta(days=days)
    await session.execute(
        text(
            "DELETE FROM energy_hourly_analytics "
            "WHERE hour_start >= :start AND hour_start < :end"
        ),
        {"start": start.replace(minute=0, second=0, microsecond=0), "end": end},
    )
    result = await session.execute(
        text(
            """
            INSERT INTO energy_hourly_analytics (
                device_id, room_id, hour_start, sample_count,
                avg_power_w, peak_power_w, metered_energy_kwh
            )
            SELECT
                reset_safe.device_id,
                reset_safe.room_id,
                reset_safe.hour_start,
                SUM(reset_safe.sample_count) AS sample_count,
                AVG(reset_safe.avg_power_w) AS avg_power_w,
                MAX(reset_safe.peak_power_w) AS peak_power_w,
                SUM(reset_safe.metered_energy_kwh) AS metered_energy_kwh
            FROM (
                SELECT
                    sr.device_id,
                    sr.room_id,
                    CAST(DATE_FORMAT(sr.timestamp, '%Y-%m-%d %H:00:00') AS DATETIME)
                        AS hour_start,
                    COUNT(*) AS sample_count,
                    AVG(CASE
                        WHEN JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.device_class')) = 'power'
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.unit_of_measurement')) IN ('W', 'w')
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) REGEXP '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN CAST(JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) AS DECIMAL(18, 6))
                    END) AS avg_power_w,
                    MAX(CASE
                        WHEN JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.device_class')) = 'power'
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.unit_of_measurement')) IN ('W', 'w')
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) REGEXP '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN CAST(JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) AS DECIMAL(18, 6))
                    END) AS peak_power_w,
                    GREATEST(MAX(CASE
                        WHEN JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.device_class')) = 'energy'
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.unit_of_measurement')) IN ('kWh', 'KWH')
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) REGEXP '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN CAST(JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) AS DECIMAL(18, 6))
                        WHEN JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.device_class')) = 'energy'
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.unit_of_measurement')) IN ('Wh', 'wh')
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) REGEXP '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN CAST(JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) AS DECIMAL(18, 6)) / 1000
                    END) - MIN(CASE
                        WHEN JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.device_class')) = 'energy'
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.unit_of_measurement')) IN ('kWh', 'KWH')
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) REGEXP '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN CAST(JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) AS DECIMAL(18, 6))
                        WHEN JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.device_class')) = 'energy'
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.unit_of_measurement')) IN ('Wh', 'wh')
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) REGEXP '^-?[0-9]+(\\.[0-9]+)?$'
                        THEN CAST(JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) AS DECIMAL(18, 6)) / 1000
                    END), 0) AS metered_energy_kwh
                FROM sensor_readings sr
                JOIN devices d ON d.id = sr.device_id
                WHERE sr.timestamp >= :start AND sr.timestamp < :end
                  AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.device_class')) IN ('power', 'energy')
                  -- Daily, difference, and "saved" sensors are derived views of
                  -- another meter. Keeping them would double-count the same load.
                  AND (d.ha_entity_id IS NULL OR d.ha_entity_id NOT REGEXP
                       '_(energy_today|energy_difference|power_energy|energy_saved)$')
                -- A cumulative meter may reset at midnight or the start of a
                -- billing period. Its last_reset value makes each side of that
                -- reset a separate sequence, preventing a reset from becoming
                -- a false, enormous consumption delta.
                GROUP BY
                    sr.device_id,
                    sr.room_id,
                    hour_start,
                    JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.last_reset'))
            ) AS reset_safe
            GROUP BY reset_safe.device_id, reset_safe.room_id, reset_safe.hour_start
            HAVING avg_power_w IS NOT NULL
                OR peak_power_w IS NOT NULL
                OR metered_energy_kwh IS NOT NULL
            """
        ),
        {"start": start, "end": end},
    )
    await session.execute(text("DELETE FROM home_analytics"))
    await session.execute(
        text(
            """
            INSERT INTO home_analytics (
                room_id, hour_of_day, motion_probability, weekly_pattern, computed_at
            )
            SELECT
                sr.room_id,
                HOUR(sr.timestamp),
                AVG(CASE
                    WHEN JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.device_class')) = 'motion'
                     AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) = 'on'
                    THEN 1 ELSE 0 END),
                JSON_OBJECT(
                    'sample_count', COUNT(*),
                    'motion_on_events', SUM(CASE
                        WHEN JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.device_class')) = 'motion'
                         AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.state')) = 'on'
                        THEN 1 ELSE 0 END)
                ),
                CURRENT_TIMESTAMP
            FROM sensor_readings sr
            WHERE sr.timestamp >= :start AND sr.timestamp < :end
              AND JSON_UNQUOTE(JSON_EXTRACT(sr.data, '$.attributes.device_class')) = 'motion'
            GROUP BY sr.room_id, HOUR(sr.timestamp)
            """
        ),
        {"start": start, "end": end},
    )
    await session.commit()
    return {
        "days": days,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows_rebuilt": max(result.rowcount or 0, 0),
    }
