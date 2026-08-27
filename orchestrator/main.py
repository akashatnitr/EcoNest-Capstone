"""FastAPI entrypoint for the EcoNest orchestrator."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, status

from orchestrator.api import (
    autonomy,
    auth,
    command,
    demo,
    devices,
    graph,
    mcp,
    monitor,
    ontology,
    readings,
    users,
)
from orchestrator.config import get_settings
from orchestrator.core.autonomy import AutonomousMonitor
from orchestrator.core.database import (
    close_databases,
    healthcheck_arcadedb,
    healthcheck_mysql,
    init_databases,
)
from orchestrator.core.graph_sync import GraphSyncMonitor
from orchestrator.core.ha_ingest import HomeAssistantIngestor
from orchestrator.core.event_dispatcher import EventDispatcher
from orchestrator.mcp import server as mcp_server

settings = get_settings()
autonomous_monitor: AutonomousMonitor | None = None
ha_ingestor: HomeAssistantIngestor | None = None
graph_sync_monitor: GraphSyncMonitor | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage database connections across the application lifespan."""
    global autonomous_monitor, ha_ingestor, graph_sync_monitor
    await init_databases()
    if settings.HA_INGEST_ENABLED:
        ha_ingestor = HomeAssistantIngestor(settings, EventDispatcher(settings))
        ha_ingestor.start()
    if settings.GRAPH_SYNC_ENABLED:
        graph_sync_monitor = GraphSyncMonitor(settings)
        graph_sync_monitor.start()
    if settings.AUTONOMY_MONITOR_ENABLED:
        autonomous_monitor = AutonomousMonitor(
            lambda: demo.collect_periodic_feedback(trigger="background_monitor"),
            interval_seconds=settings.AUTONOMY_MONITOR_INTERVAL_SECONDS,
            run_on_startup=settings.AUTONOMY_MONITOR_RUN_ON_STARTUP,
            action_recommender=demo.recommend_autonomous_action,
            action_executor=demo.execute_autonomous_action,
            action_confidence_threshold=settings.AUTONOMY_ACTION_CONFIDENCE_THRESHOLD,
            actions_enabled=settings.AUTONOMY_ACTIONS_ENABLED,
        )
        autonomous_monitor.start()
    try:
        yield
    finally:
        if graph_sync_monitor is not None:
            await graph_sync_monitor.stop()
            graph_sync_monitor = None
        if ha_ingestor is not None:
            await ha_ingestor.stop()
            ha_ingestor = None
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
app.include_router(autonomy.router)
app.include_router(command.router)
app.include_router(demo.router)
app.include_router(devices.router)
app.include_router(graph.router)
app.include_router(mcp.router)
app.include_router(mcp_server.router)
app.include_router(monitor.router)
app.include_router(ontology.router)
app.include_router(readings.router)
app.include_router(users.router)


@app.get("/ingestion/status")
async def ingestion_status() -> dict[str, Any]:
    """Return Home Assistant sensor-ingestion status."""
    if ha_ingestor is None:
        return {
            "enabled": settings.HA_INGEST_ENABLED,
            "running": False,
            "interval_seconds": settings.HA_INGEST_INTERVAL_SECONDS,
        }
    return ha_ingestor.status()


@app.post("/ingestion/run-once")
async def ingestion_run_once() -> dict[str, Any]:
    """Immediately collect and persist one Home Assistant sensor snapshot."""
    if ha_ingestor is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Home Assistant ingestion is not enabled",
        )
    return await ha_ingestor.run_once()


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


@app.get("/recovery/status")
async def recovery_status() -> dict[str, Any]:
    """Read-only Mac Mini recovery evidence and remediation guidance."""
    mysql_ok = await healthcheck_mysql()
    arcade_ok = await healthcheck_arcadedb()
    return {
        "services": {"mysql": mysql_ok, "arcadedb": arcade_ok},
        "ingestion": ha_ingestor.status() if ha_ingestor else {"running": False},
        "graph_sync": graph_sync_monitor.status() if graph_sync_monitor else {"running": False},
        "remediation": [] if mysql_ok and arcade_ok else ["Check Docker Desktop, then run docker compose -f docker-compose.real.yml up -d."],
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
