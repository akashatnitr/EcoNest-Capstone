"""Create or rotate an EcoNest service-account token.

Run this from the orchestrator container so it uses the same database and
SECRET_KEY as the running service:

    poetry run python scripts/create_service_account.py --email service@econest.local
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from orchestrator.config import get_settings
from orchestrator.core.database import (
    close_databases,
    init_databases,
    mysql_session_context,
)
from orchestrator.core.permissions import Role
from orchestrator.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or rotate credentials for an EcoNest service account."
    )
    parser.add_argument(
        "--email",
        default="service@econest.local",
        help="Service account email/username.",
    )
    parser.add_argument(
        "--password",
        default="",
        help="Optional login password. A random password is generated when omitted.",
    )
    parser.add_argument(
        "--household-id",
        type=int,
        default=None,
        help="Optional household id to assign to the service account.",
    )
    parser.add_argument(
        "--access-days",
        type=int,
        default=365,
        help="Days before the printed SERVICE_ACCOUNT_TOKEN expires.",
    )
    parser.add_argument(
        "--keep-existing-sessions",
        action="store_true",
        help="Keep existing refresh sessions instead of rotating them.",
    )
    return parser.parse_args()


async def upsert_service_account(args: argparse.Namespace) -> dict[str, str | int]:
    settings = get_settings()
    password = args.password or secrets.token_urlsafe(24)
    access_delta = timedelta(days=args.access_days)
    refresh_delta = timedelta(days=max(args.access_days, settings.REFRESH_TOKEN_EXPIRE_DAYS))

    async with mysql_session_context() as session:
        existing = await session.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": args.email},
        )
        row = existing.mappings().first()

        if row is None:
            result = await session.execute(
                text(
                    """
                    INSERT INTO users (
                        email,
                        hashed_password,
                        role,
                        household_id,
                        is_active
                    )
                    VALUES (
                        :email,
                        :hashed_password,
                        :role,
                        :household_id,
                        TRUE
                    )
                    """
                ),
                {
                    "email": args.email,
                    "hashed_password": hash_password(password),
                    "role": Role.SERVICE_ACCOUNT.value,
                    "household_id": args.household_id,
                },
            )
            user_id = int(result.lastrowid)
            action = "created"
        else:
            user_id = int(row["id"])
            await session.execute(
                text(
                    """
                    UPDATE users
                    SET
                        hashed_password = :hashed_password,
                        role = :role,
                        household_id = COALESCE(:household_id, household_id),
                        is_active = TRUE
                    WHERE id = :id
                    """
                ),
                {
                    "id": user_id,
                    "hashed_password": hash_password(password),
                    "role": Role.SERVICE_ACCOUNT.value,
                    "household_id": args.household_id,
                },
            )
            action = "updated"

        if not args.keep_existing_sessions:
            await session.execute(
                text("DELETE FROM user_sessions WHERE user_id = :user_id"),
                {"user_id": user_id},
            )

        access_token = create_access_token(
            {"sub": str(user_id), "role": Role.SERVICE_ACCOUNT.value},
            expires_delta=access_delta,
        )
        refresh_token = create_refresh_token(
            {"sub": str(user_id)},
            expires_delta=refresh_delta,
        )
        await session.execute(
            text(
                """
                INSERT INTO user_sessions (
                    user_id,
                    refresh_token,
                    expires_at
                )
                VALUES (
                    :user_id,
                    :refresh_token,
                    :expires_at
                )
                """
            ),
            {
                "user_id": user_id,
                "refresh_token": hash_refresh_token(refresh_token),
                "expires_at": datetime.now(UTC) + refresh_delta,
            },
        )
        await session.commit()

    return {
        "action": action,
        "user_id": user_id,
        "email": args.email,
        "password": password,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_days": args.access_days,
    }


async def main() -> None:
    args = parse_args()
    await init_databases()
    try:
        result = await upsert_service_account(args)
    finally:
        await close_databases()

    print(f"Service account {result['action']}: {result['email']} (id={result['user_id']})")
    print(f"Password: {result['password']}")
    print(f"SERVICE_ACCOUNT_TOKEN={result['access_token']}")
    print(f"SERVICE_ACCOUNT_REFRESH_TOKEN={result['refresh_token']}")
    print(f"Token expires in {result['access_days']} day(s).")


if __name__ == "__main__":
    asyncio.run(main())
