"""Run beside a licensed Home I/O desktop install on Windows.

This process keeps EngineIO.dll local to the simulator machine and exposes a
minimal authenticated API to EcoNest. Set ``HOME_IO_BRIDGE_MAP=standard`` to
use EcoNest's verified catalog for Home I/O's built-in teaching house. Custom
installations must supply their own explicit JSON allow-list.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import AsyncIterator, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class IOPoint(BaseModel):
    """One allow-listed Home I/O bit or float input, output, or memory point."""

    address: int = Field(ge=0)
    memory_type: str = Field(pattern="^(Input|Output|Memory)$")
    writable: bool = False
    momentary: bool = False
    data_type: Literal["Bit", "Float"] = "Bit"


class DeviceStateRequest(BaseModel):
    """A bounded command for one configured output or external-mode input."""

    value: bool | float


class HomeIOEngine:
    """Lazy pythonnet wrapper so this module can be inspected off Windows."""

    def __init__(self, sdk_path: str, points: dict[str, IOPoint]) -> None:
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

    def read(self) -> dict[str, bool | float]:
        """Synchronize and read all configured bit and float values."""
        with self._lock:
            self._memory_map.Update()
            return {
                name: self._read_point(point)
                for name, point in self._points.items()
            }

    def _read_point(self, point: IOPoint) -> bool | float:
        memory_type = getattr(self._memory_type, point.memory_type)
        if point.data_type == "Float":
            return float(self._memory_map.GetFloat(point.address, memory_type).Value)
        return bool(self._memory_map.GetBit(point.address, memory_type).Value)

    def write(self, name: str, value: bool | float) -> bool | float:
        """Write one already allow-listed bit output or float set point."""
        point = self._points[name]
        with self._lock:
            memory_type = getattr(self._memory_type, point.memory_type)
            if point.data_type == "Float":
                target = self._memory_map.GetFloat(point.address, memory_type)
                target.Value = float(value)
            else:
                target = self._memory_map.GetBit(point.address, memory_type)
                target.Value = bool(value)
            self._memory_map.Update()
            return self._read_point(point)

    def pulse(self, name: str, seconds: float | None = None) -> None:
        """Send a bounded command to a momentary Home I/O output.
        """
        point = self._points[name]
        duration = seconds if seconds is not None else 0.45
        with self._lock:
            bit = self._memory_map.GetBit(
                point.address, getattr(self._memory_type, point.memory_type)
            )
            bit.Value = True
            self._memory_map.Update()
            time.sleep(duration)
            bit.Value = False
            self._memory_map.Update()

    def drive_shade(self, name: str, timeout_seconds: float = 15.0) -> dict[str, bool | float]:
        """Drive an allow-listed roller shade until its openness reaches an endpoint."""
        if name.endswith("_open"):
            feedback_name = f"{name[:-5]}_openness"
            target_openness = 9.8
            opening = True
        elif name.endswith("_close"):
            feedback_name = f"{name[:-6]}_openness"
            target_openness = 0.2
            opening = False
        else:
            raise ValueError("Only roller-shade open and close controls can be driven")

        point = self._points[name]
        feedback = self._points.get(feedback_name)
        if feedback is None or feedback.data_type != "Float":
            raise KeyError(f"Missing openness feedback point: {feedback_name}")

        with self._lock:
            bit = self._memory_map.GetBit(
                point.address, getattr(self._memory_type, point.memory_type)
            )
            deadline = time.monotonic() + timeout_seconds
            openness = float(self._read_point(feedback))
            bit.Value = True
            self._memory_map.Update()
            try:
                while (
                    (openness < target_openness if opening else openness > target_openness)
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.15)
                    self._memory_map.Update()
                    openness = float(self._read_point(feedback))
            finally:
                bit.Value = False
                self._memory_map.Update()
                openness = float(self._read_point(feedback))

        confirmed = openness >= target_openness if opening else openness <= target_openness
        return {"confirmed": confirmed, "openness": openness}


class HomeIOWindowCapture:
    """Capture the visible Home I/O window without exposing the desktop."""

    def __init__(self, window_title: str) -> None:
        self._window_title = window_title

    def frame(self) -> bytes:
        """Return a JPEG screenshot of the matching, non-minimized window."""
        try:
            import win32gui  # type: ignore[import-not-found]
            import win32ui  # type: ignore[import-not-found]
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError as error:  # pragma: no cover - requires Windows extras
            raise RuntimeError("Install pillow and pywin32 to enable Home I/O preview") from error

        matches: list[int] = []

        def collect(handle: int, _: object) -> bool:
            title = win32gui.GetWindowText(handle)
            if win32gui.IsWindowVisible(handle) and title.startswith(self._window_title):
                matches.append(handle)
            return True

        win32gui.EnumWindows(collect, None)
        if not matches:
            raise RuntimeError(f"Home I/O window '{self._window_title}' was not found")
        handle = matches[0]
        if win32gui.IsIconic(handle):
            raise RuntimeError("Home I/O is minimized; restore its window to show a preview")
        left, top, right, bottom = win32gui.GetClientRect(handle)
        width, height = right - left, bottom - top
        if width < 1 or height < 1:
            raise RuntimeError("Home I/O window has no visible client area")
        window_dc = win32gui.GetWindowDC(handle)
        source_dc = win32ui.CreateDCFromHandle(window_dc)
        memory_dc = source_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        try:
            bitmap.CreateCompatibleBitmap(source_dc, width, height)
            memory_dc.SelectObject(bitmap)
            # Some pywin32 builds omit ``win32gui.PrintWindow``. Call the
            # native User32 API directly so the target window is captured,
            # rather than the desktop behind/overlapping it.
            print_window = ctypes.WinDLL("user32", use_last_error=True).PrintWindow
            print_window.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
            print_window.restype = ctypes.c_bool
            captured = print_window(handle, memory_dc.GetSafeHdc(), 3)
            if captured != 1:
                raise RuntimeError("Windows could not capture the Home I/O window")
            image = Image.frombuffer(
                "RGB", (width, height), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1
            )
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            memory_dc.DeleteDC()
            source_dc.DeleteDC()
            win32gui.ReleaseDC(handle, window_dc)
        image.thumbnail((1280, 720))
        encoded = BytesIO()
        image.save(encoded, format="JPEG", quality=78, optimize=True)
        return encoded.getvalue()


def _load_points() -> dict[str, IOPoint]:
    """Load the explicit address allow-list supplied by the bridge operator."""
    raw_map = os.environ.get("HOME_IO_BRIDGE_MAP", "{}")
    if raw_map.strip().lower() == "standard":
        catalog = Path(__file__).with_name("home_io_standard_map.json")
        try:
            raw_map = catalog.read_text(encoding="utf-8")
        except OSError as error:
            raise RuntimeError(
                "HOME_IO_BRIDGE_MAP=standard requires home_io_standard_map.json beside the bridge"
            ) from error
    try:
        return {name: IOPoint.model_validate(value) for name, value in json.loads(raw_map).items()}
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
    app.state.capture = HomeIOWindowCapture(os.environ.get("HOME_IO_WINDOW_TITLE", "Home I/O"))
    logger.info("Home I/O bridge started with %d allow-listed I/O points", len(app.state.engine._points))
    yield


app = FastAPI(title="EcoNest Home I/O Bridge", lifespan=_lifespan)


@app.get("/health")
async def health(_: None = Depends(_require_token)) -> dict[str, bool]:
    """Return a protected readiness result without exposing simulator state."""
    return {"connected": True}


@app.get("/state")
async def state(_: None = Depends(_require_token)) -> dict[str, dict[str, bool | float]]:
    """Read the explicit Home I/O I/O allow-list."""
    return {"points": app.state.engine.read()}


@app.get("/frame")
async def frame(_: None = Depends(_require_token)) -> Response:
    """Return a protected current frame of the Home I/O application window."""
    try:
        image = app.state.capture.frame()
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    return Response(
        content=image,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.post("/devices/{device_id}/state")
async def set_state(
    device_id: str, payload: DeviceStateRequest, _: None = Depends(_require_token)
) -> dict[str, bool | float | str]:
    """Write an explicit allow-listed Home I/O output or thermostat set point."""
    point = app.state.engine._points.get(device_id)
    if point is None or not point.writable:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device is not writable")
    if point.data_type == "Bit" and not isinstance(payload.value, bool):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Bit device needs a boolean value")
    if point.data_type == "Float" and (isinstance(payload.value, bool) or not isinstance(payload.value, (int, float))):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Float device needs a numeric value")
    applied = app.state.engine.write(device_id, payload.value)
    return {"device_id": device_id, "value": applied}


@app.post("/devices/{device_id}/pulse")
async def pulse_state(
    device_id: str, _: None = Depends(_require_token)
) -> dict[str, bool | str]:
    """Trigger a safe short pulse for a configured motor/security output."""
    point = app.state.engine._points.get(device_id)
    if point is None or not point.writable or not point.momentary:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device is not pulse-writable")
    app.state.engine.pulse(device_id)
    return {"device_id": device_id, "accepted": True}


@app.post("/devices/{device_id}/drive")
async def drive_shade(
    device_id: str, _: None = Depends(_require_token)
) -> dict[str, bool | float | str]:
    """Drive one mapped roller shade and verify its final openness sensor value."""
    point = app.state.engine._points.get(device_id)
    if point is None or not point.writable or not point.momentary:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device is not drive-writable")
    try:
        result = app.state.engine.drive_shade(device_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return {"device_id": device_id, **result}
