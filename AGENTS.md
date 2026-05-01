# Agent Instructions for EcoNest Orchestrator

This file contains coding conventions, environment variable standards, and testing requirements for the `orchestrator/` package.

## Project Structure

```
orchestrator/
├── main.py              # FastAPI entrypoint with lifespan manager
├── config.py            # Pydantic Settings with .env support
├── api/                 # FastAPI route modules
├── core/                # Security, permissions, database
├── agents/              # Sub-agents (Energy, Security, Sensor, Device)
├── graph/               # ArcadeDB graph operations
├── ontology/            # RDF/Turtle ontology, loader, reasoner, validator
├── mcp/                 # MCP protocol server and tools
├── llm/                 # LLM client and memory
└── tests/               # pytest suite
```

## Coding Standards

- **Python**: >= 3.11
- **Type hints**: All public functions must have type hints (`disallow_untyped_defs = true` in mypy).
- **Async first**: Database and HTTP operations must use `async`/`await`.
- **Pydantic models**: Use Pydantic v2 for all request/response schemas and data models.
- **Error handling**: Raise custom exceptions in core code; handle and return appropriate HTTP status codes in API layers.
- **Logging**: Use the standard `logging` module. Log at `INFO` for normal operations and `WARNING`/`ERROR` for anomalies.

## Environment Variables

All configuration lives in `orchestrator/config.py` via `pydantic-settings`. Add new env vars there.

Common conventions:
- Database vars: `{SERVICE}_HOST`, `{SERVICE}_PORT`, `{SERVICE}_USER`, `{SERVICE}_PASSWORD`
- Token secrets: end with `_TOKEN` or `_KEY`
- Timeouts / TTLs: end with `_MINUTES`, `_DAYS`, or `_SECONDS`
- Booleans: use `1`/`0` or `true`/`false` strings

## Database Access

- **MySQL**: Use `sqlalchemy.ext.asyncio` (AsyncSession) via the shared engine/pool from `orchestrator/core/database.py`.
- **ArcadeDB**: Use the async HTTP wrapper from `orchestrator/core/database.py`.
- **Never** create ad-hoc connections outside of `database.py`.

## Testing Requirements

- **Framework**: pytest with `pytest-asyncio` (`asyncio_mode = auto`).
- **Test location**: `orchestrator/tests/`
- **Fixtures**: Shared fixtures for `arcadedb_client`, `mysql_pool`, `test_user`, and `auth_headers` will live in `orchestrator/tests/conftest.py`.
- **Mocking**: Mock external services (Ollama, Home Assistant) in unit tests.
- **Database isolation**: Use separate ArcadeDB/MySQL instances or prefixes for tests.

## Dev Scripts

Defined in `pyproject.toml` via `poethepoet`:

```bash
poetry run poe format   # black orchestrator/
poetry run poe lint     # ruff check orchestrator/
poetry run poe test     # pytest
poetry run poe dev      # uvicorn orchestrator.main:app --reload
```

Run all checks before committing:

```bash
poetry run poe format && poetry run poe lint && poetry run poe test
```
