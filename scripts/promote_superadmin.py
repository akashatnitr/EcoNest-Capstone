"""Promote an existing EcoNest account to the administrator role.

Run this only from a trusted development/admin environment:

    poetry run python scripts/promote_superadmin.py --email admin@example.com
"""

from __future__ import annotations

import argparse
import asyncio
import getpass

from sqlalchemy import text

from orchestrator.core.database import (
    close_databases,
    init_databases,
    mysql_session_context,
)
from orchestrator.core.permissions import Role
from orchestrator.core.security import hash_password


def parse_args() -> argparse.Namespace:
    """Read the existing account email to promote."""
    parser = argparse.ArgumentParser(description="Promote an existing EcoNest user.")
    parser.add_argument("--email", required=True, help="Existing user email address.")
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Securely prompt for a replacement password without echoing it.",
    )
    return parser.parse_args()


async def promote(email: str, password: str | None = None) -> None:
    """Grant full cross-household administrator access to one existing user."""
    await init_databases()
    try:
        async with mysql_session_context() as session:
            result = await session.execute(
                text("SELECT id FROM users WHERE email = :email"), {"email": email}
            )
            if result.scalar_one_or_none() is None:
                raise RuntimeError(
                    "No EcoNest account exists for that email. Create the homeowner account first."
                )
            updates = "role = :role, household_id = NULL"
            values: dict[str, str] = {"role": Role.SUPERADMIN.value, "email": email}
            if password is not None:
                updates += ", hashed_password = :hashed_password"
                values["hashed_password"] = hash_password(password)
            await session.execute(
                text(f"UPDATE users SET {updates} WHERE email = :email"), values
            )
            await session.commit()
    finally:
        await close_databases()


def main() -> None:
    """Run the administrator promotion command."""
    args = parse_args()
    password: str | None = None
    if args.reset_password:
        password = getpass.getpass("New administrator password: ")
        if not password:
            raise RuntimeError("A replacement password is required.")
        confirmation = getpass.getpass("Confirm administrator password: ")
        if password != confirmation:
            raise RuntimeError("Passwords did not match.")
    asyncio.run(promote(args.email.strip().lower(), password))
    print("EcoNest administrator access granted.")


if __name__ == "__main__":
    main()
