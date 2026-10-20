"""Merge the legacy generic Connected Home row into the live HA household.

Run with ``--apply`` only after reviewing the dry-run output.  It transfers
foreign-key references, renames the retained live household to Connected Home,
and removes the duplicate generic row in one transaction.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from orchestrator.core.database import close_databases, init_databases, mysql_session_context


async def merge_connected_home(*, apply: bool) -> None:
    """Merge a generic Connected Home record into the Home Assistant record."""
    async with mysql_session_context() as session:
        homes = await session.execute(
            text(
                "SELECT id, name, home_assistant_url FROM households "
                "WHERE name IN ('Connected Home', 'Home Assistant Household') "
                "ORDER BY id"
            )
        )
        rows = homes.mappings().all()
        live = next((row for row in rows if row["home_assistant_url"]), None)
        generic = next(
            (row for row in rows if row["name"] == "Connected Home" and not row["home_assistant_url"]),
            None,
        )
        if live is None:
            raise RuntimeError("No Home Assistant household was found; nothing was changed.")
        if generic is None:
            print(f"No generic duplicate found. Live household is {live['id']}: {live['name']}.")
            return

        live_id, generic_id = int(live["id"]), int(generic["id"])
        references = await session.execute(
            text(
                "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE REFERENCED_TABLE_SCHEMA = DATABASE() "
                "AND REFERENCED_TABLE_NAME = 'households' "
                "ORDER BY TABLE_NAME, COLUMN_NAME"
            )
        )
        targets = [(str(row["TABLE_NAME"]), str(row["COLUMN_NAME"])) for row in references.mappings().all()]
        print(f"Merge household {generic_id} into {live_id}; updating: {', '.join(table for table, _ in targets)}.")
        if not apply:
            print("Dry run only. Re-run with --apply to perform the merge.")
            return

        for table, column in targets:
            await session.execute(
                text(f"UPDATE `{table}` SET `{column}` = :live_id WHERE `{column}` = :generic_id"),
                {"live_id": live_id, "generic_id": generic_id},
            )
        await session.execute(
            text("UPDATE households SET name = 'Connected Home' WHERE id = :id"),
            {"id": live_id},
        )
        await session.execute(text("DELETE FROM households WHERE id = :id"), {"id": generic_id})
        await session.commit()
        print(f"Merged successfully. Connected Home is now household {live_id}.")


def main() -> None:
    """Run the merge utility."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the merge")
    args = parser.parse_args()

    async def run() -> None:
        await init_databases()
        try:
            await merge_connected_home(apply=args.apply)
        finally:
            await close_databases()

    asyncio.run(run())


if __name__ == "__main__":
    main()
