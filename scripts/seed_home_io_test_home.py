"""Seed the optional Home I/O-compatible EcoNest test home."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from orchestrator.core.database import close_databases, init_databases, mysql_session_context
from orchestrator.core.simulated_households import HOME_IO_NAME

_ROOMS = (
    ("Entry", "Home I/O-compatible test zone: entry contact and motion."),
    ("Living room", "Home I/O-compatible test zone: lighting, motion, and thermostat."),
    ("Kitchen", "Home I/O-compatible test zone: lighting, motion, and smoke detection."),
    ("Bedroom", "Home I/O-compatible test zone: motion."),
    ("Bathroom", "Home I/O-compatible test zone: motion."),
    ("Garage", "Home I/O-compatible test zone: garage-door contact."),
)


async def seed_home_io_test_home() -> int:
    """Create or update the isolated Home I/O test-home household."""
    async with mysql_session_context() as session:
        existing = await session.execute(
            text("SELECT id FROM households WHERE name = :name LIMIT 1"),
            {"name": HOME_IO_NAME},
        )
        row = existing.mappings().first()
        if row is None:
            created = await session.execute(
                text("INSERT INTO households (name) VALUES (:name)"),
                {"name": HOME_IO_NAME},
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
    """Initialize databases, seed Home I/O, then close database connections."""
    await init_databases()
    try:
        household_id = await seed_home_io_test_home()
        print(f"Home I/O Interactive Test Home is ready as household {household_id}.")
    finally:
        await close_databases()


if __name__ == "__main__":
    asyncio.run(run())
