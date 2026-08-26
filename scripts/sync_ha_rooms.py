#!/usr/bin/env python3
"""Explicitly reconcile MySQL Home Assistant rooms and historical readings.

Run from the repository root. The default mode is read-only; ``--apply`` is
required to update room ids and should be run with the orchestrator stopped.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.config import get_settings  # noqa: E402
from orchestrator.core.database import (  # noqa: E402
    close_databases,
    init_databases,
    mysql_session_context,
)
from orchestrator.core.ha_ingest import (  # noqa: E402
    _reconcile_device_rooms,
    _sync_registry_entities,
    _sync_registry_rooms,
)
from orchestrator.core.ha_registry import (  # noqa: E402
    RegistryContext,
    fetch_registry_context,
)


async def _counts(session: Any, registry: RegistryContext) -> dict[str, int]:
    result = await session.execute(
        text(
            "SELECT COUNT(*) AS devices, "
            "SUM(room_id IS NULL) AS roomless_devices "
            "FROM devices WHERE ha_entity_id IS NOT NULL"
        )
    )
    devices = result.mappings().one()
    if not registry.entities_by_id:
        return {"ha_devices": int(devices["devices"]), "mapped_devices": 0, "fallback_devices": 0, "affected_readings": 0}
    result = await session.execute(
        text(
            "SELECT d.ha_entity_id, d.room_id, r.ha_area_id, COUNT(sr.id) AS readings, "
            "SUM(sr.room_id <> d.room_id) AS mismatched_readings "
            "FROM devices d LEFT JOIN rooms r ON r.id = d.room_id "
            "LEFT JOIN sensor_readings sr ON sr.device_id = d.id "
            "WHERE d.ha_entity_id IS NOT NULL GROUP BY d.id, d.ha_entity_id, d.room_id, r.ha_area_id"
        )
    )
    rows = result.mappings().all()
    known = set(registry.entities_by_id)
    mapped = sum(
        1
        for row in rows
        if row["ha_entity_id"] in known
        and registry.room_for_entity(str(row["ha_entity_id"]))["area_id"]
        != "home_assistant"
    )
    fallback = sum(
        1
        for row in rows
        if row["ha_entity_id"] in known
        and registry.room_for_entity(str(row["ha_entity_id"]))["area_id"]
        == "home_assistant"
    )
    return {
        "ha_devices": int(devices["devices"]),
        "mapped_devices": mapped,
        "fallback_devices": fallback,
        "affected_readings": sum(
            int(row["mismatched_readings"] or 0)
            for row in rows
            if row["ha_entity_id"] in known
        ),
    }


async def run(apply: bool) -> dict[str, int | str]:
    """Print reconciliation counts and optionally apply the room-id updates."""
    registry = await fetch_registry_context()
    if not registry.has_data:
        raise RuntimeError("HA registry is empty; refusing to map devices to fallback")
    await init_databases()
    try:
        async with mysql_session_context() as session:
            household_id = await _find_household(session)
            before = await _counts(session, registry)
            report: dict[str, int | str] = {
                "mode": "apply" if apply else "dry-run",
                "ha_areas": len(registry.areas_by_id),
                **before,
            }
            if not apply:
                return report
            try:
                room_ids = await _sync_registry_rooms(session, household_id, registry)
                await _sync_registry_entities(session, household_id, registry)
                await _reconcile_device_rooms(session, registry, room_ids)
                update = await session.execute(
                    text(
                        "UPDATE sensor_readings sr JOIN devices d ON d.id = sr.device_id "
                        "SET sr.room_id = d.room_id "
                        "WHERE d.ha_entity_id IS NOT NULL AND d.room_id IS NOT NULL "
                        "AND sr.room_id <> d.room_id"
                    )
                )
                await session.commit()
                report["readings_reassigned"] = int(update.rowcount or 0)
            except Exception:
                await session.rollback()
                raise
            return report
    finally:
        await close_databases()


async def _find_household(session: Any) -> int:
    settings = get_settings()
    result = await session.execute(
        text("SELECT id FROM households WHERE home_assistant_url = :url LIMIT 1"),
        {"url": settings.HA_URL},
    )
    row = result.mappings().first()
    if row is None:
        raise RuntimeError("No Home Assistant household exists; run ingestion first")
    return int(row["id"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply room-id updates")
    args = parser.parse_args()
    if args.apply:
        print("Applying HA room migration. Ensure the orchestrator is stopped.")
    result = asyncio.run(run(args.apply))
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
