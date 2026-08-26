# EcoNest technical onboarding

These documents describe the architecture that exists in the repository today.
They separate the intended runtime design from legacy code and from behavior
that still needs live verification.

Read them in this order:

1. [Architecture and data flow](architecture-and-dataflow.md) — what runs,
   where data goes, and which integrations own which responsibilities.
2. [Data integrity verification](data-integrity-verification.md) — payload
   contracts, database expectations, and a safe evidence-gathering runbook.
3. [Legacy sensor collectors](legacy-sensor-collectors.md) — the limited
   historical context needed for `medium home/`.

## Vocabulary

- **Home Assistant (HA):** the live device registry, state, automation, and
  device-control system.
- **MySQL:** EcoNest's durable relational store for inventory and time-series
  reading records.
- **ArcadeDB:** EcoNest's relationship/reasoning graph. It is not the primary
  sink for normal ingestion.
- **Orchestrator:** the FastAPI application in `orchestrator/` that owns the
  current API, authentication, graph integration, MCP tools, agents, and
  optional HA polling.

Never commit HA registry exports, state dumps, `.env` files, access tokens, or
home-specific device inventories. The repository's `.gitignore` already lists
the common local export names.
