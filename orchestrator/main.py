"""FastAPI entrypoint for the EcoNest orchestrator."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, status

from orchestrator.api import auth, demo, devices, graph, mcp, ontology, readings, users
from orchestrator.config import get_settings
from orchestrator.core.autonomy import AutonomousMonitor
from orchestrator.core.database import (
    close_databases,
    healthcheck_arcadedb,
    healthcheck_mysql,
    init_databases,
)
from orchestrator.mcp import server as mcp_server

settings = get_settings()
autonomous_monitor: AutonomousMonitor | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage database connections across the application lifespan."""
    global autonomous_monitor
    await init_databases()
    if settings.AUTONOMY_MONITOR_ENABLED:
        autonomous_monitor = AutonomousMonitor(
            lambda: demo.collect_periodic_feedback(trigger="background_monitor"),
            interval_seconds=settings.AUTONOMY_MONITOR_INTERVAL_SECONDS,
            run_on_startup=settings.AUTONOMY_MONITOR_RUN_ON_STARTUP,
            action_recommender=demo.recommend_autonomous_action,
            action_executor=demo.execute_autonomous_action,
            action_confidence_threshold=settings.AUTONOMY_ACTION_CONFIDENCE_THRESHOLD,
        )
        autonomous_monitor.start()
    try:
        yield
    finally:
        if autonomous_monitor is not None:
            await autonomous_monitor.stop()
            autonomous_monitor = None
        await close_databases()


app = FastAPI(
    title="EcoNest Orchestrator",
    description="Smart Home Sensor With Reasoning",
    version=settings.VERSION,
    lifespan=lifespan,
)


app.include_router(auth.router)
app.include_router(demo.router)
app.include_router(devices.router)
app.include_router(graph.router)
app.include_router(mcp.router)
app.include_router(mcp_server.router)
app.include_router(ontology.router)
app.include_router(readings.router)
app.include_router(users.router)


@app.get("/health")
async def health_check() -> dict[str, Any]:
    """Infrastructure health probe."""
    mysql_ok = await healthcheck_mysql()
    arcadedb_ok = await healthcheck_arcadedb()

    overall = mysql_ok and arcadedb_ok

    return {
        "status": "ok" if overall else "degraded",
        "version": settings.VERSION,
        "services": {
            "mysql": mysql_ok,
            "arcadedb": arcadedb_ok,
        },
    }


@app.get("/autonomy/status")
async def autonomy_status() -> dict[str, Any]:
    """Read-only status for the background autonomous monitor."""
    if autonomous_monitor is None:
        return {
            "enabled": settings.AUTONOMY_MONITOR_ENABLED,
            "running": False,
            "interval_seconds": settings.AUTONOMY_MONITOR_INTERVAL_SECONDS,
            "run_on_startup": settings.AUTONOMY_MONITOR_RUN_ON_STARTUP,
            "actions_enabled": settings.AUTONOMY_ACTIONS_ENABLED,
            "action_confidence_threshold": settings.AUTONOMY_ACTION_CONFIDENCE_THRESHOLD,
            "allowed_actions": settings.AUTONOMY_ALLOWED_ACTIONS,
            "allowed_entities": settings.AUTONOMY_ALLOWED_ENTITIES,
        }
    status = autonomous_monitor.status()
    status["actions_enabled"] = settings.AUTONOMY_ACTIONS_ENABLED
    status["allowed_actions"] = settings.AUTONOMY_ALLOWED_ACTIONS
    status["allowed_entities"] = settings.AUTONOMY_ALLOWED_ENTITIES
    return status


@app.post("/autonomy/run-once")
async def autonomy_run_once() -> dict[str, Any]:
    """Run one autonomous monitor cycle immediately for demos/tests."""
    if autonomous_monitor is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Autonomous monitor is not enabled",
        )
    result = await autonomous_monitor.run_once()
    return {
        "result": result,
        "status": autonomous_monitor.status(),
    }
