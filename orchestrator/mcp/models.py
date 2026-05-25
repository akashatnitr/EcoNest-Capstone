"""Shared MCP semantic capability models."""

from typing import Any

from pydantic import BaseModel, Field


class ToolExecutionResult(BaseModel):
    success: bool = True
    capability: str
    result: Any = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
