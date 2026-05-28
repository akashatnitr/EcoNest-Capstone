"""Bootstrap ArcadeDB and import live Home Assistant data."""

from __future__ import annotations

import asyncio
import json

from orchestrator.core.database import close_databases, init_databases
from orchestrator.graph.ha_importer import bootstrap_home_assistant_graph


async def main() -> None:
    await init_databases()
    try:
        result = await bootstrap_home_assistant_graph()
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        await close_databases()


if __name__ == "__main__":
    asyncio.run(main())
