# Agent Instructions for EcoNest Orchestrator

This file defines the coding conventions, environment variable standards,
database boundaries, and testing expectations for work in the `orchestrator/`
package.

## Project Structure

```text
orchestrator/
+-- main.py              # FastAPI entrypoint and lifespan manager
+-- config.py            # Pydantic Settings with .env support
+-- api/                 # FastAPI route modules
+-- core/                # Security, permissions, database clients
+-- agents/              # Energy, Security, Sensor, and Device agents
+-- graph/               # ArcadeDB graph models, seeds, queries, sync helpers
+-- ontology/            # RDF/Turtle ontology, loader, reasoner, validator
+-- mcp/                 # MCP protocol server and tools
+-- llm/                 # LLM client and memory helpers
+-- tests/               # pytest suite
```

## Coding Standards

- Python must target version 3.11 or newer.
- Public functions must have type hints. The project enables
  `disallow_untyped_defs = true` in mypy.
- Database and HTTP operations in the orchestrator should be async.
- Use Pydantic v2 models for request, response, and internal structured data.
- Keep route-specific HTTP error handling in `orchestrator/api/`.
- Keep reusable security, permissions, and database logic in `orchestrator/core/`.
- Use the standard `logging` module. Log normal operations at `INFO` and
  unexpected or unsafe behavior at `WARNING` or `ERROR`.
- Prefer small, focused modules over large route handlers with embedded business
  logic.

## Environment Variables

All orchestrator configuration must live in `orchestrator/config.py` through
`pydantic-settings`. Add new orchestrator env vars there instead of reading
directly from `os.environ` inside route or core modules.

Naming conventions:

- Database vars: `{SERVICE}_HOST`, `{SERVICE}_PORT`, `{SERVICE}_USER`,
  `{SERVICE}_PASSWORD`, `{SERVICE}_DATABASE`
- Token secrets: end with `_TOKEN` or `_KEY`
- Timeouts and TTLs: end with `_SECONDS`, `_MINUTES`, or `_DAYS`
- Booleans: accept `1`/`0` or `true`/`false`

Common orchestrator vars:

```env
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=econest
MYSQL_DATABASE=econest

ARCADEDB_HOST=localhost
ARCADEDB_PORT=2480
ARCADEDB_USER=root
ARCADEDB_PASSWORD=playwithdata
ARCADEDB_DATABASE=econest

OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=gemma4
OLLAMA_FALLBACK_MODEL=mistral

HA_URL=http://localhost:8123
HA_TOKEN=your_home_assistant_token

SECRET_KEY=replace-this-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7
```

Sensor scripts that post readings to an authenticated backend should use a
service-account JWT:

```env
SERVICE_ACCOUNT_TOKEN=eyJ...
```

During migration from legacy scripts, a long-lived API key fallback may also be
used:

```env
LEGACY_API_KEY=your-long-lived-key
```

When both values are set, scripts must prefer `SERVICE_ACCOUNT_TOKEN`.

## Database Access

- MySQL access must go through the async SQLAlchemy session factory in
  `orchestrator/core/database.py`.
- ArcadeDB access must go through the async HTTP wrapper in
  `orchestrator/core/database.py`.
- Do not create ad-hoc MySQL, ArcadeDB, or HTTP clients in route handlers.
- Keep schema migrations in `scripts/` and keep Docker init order in
  `docker-compose.yml` aligned with those migrations.
- Database changes must include focused tests or explicit migration notes.

## Security and Permissions

- Password hashing, token creation, token decoding, and token validation belong
  in `orchestrator/core/security.py`.
- Role definitions, permission constants, RBAC checks, and ABAC helpers belong
  in `orchestrator/core/permissions.py`.
- API routes should call shared permission helpers instead of duplicating role
  logic.
- Never log passwords, refresh tokens, access tokens, Home Assistant tokens, or
  API keys.
- Public registration should stay conservative. More privileged users and
  service accounts should be provisioned through admin-controlled workflows.

## Testing Requirements

- Use pytest with `pytest-asyncio`; `asyncio_mode = auto` is configured in
  `pyproject.toml`.
- Tests live under `orchestrator/tests/`.
- Mock external services such as Ollama and Home Assistant in unit tests.
- Use mocked MySQL and ArcadeDB clients for route/unit tests unless the test is
  explicitly an integration test.
- Add tests for security-sensitive behavior:
  - password hashing and verification
  - access and refresh token validation
  - revoked or expired refresh tokens
  - role and permission boundaries
  - auth-protected route behavior

## Development Scripts

Defined in `pyproject.toml` through `poethepoet`:

```bash
poetry run poe format   # black orchestrator/
poetry run poe lint     # ruff check orchestrator/
poetry run poe test     # pytest
poetry run poe dev      # uvicorn orchestrator.main:app --reload
```

Run all checks before committing when the local environment supports it:

```bash
poetry run poe format && poetry run poe lint && poetry run poe test
```
