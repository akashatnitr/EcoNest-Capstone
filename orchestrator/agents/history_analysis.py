"""Validated, read-only plan contract for retained-home-data questions."""

from typing import Literal

from pydantic import BaseModel, Field


class HistoryAnalysisPlan(BaseModel):
    """A bounded analysis request selected by the language model."""

    scope: Literal["appliance_cycles", "important_events"]
    operation: Literal["latest", "average_duration", "count", "total_duration", "list"]
    subject: str | None = Field(default=None, max_length=80)
    period_days: int | None = Field(default=None, ge=1, le=3_650)


class RoomComfortQueryPlan(BaseModel):
    """A bounded current-room-state request selected by the language model."""

    room: str = Field(min_length=1, max_length=80)
    metric: Literal["target_temperature", "current_temperature", "humidity", "hvac_mode"]
