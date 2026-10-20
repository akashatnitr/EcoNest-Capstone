"""FastAPI entrypoint for the EcoNest orchestrator."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import ipaddress
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from orchestrator.api import (
    analytics,
    auth,
    autonomy,
    benchmarks,
    calendar,
    command,
    demo,
    devices,
    graph,
    gmail,
    home,
    integrations,
    launchpad,
    mcp,
    monitor,
    ontology,
    readings,
    schema,
    simulation,
    users,
)
from orchestrator.config import get_settings
from orchestrator.core.autonomy import AutonomousMonitor
from orchestrator.core.calendar_monitor import CalendarContextMonitor
from orchestrator.core.database import (
    close_databases,
    healthcheck_arcadedb,
    healthcheck_mysql,
    init_databases,
)
from orchestrator.core.event_dispatcher import EventDispatcher
from orchestrator.core.graph_sync import GraphSyncMonitor
from orchestrator.core.ha_ingest import HomeAssistantIngestor
from orchestrator.core.security import decode_token
from orchestrator.mcp import server as mcp_server

settings = get_settings()
autonomous_monitor: AutonomousMonitor | None = None
ha_ingestor: HomeAssistantIngestor | None = None
graph_sync_monitor: GraphSyncMonitor | None = None
calendar_context_monitor: CalendarContextMonitor | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage database connections across the application lifespan."""
    global autonomous_monitor, calendar_context_monitor, ha_ingestor, graph_sync_monitor
    await init_databases()
    if settings.HA_INGEST_ENABLED:
        ha_ingestor = HomeAssistantIngestor(settings, EventDispatcher(settings))
        ha_ingestor.start()
    if settings.GRAPH_SYNC_ENABLED:
        graph_sync_monitor = GraphSyncMonitor(settings)
        graph_sync_monitor.start()
    if (
        settings.GOOGLE_CALENDAR_ENABLED
        and settings.GOOGLE_CALENDAR_AUTONOMOUS_REVIEWS_ENABLED
    ):
        calendar_context_monitor = CalendarContextMonitor(
            autonomy.run_calendar_context_reviews,
            interval_seconds=settings.GOOGLE_CALENDAR_SYNC_INTERVAL_SECONDS,
        )
        calendar_context_monitor.start()
    if settings.AUTONOMY_MONITOR_ENABLED:
        autonomous_monitor = AutonomousMonitor(
            lambda: demo.collect_periodic_feedback(trigger="background_monitor"),
            interval_seconds=settings.AUTONOMY_RECOMMENDATION_INTERVAL_SECONDS,
            run_on_startup=settings.AUTONOMY_MONITOR_RUN_ON_STARTUP,
            action_recommender=demo.recommend_autonomous_action,
            action_executor=demo.execute_autonomous_action,
            advisory_runner=(
                autonomy.run_scheduled_recommendation
                if settings.AUTONOMY_RECOMMENDATIONS_ENABLED
                else None
            ),
            action_confidence_threshold=settings.AUTONOMY_ACTION_CONFIDENCE_THRESHOLD,
            actions_enabled=settings.AUTONOMY_ACTIONS_ENABLED,
            align_to_interval=True,
        )
        autonomous_monitor.start()
    try:
        yield
    finally:
        if calendar_context_monitor is not None:
            await calendar_context_monitor.stop()
            calendar_context_monitor = None
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


@app.middleware("http")
async def redirect_direct_tailscale_ip_to_https(request: Request, call_next: Any) -> Any:
    """Upgrade direct Tailnet-IP visits to the configured HTTPS hostname.

    Microphone access is granted only to secure browser contexts.  Redirecting
    at the application boundary also covers the Launchpad and its embedded
    pages, rather than fixing only the standalone command page.
    """
    public_https_url = settings.ECONEST_PUBLIC_HTTPS_URL.rstrip("/")
    if public_https_url and _is_direct_tailscale_ip_request(request):
        location = f"{public_https_url}{request.url.path}"
        if request.url.query:
            location = f"{location}?{request.url.query}"
        return RedirectResponse(location, status_code=status.HTTP_308_PERMANENT_REDIRECT)
    if _requires_launchpad_login(request) and not _has_valid_browser_session(request):
        next_path = request.url.path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        return RedirectResponse(
            f"/login?next={quote(next_path, safe='/')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return await call_next(request)


def _is_direct_tailscale_ip_request(request: Request) -> bool:
    """Return whether a request uses a direct Tailscale CGNAT address."""
    host = request.headers.get("host", "").split(":", 1)[0].strip("[]")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address in ipaddress.ip_network("100.64.0.0/10")


def _requires_launchpad_login(request: Request) -> bool:
    """Return whether this browser-facing workspace request needs a session."""
    if not settings.LAUNCHPAD_AUTH_REQUIRED:
        return False
    path = request.url.path
    protected_prefixes = (
        "/analytics",
        "/autonomy",
        "/benchmarks",
        "/calendar",
        "/command",
        "/devices",
        "/gmail",
        "/home",
        "/integrations",
        "/mcp/activity",
        "/monitor",
        "/schema",
        "/simulation",
    )
    return path in {"/", "/launchpad"} or path.startswith(protected_prefixes)


def _has_valid_browser_session(request: Request) -> bool:
    """Validate the short-lived access cookie used by Launchpad navigation."""
    token = request.cookies.get(settings.AUTH_SESSION_COOKIE_NAME)
    payload = decode_token(token) if token else None
    return payload is not None and payload.get("type") == "access"


app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)


app.include_router(auth.router)
app.include_router(analytics.router)
app.include_router(autonomy.router)
app.include_router(benchmarks.router)
app.include_router(calendar.router)
app.include_router(command.router)
app.include_router(demo.router)
app.include_router(devices.router)
app.include_router(graph.router)
app.include_router(gmail.router)
app.include_router(home.router)
app.include_router(integrations.router)
app.include_router(launchpad.router)
app.include_router(mcp.router)
app.include_router(mcp_server.router)
app.include_router(monitor.router)
app.include_router(ontology.router)
app.include_router(readings.router)
app.include_router(schema.router)
app.include_router(simulation.router)
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
            "interval_seconds": settings.AUTONOMY_RECOMMENDATION_INTERVAL_SECONDS,
            "recommendation_interval_seconds": settings.AUTONOMY_RECOMMENDATION_INTERVAL_SECONDS,
            "recommendations_enabled": settings.AUTONOMY_RECOMMENDATIONS_ENABLED,
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
