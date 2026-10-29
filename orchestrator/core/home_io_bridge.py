"""Status client for an optional, local Home I/O desktop bridge."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from orchestrator.config import get_settings

logger = logging.getLogger(__name__)


async def home_io_bridge_status() -> dict[str, Any]:
    """Report whether the separately deployed Home I/O bridge is reachable.

    The bridge is intentionally opt-in.  A missing bridge is a normal demo
    state, rather than a reason to fall back to any connected-home service.
    """
    settings = get_settings()
    if not settings.HOME_IO_BRIDGE_ENABLED or not settings.HOME_IO_BRIDGE_URL:
        return {
            "configured": False,
            "connected": False,
            "mode": "EcoNest demo mode · bridge not configured",
        }
    try:
        async with httpx.AsyncClient(timeout=settings.HOME_IO_BRIDGE_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{settings.HOME_IO_BRIDGE_URL.rstrip('/')}/health",
                headers={"X-EcoNest-Bridge-Token": settings.HOME_IO_BRIDGE_TOKEN},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("Home I/O bridge health check failed: %s", type(error).__name__)
        return {
            "configured": True,
            "connected": False,
            "mode": "Bridge configured but unavailable",
        }
    return {
        "configured": True,
        "connected": bool(payload.get("connected", True)),
        "mode": "Live Home I/O desktop bridge" if payload.get("connected", True) else "Home I/O desktop bridge unavailable",
    }


async def home_io_bridge_points() -> dict[str, bool] | None:
    """Read allow-listed Home I/O points, or return ``None`` if unavailable."""
    settings = get_settings()
    if not settings.HOME_IO_BRIDGE_ENABLED or not settings.HOME_IO_BRIDGE_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=settings.HOME_IO_BRIDGE_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{settings.HOME_IO_BRIDGE_URL.rstrip('/')}/state",
                headers={"X-EcoNest-Bridge-Token": settings.HOME_IO_BRIDGE_TOKEN},
            )
            response.raise_for_status()
            payload = response.json()
        points = payload.get("points")
        if not isinstance(points, dict):
            return None
        return {str(name): bool(value) for name, value in points.items()}
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("Home I/O bridge state read failed: %s", type(error).__name__)
        return None


async def set_home_io_bridge_point(name: str, value: bool) -> bool | None:
    """Set one allow-listed Home I/O output, or return ``None`` if unavailable."""
    settings = get_settings()
    if not settings.HOME_IO_BRIDGE_ENABLED or not settings.HOME_IO_BRIDGE_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=settings.HOME_IO_BRIDGE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{settings.HOME_IO_BRIDGE_URL.rstrip('/')}/devices/{name}/state",
                headers={"X-EcoNest-Bridge-Token": settings.HOME_IO_BRIDGE_TOKEN},
                json={"value": value},
            )
            response.raise_for_status()
            payload = response.json()
        return bool(payload.get("value"))
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("Home I/O bridge command failed for %s: %s", name, type(error).__name__)
        return None


async def pulse_home_io_bridge_point(name: str) -> bool:
    """Trigger one allow-listed non-latching Home I/O action."""
    settings = get_settings()
    if not settings.HOME_IO_BRIDGE_ENABLED or not settings.HOME_IO_BRIDGE_URL:
        return False
    try:
        async with httpx.AsyncClient(timeout=settings.HOME_IO_BRIDGE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{settings.HOME_IO_BRIDGE_URL.rstrip('/')}/devices/{name}/pulse",
                headers={"X-EcoNest-Bridge-Token": settings.HOME_IO_BRIDGE_TOKEN},
            )
            response.raise_for_status()
            payload = response.json()
        return bool(payload.get("accepted"))
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("Home I/O bridge pulse failed for %s: %s", name, type(error).__name__)
        return False


async def home_io_bridge_frame() -> bytes | None:
    """Fetch one protected Home I/O preview frame without exposing bridge auth."""
    settings = get_settings()
    if not settings.HOME_IO_BRIDGE_ENABLED or not settings.HOME_IO_BRIDGE_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=settings.HOME_IO_BRIDGE_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{settings.HOME_IO_BRIDGE_URL.rstrip('/')}/frame",
                headers={"X-EcoNest-Bridge-Token": settings.HOME_IO_BRIDGE_TOKEN},
            )
            response.raise_for_status()
            if not response.headers.get("content-type", "").startswith("image/jpeg"):
                return None
            return response.content
    except httpx.HTTPError as error:
        logger.warning("Home I/O bridge frame read failed: %s", type(error).__name__)
        return None
