"""Seed a small, safe CASAS Aruba replay household into EcoNest."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from orchestrator.core.database import close_databases, init_databases, mysql_session_context
from orchestrator.core.simulated_households import CASAS_ARUBA_NAME

_ROOMS = (
    ("Bedroom", "CASAS Aruba replay: bed and bedroom motion sensors."),
    ("Bathroom", "CASAS Aruba replay: bathroom transition motion sensor."),
    ("Living room", "CASAS Aruba floor-plan zone."),
    ("Kitchen", "CASAS Aruba floor-plan zone."),
)


async def seed_casas_aruba_replay() -> int:
    """Create or update the dataset-backed replay household."""
    async with mysql_session_context() as session:
        existing = await session.execute(
            text("SELECT id FROM households WHERE name = :name LIMIT 1"),
            {"name": CASAS_ARUBA_NAME},
        )
        row = existing.mappings().first()
        if row is None:
            created = await session.execute(
                text("INSERT INTO households (name) VALUES (:name)"),
                {"name": CASAS_ARUBA_NAME},
            )
            household_id = int(created.lastrowid)
        else:
            household_id = int(row["id"])
        for name, description in _ROOMS:
            await session.execute(
                text(
                    "INSERT INTO rooms (household_id, name, description) "
                    "VALUES (:household_id, :name, :description) "
                    "ON DUPLICATE KEY UPDATE description = VALUES(description)"
                ),
                {"household_id": household_id, "name": name, "description": description},
            )
        await session.commit()
    return household_id


async def run() -> None:
    """Initialize databases, seed the household, and close connections."""
    await init_databases()
    try:
        household_id = await seed_casas_aruba_replay()
        print(f"CASAS Aruba Replay is ready as household {household_id}.")
    finally:
        await close_databases()


if __name__ == "__main__":
    asyncio.run(run())
