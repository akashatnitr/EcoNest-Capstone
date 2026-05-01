"""FastAPI entrypoint for the EcoNest orchestrator."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from orchestrator.api import auth, devices, graph, mcp, ontology, users
from orchestrator.config import get_settings
from orchestrator.core.database import close_databases, init_databases
from orchestrator.mcp import server as mcp_server

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
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
async def health_check():
    """Liveness probe."""
    return {"status": "ok", "version": settings.VERSION}
