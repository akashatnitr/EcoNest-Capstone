"""FastAPI entrypoint for the EcoNest orchestrator."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from orchestrator.api import auth, devices, graph, mcp, ontology, users
from orchestrator.config import get_settings
from orchestrator.core.database import (
    close_databases,
    healthcheck_arcadedb,
    healthcheck_mysql,
    init_databases,
)
from orchestrator.mcp import server as mcp_server

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage database connections across the application lifespan."""
    await init_databases()
    yield
    await close_databases()


app = FastAPI(
    title="EcoNest Orchestrator",
    description="Smart Home Sensor With Reasoning",
    version=settings.VERSION,
    lifespan=lifespan,
)


app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(graph.router)
app.include_router(mcp.router)
app.include_router(mcp_server.router)
app.include_router(ontology.router)
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
