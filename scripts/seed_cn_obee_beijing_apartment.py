"""Seed the public CN-OBEE Beijing apartment as an EcoNest household."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from orchestrator.core.database import (
    close_databases,
    init_mysql_database,
    mysql_session_context,
)
from orchestrator.core.simulated_households import CN_OBEE_NAME

_ROOMS = (
    ("Cloakroom", "CN-OBEE: temperature, humidity, pressure, and occupancy."),
    ("Home office", "CN-OBEE: environment, window state, and occupancy."),
    ("Kitchen", "CN-OBEE: environment, window state, occupancy, and appliance-power context."),
    ("Living room", "CN-OBEE: environment, window state, and occupancy."),
    ("Master bedroom", "CN-OBEE: environment, window state, and occupancy."),
    ("Secondary bedroom", "CN-OBEE: environment, window state, and occupancy."),
)


async def seed_cn_obee_beijing_apartment() -> int:
    """Create or update the source-backed CN-OBEE simulated household."""
    async with mysql_session_context() as session:
        existing = await session.execute(
            text("SELECT id FROM households WHERE name = :name LIMIT 1"),
            {"name": CN_OBEE_NAME},
        )
        row = existing.mappings().first()
        if row is None:
            created = await session.execute(
                text("INSERT INTO households (name) VALUES (:name)"),
                {"name": CN_OBEE_NAME},
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
                {
                    "household_id": household_id,
                    "name": name,
                    "description": description,
                },
            )
        await session.commit()
    return household_id


async def run() -> None:
    """Initialize databases, seed CN-OBEE, and close connections."""
    await init_mysql_database()
    try:
        household_id = await seed_cn_obee_beijing_apartment()
        print(f"CN-OBEE Beijing Apartment is ready as household {household_id}.")
    finally:
        await close_databases()


if __name__ == "__main__":
    asyncio.run(run())
