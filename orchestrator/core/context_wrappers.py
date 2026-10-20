"""Shared contract for bounded context providers used by EcoNest reasoning."""

from __future__ import annotations

from typing import Any, Protocol


class ContextWrapper(Protocol):
    """Standard interface future Calendar, weather, and email wrappers follow."""

    name: str

    async def status(self) -> dict[str, Any]:
        """Return non-sensitive availability and freshness information."""

    async def build_context(
        self, intent: str = "", payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return bounded context for one reasoning task."""
