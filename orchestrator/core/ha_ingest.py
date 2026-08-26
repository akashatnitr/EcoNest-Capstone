"""Periodic Home Assistant sensor-state ingestion into MySQL."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text

from orchestrator.config import Settings
from orchestrator.core.database import mysql_session_context
from orchestrator.core.ha_registry import RegistryContext, fetch_registry_context

logger = logging.getLogger(__name__)


class HomeAssistantIngestor:
    """Poll Home Assistant and persist sensor state snapshots in MySQL."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.interval_seconds = max(15, settings.HA_INGEST_INTERVAL_SECONDS)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self.last_run_at: str | None = None
        self.last_success_at: str | None = None
        self.last_error: str | None = None
        self.run_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.readings_inserted = 0
        self._last_seen: dict[str, str] = {}
        self._registry: RegistryContext | None = None
        self._registry_refreshed_at: datetime | None = None
        self.registry_last_error: str | None = None
        self.unmapped_entity_count = 0

    def start(self) -> None:
        """Start the background ingestion loop."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="econest-ha-ingestor")
        logger.info("Home Assistant ingestion started")

    async def stop(self) -> None:
        """Stop the background ingestion loop."""
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("Home Assistant ingestion stopped")

    def status(self) -> dict[str, Any]:
        """Return ingestion runtime status without exposing credentials."""
        return {
            "enabled": self.settings.HA_INGEST_ENABLED,
            "running": self._task is not None and not self._task.done(),
            "interval_seconds": self.interval_seconds,
            "last_run_at": self.last_run_at,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "run_count": self.run_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "readings_inserted": self.readings_inserted,
            "registry_refreshed_at": (
                self._registry_refreshed_at.isoformat()
                if self._registry_refreshed_at is not None
                else None
            ),
            "registry_source": self._registry.source if self._registry else None,
            "registry_last_error": self.registry_last_error,
            "unmapped_entity_count": self.unmapped_entity_count,
        }

    async def run_once(self) -> dict[str, int]:
        """Fetch and store one snapshot of HA sensor and binary-sensor states."""
        self.run_count += 1
        self.last_run_at = _utc_now()
        try:
            states = await self._fetch_states()
            result = await self._store_states(states)
        except Exception as exc:
            self.failure_count += 1
            self.last_error = f"{exc.__class__.__name__}: {exc}"
            logger.warning("Home Assistant ingestion failed: %s", exc)
            raise

        self.success_count += 1
        self.last_success_at = _utc_now()
        self.last_error = None
        self.readings_inserted += result["readings_inserted"]
        logger.info(
            "Stored %s Home Assistant sensor readings",
            result["readings_inserted"],
        )
        return result

    async def _run(self) -> None:
        await self._run_safely()
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.interval_seconds
                )
            except TimeoutError:
                await self._run_safely()

    async def _run_safely(self) -> None:
        try:
            await self.run_once()
        except Exception:
            # run_once records and logs the failure; keep the collector alive.
            pass

    async def _fetch_states(self) -> list[dict[str, Any]]:
        if not self.settings.HA_TOKEN:
            raise RuntimeError("HA_TOKEN is required when HA ingestion is enabled")
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{self.settings.HA_URL.rstrip('/')}/api/states",
                headers={"Authorization": f"Bearer {self.settings.HA_TOKEN}"},
            )
            response.raise_for_status()
            states = response.json()
        if not isinstance(states, list):
            raise RuntimeError("Home Assistant /api/states did not return a list")
        return [state for state in states if isinstance(state, dict)]

    async def _store_states(self, states: list[dict[str, Any]]) -> dict[str, int]:
        selected = [state for state in states if _is_sensor_state(state)]
        changed = [state for state in selected if self._is_changed(state)]
        registry, registry_available = await self._get_registry()
        async with mysql_session_context() as session:
            try:
                household_id = await _ensure_household(session, self.settings.HA_URL)
                room_ids = await _sync_registry_rooms(
                    session, household_id, registry if registry_available else None
                )
                fallback_room_id = room_ids["home_assistant"]
                if registry_available and registry is not None:
                    await _sync_registry_entities(session, household_id, registry)
                    await _reconcile_device_rooms(session, registry, room_ids)
                inserted = 0
                for state in changed:
                    entity_id = str(state["entity_id"])
                    room_id = fallback_room_id
                    if registry_available and registry is not None:
                        room = registry.room_for_entity(entity_id)
                        room_id = room_ids.get(room["area_id"], fallback_room_id)
                        if room["area_id"] == "home_assistant":
                            self.unmapped_entity_count += 1
                    device_id, room_id = await _ensure_device(
                        session, room_id, state, preserve_existing=not registry_available
                    )
                    payload = {
                        "state": state.get("state"),
                        "attributes": state.get("attributes") or {},
                        "last_changed": state.get("last_changed"),
                        "last_updated": state.get("last_updated"),
                        "source": "home_assistant",
                    }
                    await session.execute(
                        text(
                            "INSERT INTO sensor_readings "
                            "(device_id, room_id, data) "
                            "VALUES (:device_id, :room_id, :data)"
                        ),
                        {
                            "device_id": device_id,
                            "room_id": room_id,
                            "data": json.dumps(payload, default=str),
                        },
                    )
                    inserted += 1
                await session.commit()
                for state in changed:
                    self._last_seen[str(state["entity_id"])] = _state_revision(state)
            except Exception:
                await session.rollback()
                raise
        return {
            "states_received": len(states),
            "sensor_states": len(selected),
            "unchanged_skipped": len(selected) - len(changed),
            "readings_inserted": inserted,
        }

    async def _get_registry(self) -> tuple[RegistryContext | None, bool]:
        """Refresh registry metadata periodically while retaining a good cache."""
        now = datetime.now(UTC)
        refresh_due = self._registry_refreshed_at is None or (
            now - self._registry_refreshed_at
        ).total_seconds() >= self.settings.HA_REGISTRY_REFRESH_SECONDS
        if not refresh_due and self._registry is not None:
            return self._registry, True
        try:
            registry = await fetch_registry_context()
            if not registry.has_data:
                raise RuntimeError("Home Assistant registry returned no records")
        except Exception as exc:
            self.registry_last_error = f"{exc.__class__.__name__}: {exc}"
            logger.warning("Keeping existing room assignments: %s", exc)
            return self._registry, self._registry is not None
        self._registry = registry
        self._registry_refreshed_at = now
        self.registry_last_error = None
        self.unmapped_entity_count = sum(
            1
            for entity_id in registry.entities_by_id
            if registry.room_for_entity(entity_id)["area_id"] == "home_assistant"
        )
        return registry, True

    def _is_changed(self, state: dict[str, Any]) -> bool:
        entity_id = str(state["entity_id"])
        return self._last_seen.get(entity_id) != _state_revision(state)


async def _ensure_household(session: Any, ha_url: str) -> int:
    result = await session.execute(
        text("SELECT id FROM households WHERE home_assistant_url = :url LIMIT 1"),
        {"url": ha_url},
    )
    row = result.mappings().first()
    if row is not None:
        return int(row["id"])
    result = await session.execute(
        text(
            "INSERT INTO households (name, home_assistant_url) "
            "VALUES ('Home Assistant Household', :url)"
        ),
        {"url": ha_url},
    )
    return int(result.lastrowid)


async def _ensure_room(
    session: Any, household_id: int, area_id: str, name: str
) -> int:
    result = await session.execute(
        text(
            "SELECT id FROM rooms WHERE household_id = :household_id "
            "AND ha_area_id = :area_id LIMIT 1"
        ),
        {"household_id": household_id, "area_id": area_id},
    )
    row = result.mappings().first()
    if row is not None:
        await session.execute(
            text(
                "UPDATE rooms SET name = :name, description = :description "
                "WHERE id = :room_id"
            ),
            {
                "room_id": int(row["id"]),
                "name": name,
                "description": (
                    "HA registry-backed room"
                    if area_id != "home_assistant"
                    else "Entities awaiting room registry mapping"
                ),
            },
        )
        return int(row["id"])
    result = await session.execute(
        text(
            "INSERT INTO rooms (household_id, name, description, ha_area_id) "
            "VALUES (:household_id, :name, :description, :area_id) "
            "ON DUPLICATE KEY UPDATE name = VALUES(name), "
            "description = VALUES(description)"
        ),
        {
            "household_id": household_id,
            "name": name,
            "description": "HA registry-backed room" if area_id != "home_assistant" else "Entities awaiting room registry mapping",
            "area_id": area_id,
        },
    )
    if result.lastrowid:
        return int(result.lastrowid)
    result = await session.execute(
        text("SELECT id FROM rooms WHERE household_id = :household_id AND ha_area_id = :area_id"),
        {"household_id": household_id, "area_id": area_id},
    )
    return int(result.mappings().one()["id"])


async def _ensure_device(
    session: Any, room_id: int, state: dict[str, Any], preserve_existing: bool = False
) -> tuple[int, int]:
    entity_id = str(state["entity_id"])
    attributes = state.get("attributes") or {}
    friendly_name = str(attributes.get("friendly_name") or entity_id)
    name = f"{friendly_name} [{entity_id}]"[:100]
    device_type = "motion_sensor" if entity_id.startswith("binary_sensor.") else "sensor"
    existing_room_id: int | None = None
    if preserve_existing:
        existing = await session.execute(
            text("SELECT room_id FROM devices WHERE ha_entity_id = :entity_id LIMIT 1"),
            {"entity_id": entity_id},
        )
        row = existing.mappings().first()
        existing_room_id = int(row["room_id"]) if row and row["room_id"] else None
    effective_room_id = existing_room_id or room_id
    await session.execute(
        text(
            "INSERT INTO devices "
            "(name, device_type, room_id, ha_entity_id, ha_platform, is_active) "
            "VALUES (:name, :device_type, :room_id, :entity_id, 'home_assistant', TRUE) "
            "ON DUPLICATE KEY UPDATE room_id = VALUES(room_id), "
            "device_type = VALUES(device_type), is_active = TRUE"
        ),
        {
            "name": name,
            "device_type": device_type,
            "room_id": effective_room_id,
            "entity_id": entity_id,
        },
    )
    result = await session.execute(
        text("SELECT id FROM devices WHERE ha_entity_id = :entity_id LIMIT 1"),
        {"entity_id": entity_id},
    )
    row = result.mappings().first()
    if row is None:
        raise RuntimeError(f"Unable to resolve stored device {entity_id}")
    return int(row["id"]), effective_room_id


async def _sync_registry_rooms(
    session: Any, household_id: int, registry: RegistryContext | None
) -> dict[str, int]:
    """Upsert fallback and current HA areas, returning their MySQL ids."""
    room_ids = {
        "home_assistant": await _ensure_room(
            session, household_id, "home_assistant", "Home Assistant"
        )
    }
    if registry is None:
        return room_ids
    for area_id, area in registry.areas_by_id.items():
        room_ids[area_id] = await _ensure_room(
            session, household_id, area_id, str(area.get("name") or area_id.replace("_", " ").title())
        )
    return room_ids


async def _sync_registry_entities(
    session: Any, household_id: int, registry: RegistryContext
) -> None:
    """Keep a traceable MySQL copy of HA's current entity registry."""
    for entity_id, entity in registry.entities_by_id.items():
        resolved = registry.room_for_entity(entity_id)
        device = registry.device_for_entity(entity_id)["device"]
        attributes = {
            "entity": entity,
            "device": device,
            "registry_source": registry.source,
        }
        await session.execute(
            text(
                "INSERT INTO home_assistant_entities "
                "(household_id, ha_entity_id, ha_device_id, ha_area_id, domain, platform, "
                "friendly_name, original_name, entity_category, disabled_by, metadata) "
                "VALUES (:household_id, :entity_id, :device_id, :area_id, :domain, :platform, "
                ":friendly_name, :original_name, :entity_category, :disabled_by, :metadata) "
                "ON DUPLICATE KEY UPDATE ha_device_id = VALUES(ha_device_id), "
                "ha_area_id = VALUES(ha_area_id), platform = VALUES(platform), "
                "friendly_name = VALUES(friendly_name), original_name = VALUES(original_name), "
                "entity_category = VALUES(entity_category), disabled_by = VALUES(disabled_by), "
                "metadata = VALUES(metadata)"
            ),
            {
                "household_id": household_id,
                "entity_id": entity_id,
                "device_id": entity.get("device_id"),
                "area_id": resolved["area_id"] if resolved["area_id"] != "home_assistant" else None,
                "domain": entity_id.split(".", 1)[0],
                "platform": entity.get("platform"),
                "friendly_name": entity.get("name") or entity.get("original_name"),
                "original_name": entity.get("original_name"),
                "entity_category": entity.get("entity_category"),
                "disabled_by": entity.get("disabled_by"),
                "metadata": json.dumps(attributes, default=str),
            },
        )


async def _reconcile_device_rooms(
    session: Any, registry: RegistryContext, room_ids: dict[str, int]
) -> None:
    """Reflect current registry placement for already-known HA devices."""
    fallback_room_id = room_ids["home_assistant"]
    for entity_id in registry.entities_by_id:
        area_id = registry.room_for_entity(entity_id)["area_id"]
        await session.execute(
            text(
                "UPDATE devices SET room_id = :room_id "
                "WHERE ha_entity_id = :entity_id"
            ),
            {"room_id": room_ids.get(area_id, fallback_room_id), "entity_id": entity_id},
        )


def _is_sensor_state(state: dict[str, Any]) -> bool:
    entity_id = str(state.get("entity_id") or "")
    return entity_id.startswith(("sensor.", "binary_sensor.")) and state.get(
        "state"
    ) not in {None, "unknown", "unavailable"}


def _state_revision(state: dict[str, Any]) -> str:
    return str(
        state.get("last_updated") or state.get("last_changed") or state.get("state")
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
