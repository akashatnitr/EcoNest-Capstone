"""Async database clients for ArcadeDB and MySQL with lifespan management."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from orchestrator.config import Settings, get_settings

_settings: Optional[Settings] = None
_mysql_engine = None
_mysql_session_factory = None
_arcadedb_client: Optional[httpx.AsyncClient] = None


async def init_databases() -> None:
    """Initialize MySQL engine and ArcadeDB HTTP client."""
    global _settings, _mysql_engine, _mysql_session_factory, _arcadedb_client

    _settings = get_settings()

    # MySQL async engine via aiomysql driver
    db_url = (
        f"mysql+aiomysql://{_settings.MYSQL_USER}:{_settings.MYSQL_PASSWORD}"
        f"@{_settings.MYSQL_HOST}:{_settings.MYSQL_PORT}/{_settings.MYSQL_DATABASE}"
    )
    _mysql_engine = create_async_engine(
        db_url,
        pool_size=_settings.MYSQL_POOL_SIZE,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )
    _mysql_session_factory = async_sessionmaker(
        _mysql_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # ArcadeDB async HTTP client
    _arcadedb_client = httpx.AsyncClient(
        base_url=f"http://{_settings.ARCADEDB_HOST}:{_settings.ARCADEDB_PORT}",
        auth=(_settings.ARCADEDB_USER, _settings.ARCADEDB_PASSWORD),
        timeout=httpx.Timeout(30.0),
        headers={"Content-Type": "application/json"},
    )


async def close_databases() -> None:
    """Cleanly close MySQL engine and ArcadeDB HTTP client."""
    global _mysql_engine, _arcadedb_client

    if _arcadedb_client is not None:
        await _arcadedb_client.aclose()
        _arcadedb_client = None

    if _mysql_engine is not None:
        await _mysql_engine.dispose()
        _mysql_engine = None


@asynccontextmanager
async def mysql_session_context() -> AsyncIterator[AsyncSession]:
    """Provide an async MySQL session outside FastAPI dependency injection."""
    if _mysql_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_databases() first.")
    async with _mysql_session_factory() as session:
        yield session


async def get_mysql_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async MySQL session for FastAPI dependency injection."""
    async with mysql_session_context() as session:
        yield session


async def arcadedb_query(
    language: str,
    command: str,
    database: Optional[str] = None,
    readonly: bool = True,
) -> dict[str, Any]:
    """Execute a query/command against ArcadeDB via HTTP."""
    if _arcadedb_client is None or _settings is None:
        raise RuntimeError(
            "ArcadeDB client not initialized. Call init_databases() first."
        )

    db = database or _settings.ARCADEDB_DATABASE
    url = f"/api/v1/command/{db}"
    payload = {
        "language": language,
        "command": command,
        "readonly": readonly,
    }
    response = await _arcadedb_client.post(url, json=payload)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict):
        return data
    return {"result": data}


async def arcadedb_server_command(command: str) -> dict[str, Any]:
    """Execute a server-level ArcadeDB command."""
    if _arcadedb_client is None:
        raise RuntimeError(
            "ArcadeDB client not initialized. Call init_databases() first."
        )

    response = await _arcadedb_client.post(
        "/api/v1/server",
        json={"command": command},
    )
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict):
        return data
    return {"result": data}


async def arcadedb_database_exists(database: Optional[str] = None) -> bool:
    """Return True if an ArcadeDB database exists."""
    if _arcadedb_client is None or _settings is None:
        raise RuntimeError(
            "ArcadeDB client not initialized. Call init_databases() first."
        )

    db = database or _settings.ARCADEDB_DATABASE
    response = await _arcadedb_client.get(f"/api/v1/exists/{db}")
    response.raise_for_status()
    data = response.json()
    return bool(isinstance(data, dict) and data.get("result") is True)


async def ensure_arcadedb_database(database: Optional[str] = None) -> bool:
    """Create the configured ArcadeDB database if it does not exist.

    Returns True when a database was created and False when it already existed.
    """
    if _settings is None:
        raise RuntimeError(
            "ArcadeDB client not initialized. Call init_databases() first."
        )

    db = database or _settings.ARCADEDB_DATABASE
    if await arcadedb_database_exists(db):
        return False
    await arcadedb_server_command(f"create database {db}")
    return True


async def healthcheck_mysql() -> bool:
    """Return True if MySQL is reachable."""
    if _mysql_engine is None:
        return False
    try:
        async with _mysql_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def healthcheck_arcadedb() -> bool:
    """Return True if ArcadeDB is reachable."""
    if _arcadedb_client is None:
        return False
    try:
        response = await _arcadedb_client.get("/api/v1/server")
        return response.status_code == 200
    except Exception:
        return False
