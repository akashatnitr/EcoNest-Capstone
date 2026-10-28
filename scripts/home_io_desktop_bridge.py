"""Run beside a licensed Home I/O desktop install on Windows.

This process keeps EngineIO.dll local to the simulator machine and exposes a
minimal authenticated API to EcoNest.  It supports bit I/O in phase 1; use the
SDK's Engine I/O Explorer to populate HOME_IO_BRIDGE_MAP with the installed
house's actual addresses.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BitPoint(BaseModel):
    """One allow-listed Home I/O bit input or output."""

    address: int = Field(ge=0)
    memory_type: str = Field(pattern="^(Input|Output|Memory)$")
    writable: bool = False


class DeviceStateRequest(BaseModel):
    """A bounded command for a configured bit output."""

    value: bool


class HomeIOEngine:
    """Lazy pythonnet wrapper so this module can be inspected off Windows."""

    def __init__(self, sdk_path: str, points: dict[str, BitPoint]) -> None:
        try:
            # Home I/O and Connect I/O are .NET Framework applications.  Set
            # this before importing pythonnet so it does not select CoreCLR.
            os.environ.setdefault("PYTHONNET_RUNTIME", "netfx")
            import clr  # type: ignore[import-not-found]

            clr.AddReference(str(Path(sdk_path) / "EngineIO.dll"))
            from EngineIO import MemoryMap, MemoryType  # type: ignore[import-not-found]
        except Exception as error:  # pragma: no cover - requires Home I/O SDK
            raise RuntimeError("Could not load Home I/O EngineIO.dll") from error
        self._memory_map = MemoryMap.Instance
        self._memory_type = MemoryType
        self._points = points
        self._lock = threading.Lock()

    def read(self) -> dict[str, bool]:
        """Synchronize and read all configured bit values."""
        with self._lock:
            self._memory_map.Update()
            return {
                name: bool(
                    self._memory_map.GetBit(point.address, getattr(self._memory_type, point.memory_type)).Value
                )
                for name, point in self._points.items()
            }

    def write(self, name: str, value: bool) -> bool:
        """Write one already allow-listed output bit and synchronize it."""
        point = self._points[name]
        with self._lock:
            bit = self._memory_map.GetBit(
                point.address, getattr(self._memory_type, point.memory_type)
            )
            bit.Value = value
            self._memory_map.Update()
            return bool(bit.Value)


def _load_points() -> dict[str, BitPoint]:
    """Load the explicit address allow-list supplied by the bridge operator."""
    raw_map = os.environ.get("HOME_IO_BRIDGE_MAP", "{}")
    try:
        return {name: BitPoint.model_validate(value) for name, value in json.loads(raw_map).items()}
    except (json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("HOME_IO_BRIDGE_MAP must be a JSON object of bit points") from error


def _require_token(request: Request) -> None:
    """Reject all calls unless the configured bridge token matches."""
    expected = os.environ.get("HOME_IO_BRIDGE_TOKEN", "")
    supplied = request.headers.get("X-EcoNest-Bridge-Token", "")
    if not expected or supplied != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized bridge request")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load the proprietary SDK only in the process beside Home I/O."""
    sdk_path = os.environ.get("HOME_IO_SDK_PATH", "")
    if not sdk_path:
        raise RuntimeError("Set HOME_IO_SDK_PATH to the directory containing EngineIO.dll")
    app.state.engine = HomeIOEngine(sdk_path, _load_points())
    logger.info("Home I/O bridge started with %d allow-listed I/O points", len(app.state.engine._points))
    yield


app = FastAPI(title="EcoNest Home I/O Bridge", lifespan=_lifespan)


@app.get("/health")
async def health(_: None = Depends(_require_token)) -> dict[str, bool]:
    """Return a protected readiness result without exposing simulator state."""
    return {"connected": True}


@app.get("/state")
async def state(_: None = Depends(_require_token)) -> dict[str, dict[str, bool]]:
    """Read the explicit Home I/O I/O allow-list."""
    return {"points": app.state.engine.read()}


@app.post("/devices/{device_id}/state")
async def set_state(
    device_id: str, payload: DeviceStateRequest, _: None = Depends(_require_token)
) -> dict[str, bool | str]:
    """Write an allow-listed Home I/O output bit only."""
    point = app.state.engine._points.get(device_id)
    if point is None or not point.writable or point.memory_type != "Output":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device is not writable")
    applied = app.state.engine.write(device_id, payload.value)
    return {"device_id": device_id, "value": applied}
