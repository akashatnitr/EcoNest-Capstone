"""Import Home Assistant long-term energy statistics into EcoNest analytics."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import websockets
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import Settings

_POWER_SUFFIX = "_power_minute_average"
_ENERGY_SUFFIX = "_energy_this_month"
_STATISTICS_BATCH_SIZE = 8
_MAX_MESSAGE_BYTES = 64 * 1024 * 1024

CONTEXT_STATISTICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS home_assistant_hourly_statistics (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    statistic_id VARCHAR(255) NOT NULL,
    hour_start DATETIME NOT NULL,
    mean_value FLOAT NULL,
    min_value FLOAT NULL,
    max_value FLOAT NULL,
    state_value FLOAT NULL,
    sum_value FLOAT NULL,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_home_assistant_statistic_hour (statistic_id, hour_start),
    INDEX idx_home_assistant_statistic_time (hour_start),
    INDEX idx_home_assistant_statistic_id_time (statistic_id, hour_start)
)
"""


@dataclass(frozen=True)
class EnergyStatisticSource:
    """A matched Home Assistant power and energy statistic for one device."""

    device_id: int
    room_id: int
    power_statistic_id: str
    energy_statistic_id: str


async def backfill_home_assistant_energy_statistics(
    session: AsyncSession,
    settings: Settings,
    *,
    start: datetime,
    end: datetime | None = None,
) -> dict[str, Any]:
    """Upsert hourly long-term energy statistics from Home Assistant.

    Home Assistant retains hourly aggregates after detailed state history is
    purged.  These rows supplement EcoNest's current detailed reading-derived
    analytics without inserting fabricated raw sensor readings.
    """
    start_utc = _as_utc(start)
    end_utc = _as_utc(end or datetime.now(UTC))
    if start_utc >= end_utc:
        raise ValueError("start must be before end")

    sources = await _energy_statistic_sources(session)
    if not sources:
        return {"sources": 0, "rows_upserted": 0, "start": start_utc.isoformat(), "end": end_utc.isoformat()}

    statistic_ids = [
        statistic_id
        for source in sources
        for statistic_id in (source.power_statistic_id, source.energy_statistic_id)
    ]
    statistics = await _fetch_hourly_statistics(
        settings,
        statistic_ids=statistic_ids,
        start=start_utc,
        end=end_utc,
    )
    rows = _analytics_rows(sources, statistics)
    if rows:
        await session.execute(
            text(
                """
                INSERT INTO energy_hourly_analytics (
                    device_id, room_id, hour_start, sample_count,
                    avg_power_w, peak_power_w, metered_energy_kwh
                ) VALUES (
                    :device_id, :room_id, :hour_start, :sample_count,
                    :avg_power_w, :peak_power_w, :metered_energy_kwh
                )
                ON DUPLICATE KEY UPDATE
                    sample_count = GREATEST(sample_count, VALUES(sample_count)),
                    avg_power_w = COALESCE(VALUES(avg_power_w), avg_power_w),
                    peak_power_w = COALESCE(VALUES(peak_power_w), peak_power_w),
                    metered_energy_kwh = COALESCE(
                        VALUES(metered_energy_kwh), metered_energy_kwh
                    )
                """
            ),
            rows,
        )
        await session.commit()
    return {
        "sources": len(sources),
        "rows_upserted": len(rows),
        "start": start_utc.isoformat(),
        "end": end_utc.isoformat(),
    }


async def backfill_home_assistant_context_statistics(
    session: AsyncSession,
    settings: Settings,
    *,
    start: datetime,
    end: datetime | None = None,
) -> dict[str, Any]:
    """Import retained temperature and motion-device telemetry aggregates."""
    start_utc = _as_utc(start)
    end_utc = _as_utc(end or datetime.now(UTC))
    if start_utc >= end_utc:
        raise ValueError("start must be before end")
    await session.execute(text(CONTEXT_STATISTICS_SCHEMA))
    statistic_ids = await _context_statistic_ids(settings)
    statistics = await _fetch_hourly_statistics(
        settings,
        statistic_ids=statistic_ids,
        start=start_utc,
        end=end_utc,
    )
    rows = _context_rows(statistics)
    if rows:
        await session.execute(
            text(
                """
                INSERT INTO home_assistant_hourly_statistics (
                    statistic_id, hour_start, mean_value, min_value, max_value,
                    state_value, sum_value
                ) VALUES (
                    :statistic_id, :hour_start, :mean_value, :min_value, :max_value,
                    :state_value, :sum_value
                )
                ON DUPLICATE KEY UPDATE
                    mean_value = VALUES(mean_value),
                    min_value = VALUES(min_value),
                    max_value = VALUES(max_value),
                    state_value = VALUES(state_value),
                    sum_value = VALUES(sum_value)
                """
            ),
            rows,
        )
    await session.commit()
    return {
        "statistics": len(statistic_ids),
        "rows_upserted": len(rows),
        "start": start_utc.isoformat(),
        "end": end_utc.isoformat(),
    }


async def _energy_statistic_sources(session: AsyncSession) -> list[EnergyStatisticSource]:
    """Match each supported Home Assistant power statistic to its energy meter."""
    result = await session.execute(
        text(
            """
            SELECT id, room_id, ha_entity_id
            FROM devices
            WHERE ha_entity_id LIKE :power_suffix
               OR ha_entity_id LIKE :energy_suffix
            """
        ),
        {"power_suffix": f"%{_POWER_SUFFIX}", "energy_suffix": f"%{_ENERGY_SUFFIX}"},
    )
    power_devices: dict[str, tuple[int, int, str]] = {}
    energy_entities: dict[str, str] = {}
    for row in result.mappings():
        entity_id = str(row["ha_entity_id"] or "")
        if entity_id.endswith(_POWER_SUFFIX):
            base = entity_id.removesuffix(_POWER_SUFFIX)
            power_devices[base] = (int(row["id"]), int(row["room_id"]), entity_id)
        elif entity_id.endswith(_ENERGY_SUFFIX):
            energy_entities[entity_id.removesuffix(_ENERGY_SUFFIX)] = entity_id

    return [
        EnergyStatisticSource(
            device_id=device_id,
            room_id=room_id,
            power_statistic_id=power_entity,
            energy_statistic_id=energy_entities[base],
        )
        for base, (device_id, room_id, power_entity) in power_devices.items()
        if base in energy_entities
    ]


async def _fetch_hourly_statistics(
    settings: Settings,
    *,
    statistic_ids: list[str],
    start: datetime,
    end: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Read hourly long-term statistics through Home Assistant's WebSocket API."""
    if not settings.HA_TOKEN:
        raise RuntimeError("HA_TOKEN is required to import Home Assistant statistics")
    websocket_url = _websocket_url(settings.HA_URL)
    collected: dict[str, list[dict[str, Any]]] = {}
    async with websockets.connect(
        websocket_url,
        open_timeout=20,
        max_size=_MAX_MESSAGE_BYTES,
    ) as socket:
        await _authenticate(socket, settings.HA_TOKEN)
        for request_id, batch in enumerate(_batches(statistic_ids, _STATISTICS_BATCH_SIZE), start=1):
            await socket.send(
                json.dumps(
                    {
                        "id": request_id,
                        "type": "recorder/statistics_during_period",
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                        "statistic_ids": batch,
                        "period": "hour",
                        "types": ["mean", "max", "sum"],
                    }
                )
            )
            reply = json.loads(await socket.recv())
            if not reply.get("success"):
                error = reply.get("error") or {}
                raise RuntimeError(f"Home Assistant statistics request failed: {error.get('message', 'unknown error')}")
            result = reply.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Home Assistant statistics response was not an object")
            for statistic_id, samples in result.items():
                if isinstance(samples, list):
                    collected[str(statistic_id)] = [sample for sample in samples if isinstance(sample, dict)]
    return collected


async def _context_statistic_ids(settings: Settings) -> list[str]:
    """Find retained room-temperature and motion-device telemetry statistics."""
    metadata = await _fetch_statistic_metadata(settings)
    return sorted(
        statistic_id
        for statistic_id in metadata
        if statistic_id.startswith("sensor.")
        and ("temperature" in statistic_id or "motion_sensor" in statistic_id)
    )


async def _fetch_statistic_metadata(settings: Settings) -> list[str]:
    """List Home Assistant long-term statistic IDs without exposing credentials."""
    if not settings.HA_TOKEN:
        raise RuntimeError("HA_TOKEN is required to import Home Assistant statistics")
    async with websockets.connect(
        _websocket_url(settings.HA_URL),
        open_timeout=20,
        max_size=_MAX_MESSAGE_BYTES,
    ) as socket:
        await _authenticate(socket, settings.HA_TOKEN)
        await socket.send(json.dumps({"id": 1, "type": "recorder/list_statistic_ids"}))
        reply = json.loads(await socket.recv())
    if not reply.get("success"):
        error = reply.get("error") or {}
        raise RuntimeError(f"Home Assistant statistic list failed: {error.get('message', 'unknown error')}")
    result = reply.get("result")
    if not isinstance(result, list):
        raise RuntimeError("Home Assistant statistic list was not an array")
    return [
        str(item["statistic_id"])
        for item in result
        if isinstance(item, dict) and isinstance(item.get("statistic_id"), str)
    ]


async def _authenticate(socket: Any, token: str) -> None:
    """Complete the Home Assistant WebSocket authentication handshake."""
    required = json.loads(await socket.recv())
    if required.get("type") != "auth_required":
        raise RuntimeError("Home Assistant WebSocket did not request authentication")
    await socket.send(json.dumps({"type": "auth", "access_token": token}))
    response = json.loads(await socket.recv())
    if response.get("type") != "auth_ok":
        raise RuntimeError("Home Assistant WebSocket authentication failed")


def _analytics_rows(
    sources: list[EnergyStatisticSource],
    statistics: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Combine power means and cumulative-energy deltas into hourly rows."""
    rows: list[dict[str, Any]] = []
    for source in sources:
        power_samples = _samples_by_start(statistics.get(source.power_statistic_id, []))
        energy_samples = _samples_by_start(statistics.get(source.energy_statistic_id, []))
        prior_sum: float | None = None
        for timestamp_ms in sorted(set(power_samples) | set(energy_samples)):
            power = power_samples.get(timestamp_ms, {})
            energy = energy_samples.get(timestamp_ms, {})
            cumulative_sum = _number(energy.get("sum"))
            metered_energy_kwh: float | None = None
            if cumulative_sum is not None:
                if prior_sum is not None:
                    metered_energy_kwh = max(0.0, cumulative_sum - prior_sum)
                prior_sum = cumulative_sum
            mean_power = _number(power.get("mean"))
            peak_power = _number(power.get("max"))
            if mean_power is None and peak_power is None and metered_energy_kwh is None:
                continue
            rows.append(
                {
                    "device_id": source.device_id,
                    "room_id": source.room_id,
                    "hour_start": datetime.fromtimestamp(timestamp_ms / 1000, UTC).replace(tzinfo=None),
                    "sample_count": 1,
                    "avg_power_w": mean_power,
                    "peak_power_w": peak_power,
                    "metered_energy_kwh": metered_energy_kwh,
                }
            )
    return rows


def _context_rows(statistics: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Convert Home Assistant hourly context statistics into database rows."""
    rows: list[dict[str, Any]] = []
    for statistic_id, samples in statistics.items():
        for timestamp_ms, sample in _samples_by_start(samples).items():
            rows.append(
                {
                    "statistic_id": statistic_id,
                    "hour_start": datetime.fromtimestamp(timestamp_ms / 1000, UTC).replace(
                        tzinfo=None
                    ),
                    "mean_value": _number(sample.get("mean")),
                    "min_value": _number(sample.get("min")),
                    "max_value": _number(sample.get("max")),
                    "state_value": _number(sample.get("state")),
                    "sum_value": _number(sample.get("sum")),
                }
            )
    return rows


def _samples_by_start(samples: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Index Home Assistant statistic samples by their millisecond start time."""
    indexed: dict[int, dict[str, Any]] = {}
    for sample in samples:
        start = sample.get("start")
        if isinstance(start, int):
            indexed[start] = sample
    return indexed


def _number(value: Any) -> float | None:
    """Return a finite numeric statistic value, if one was supplied."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric == numeric else None


def _websocket_url(http_url: str) -> str:
    """Convert a Home Assistant HTTP endpoint into its WebSocket endpoint."""
    base = http_url.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base.removeprefix("https://") + "/api/websocket"
    if base.startswith("http://"):
        return "ws://" + base.removeprefix("http://") + "/api/websocket"
    raise ValueError("HA_URL must start with http:// or https://")


def _as_utc(value: datetime) -> datetime:
    """Normalize an input datetime to an aware UTC timestamp."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _batches(values: list[str], size: int) -> Iterable[list[str]]:
    """Yield fixed-size batches without depending on Python-version helpers."""
    for start in range(0, len(values), size):
        yield values[start : start + size]
