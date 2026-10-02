"""Run a safe one-time reconstruction of home events from raw retained readings."""

from __future__ import annotations

import argparse
import asyncio

from orchestrator.config import get_settings
from orchestrator.core.database import close_databases, init_databases
from orchestrator.core.home_events_backfill import backfill_home_events


async def main() -> None:
    """Initialize database access, reconstruct events, and print a compact summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    await init_databases()
    try:
        summary = await backfill_home_events(
            get_settings(), batch_size=arguments.batch_size, dry_run=arguments.dry_run
        )
        print(summary.model_dump_json(indent=2))
    finally:
        await close_databases()


if __name__ == "__main__":
    asyncio.run(main())
