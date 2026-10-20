"""Create the first safe simulated EcoNest household and room inventory."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from orchestrator.core.database import close_databases, init_databases, mysql_session_context
from orchestrator.core.simulated_households import CITY_APARTMENT_NAME

_ROOMS = (
    ("Bedroom", "Sleeping space with a motion-enabled dimmer and thermostat."),
    ("Bathroom", "Bathroom with a motion sensor."),
    ("Living room", "Shared space with dimmer, thermostat, and TV plug."),
    ("Kitchen", "Kitchen with motion-enabled dimmer and energy meter."),
)


async def seed_city_apartment() -> int:
    """Create or update City Apartment and return its household ID."""
    async with mysql_session_context() as session:
        existing = await session.execute(
            text("SELECT id FROM households WHERE name = :name LIMIT 1"),
            {"name": CITY_APARTMENT_NAME},
        )
        row = existing.mappings().first()
        if row is None:
            created = await session.execute(
                text("INSERT INTO households (name) VALUES (:name)"),
                {"name": CITY_APARTMENT_NAME},
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
    """Initialize connections, seed the scenario, and close connections."""
    await init_databases()
    try:
        household_id = await seed_city_apartment()
        print(f"City Apartment Simulation is ready as household {household_id}.")
    finally:
        await close_databases()


if __name__ == "__main__":
    asyncio.run(run())
