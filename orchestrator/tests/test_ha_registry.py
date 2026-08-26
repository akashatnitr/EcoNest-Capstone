"""Tests for Home Assistant registry resolution and ingestion cache behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from orchestrator.config import Settings
from orchestrator.core import ha_ingest
from orchestrator.core.ha_ingest import HomeAssistantIngestor
from orchestrator.core.ha_registry import RegistryContext


def _registry() -> RegistryContext:
    return RegistryContext(
        areas=[
            {"area_id": "kitchen", "name": "Kitchen"},
            {"area_id": "garage", "name": "Garage"},
        ],
        devices=[{"id": "device-kitchen", "area_id": "kitchen"}],
        entities=[
            {"entity_id": "sensor.direct", "area_id": "garage"},
            {"entity_id": "sensor.parent", "device_id": "device-kitchen"},
            {"entity_id": "sensor.unassigned"},
        ],
        source="test",
    )


def test_registry_prefers_entity_area_then_device_area_then_fallback() -> None:
    registry = _registry()

    assert registry.room_for_entity("sensor.direct") == {
        "area_id": "garage",
        "name": "Garage",
    }
    assert registry.room_for_entity("sensor.parent") == {
        "area_id": "kitchen",
        "name": "Kitchen",
    }
    assert registry.room_for_entity("sensor.unassigned") == {
        "area_id": "home_assistant",
        "name": "Home Assistant",
    }


def test_registry_area_rename_uses_current_name() -> None:
    registry = RegistryContext(
        areas=[{"area_id": "kitchen", "name": "New Kitchen Name"}],
        devices=[],
        entities=[{"entity_id": "sensor.kitchen", "area_id": "kitchen"}],
    )

    assert registry.room_for_entity("sensor.kitchen")["name"] == "New Kitchen Name"


@pytest.mark.asyncio
async def test_registry_outage_keeps_last_successful_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingestor = HomeAssistantIngestor(Settings(HA_REGISTRY_REFRESH_SECONDS=1))
    cached = _registry()
    ingestor._registry = cached
    ingestor._registry_refreshed_at = datetime(2000, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(
        ha_ingest,
        "fetch_registry_context",
        AsyncMock(side_effect=RuntimeError("HA unavailable")),
    )

    registry, available = await ingestor._get_registry()

    assert registry is cached
    assert available is True
    assert "HA unavailable" in (ingestor.registry_last_error or "")


@pytest.mark.asyncio
async def test_first_registry_outage_does_not_claim_mapping_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingestor = HomeAssistantIngestor(Settings())
    monkeypatch.setattr(
        ha_ingest,
        "fetch_registry_context",
        AsyncMock(side_effect=RuntimeError("HA unavailable")),
    )

    registry, available = await ingestor._get_registry()

    assert registry is None
    assert available is False
