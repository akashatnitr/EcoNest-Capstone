"""Persist resident-edited household plans as category-specific preferences."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


HOUSEHOLD_PLAN_PREFERENCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS household_plan_preferences (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    mode VARCHAR(32) NOT NULL,
    plan_json TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_household_plan_preference_user_mode (user_id, mode),
    INDEX idx_household_plan_preference_user_mode (user_id, mode)
)
"""


async def ensure_household_plan_preferences_schema(session: AsyncSession) -> None:
    """Create the resident plan-preference store during database startup."""
    await session.execute(text(HOUSEHOLD_PLAN_PREFERENCES_SCHEMA))
    await session.commit()


async def save_household_plan_preference(
    session: AsyncSession, *, user_id: int, mode: str, plan: dict[str, Any]
) -> dict[str, Any]:
    """Save one resident-authored plan preference for an event category."""
    serialized_plan = json.dumps(plan, separators=(",", ":"), ensure_ascii=False)
    await session.execute(
        text(
            "INSERT INTO household_plan_preferences (user_id, mode, plan_json) "
            "VALUES (:user_id, :mode, :plan_json) "
            "ON DUPLICATE KEY UPDATE plan_json = VALUES(plan_json), "
            "updated_at = CURRENT_TIMESTAMP"
        ),
        {"user_id": user_id, "mode": mode, "plan_json": serialized_plan},
    )
    await session.commit()
    return {"saved": True, "mode": mode}


async def load_household_plan_preferences(
    session: AsyncSession, *, user_id: int
) -> dict[str, dict[str, Any]]:
    """Return the latest valid resident preference for each plan category."""
    result = await session.execute(
        text(
            "SELECT mode, plan_json FROM household_plan_preferences "
            "WHERE user_id = :user_id"
        ),
        {"user_id": user_id},
    )
    preferences: dict[str, dict[str, Any]] = {}
    for mode, serialized_plan in result.all():
        if not isinstance(mode, str) or not isinstance(serialized_plan, str):
            continue
        try:
            plan = json.loads(serialized_plan)
        except json.JSONDecodeError:
            continue
        if isinstance(plan, dict):
            preferences[mode] = plan
    return preferences
