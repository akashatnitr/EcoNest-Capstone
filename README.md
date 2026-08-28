# EcoNest - Energy-Aware Smart Home Orchestrator

EcoNest is a capstone smart home system for energy monitoring, anomaly
detection, graph-based home context, and AI-assisted automation. The current
architecture centers on a FastAPI orchestrator that coordinates MySQL,
ArcadeDB, Home Assistant, MCP tools, specialized agents, and a local Ollama
model. It provides both a human-operated Command Center and a constrained,
auditable autonomy path.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Repository Structure](#repository-structure)
4. [Quick Start](#quick-start)
5. [Live Interfaces](#live-interfaces)
6. [Command Center and Autonomy](#command-center-and-autonomy)
7. [Authentication](#authentication)
8. [Orchestrator API](#orchestrator-api)
9. [MCP Tools and Agents](#mcp-tools-and-agents)
10. [Ontology and Graph](#ontology-and-graph)
11. [Sensor Polling and ML Scripts](#sensor-polling-and-ml-scripts)
12. [Home Assistant](#home-assistant)
13. [Database Schema](#database-schema)
14. [Documentation](#documentation)

---

## Project Overview

EcoNest monitors energy consumption, motion, sound, and device state across a
home. Sensor data is stored in MySQL, mapped into an ArcadeDB graph, interpreted
through an RDF ontology, and routed through specialized agents for energy, security,
sensor health, and device-control workflows.

### Key Features

- **FastAPI orchestrator** for auth, devices, graph access, ontology endpoints,
  MCP tools, and agent task routing.
- **MySQL storage** for users, sessions, devices, rooms, sensor readings,
  snapshots, analytics, and profiles.
- **ArcadeDB graph** for homes, rooms, devices, circuits, sensors, users,
  permissions, capabilities, and relationships.
- **MCP tool layer** for database queries, graph lookups, Home Assistant state,
  and device actions.
- **Specialized agents** for energy, security, sensor, and device workflows.
- **Local LLM inference** through Ollama with Gemma4 as both the primary and
  configured fallback model.
- **Home Assistant integration** for live device state, automations, and
  service calls.
- **Command Center UI** for authenticated, explicit Home Assistant commands.
- **Autonomy Activity UI** for recommendation history, confidence, reasoning,
  fallback diagnostics, and execution outcomes.

---

## Architecture

EcoNest is being migrated from a static collection of scripts into an
orchestrated, agentic smart-home platform. The current target architecture has
one central FastAPI service, the `orchestrator`, that coordinates data storage,
Home Assistant access, graph context, model inference, MCP tools, and agent
workflows.

```text
Home Assistant + sensor/analytics scripts + Command Center
        |
        | device state, service calls, sensor readings, anomaly events
        v
FastAPI orchestrator
        |
        +-- Auth and security
        |      - admin identity for human operators
        |      - service identity for agents, MCP tools, and automation scripts
        |      - JWT access/refresh tokens
        |      - refresh-token hashing support
        |
        +-- MySQL relational storage
        |      - households
        |      - rooms mapped to Home Assistant areas
        |      - devices mapped to Home Assistant devices/entities
        |      - users and sessions
        |      - room/device access grants
        |      - sensor readings, snapshots, analytics, and device profiles
        |
        +-- ArcadeDB graph context
        |      - homes, rooms, devices, sensors, circuits, users, capabilities
        |      - relationship queries for neighborhood/context lookup
        |      - graph-backed reasoning support for agent decisions
        |
        +-- Home Assistant integration
        |      - live entity state lookup
        |      - service calls for device control
        |      - registry-derived mapping through entity, device, and area data
        |
        +-- MCP tool layer
        |      - safe MySQL read tools
        |      - ArcadeDB graph tools
        |      - Home Assistant state/action tools
        |      - device action wrappers
        |
        +-- Agent orchestrator
        |      - routes tasks by intent
        |      - delegates to energy, security, sensor, and device agents
        |      - uses tool permissions before exposing actions
        |
        +-- Ontology and reasoning
        |      - RDF/Turtle smart-home ontology
        |      - validation helpers
        |      - rule-style reasoning over known graph context
        |
        +-- Local LLM inference
               - Ollama-backed model client
               - optional MCP-assisted context gathering
               - fallback behavior when MCP tools are disabled
        |
        +-- Autonomous monitor
               - periodic Home Assistant state snapshots
               - recommendation, safety-gate, execution, and audit lifecycle
```

### Runtime Flow

1. Home Assistant and the legacy sensor scripts provide live smart-home inputs:
   entity states, service-call targets, sensor readings, and anomaly events.
2. The orchestrator receives API calls or MCP task requests and authenticates
   the caller as either `admin` or `service`.
3. For factual context, the orchestrator can read from MySQL, query ArcadeDB,
   call Home Assistant, and load ontology data.
4. For agentic work, the orchestrator routes the task to a specialized agent:
   energy, security, sensor, or device.
5. Agents use MCP tools instead of reaching directly into every backend. This
   keeps tool access centralized and makes permissions easier to enforce.
6. If an action is needed, such as turning on a light or checking a device
   neighborhood, the orchestrator validates the request and calls the correct
   Home Assistant or graph/database tool.
7. When enabled, the autonomous monitor separately collects periodic Home
   Assistant snapshots, asks the local model for one safe recommendation, and
   independently validates every proposed action before execution.

### Data Model Direction

The MySQL schema is the durable system-of-record for structured application
data. It stores households, users, sessions, rooms, devices, Home Assistant
entity mappings, access grants, readings, analytics, and profiles.

ArcadeDB is the relationship layer. It is used for graph questions that are
awkward in relational SQL, such as what devices are near a room, what sensors
monitor an area, what circuit powers a device, or what capabilities a device
exposes.

Home Assistant remains the live device-control layer. EcoNest should not try to
replace Home Assistant's device registry or automation runtime. Instead,
EcoNest maps HA entities/devices/areas into its own schema and graph so agents
can reason over them and call HA services safely.

### Legacy Code Status

Legacy Flask and standalone ML scripts are still present under `medium home/`
and `Machine_learning/`. They are useful for sensor polling, analytics, demos,
and migration support. New orchestration, authentication, graph, MCP, and agent
work should live under `orchestrator/`.

<!--
Older architecture note kept for reference:

Sensor scripts / Home Assistant
        |
        v
FastAPI orchestrator
        |
        +-- Auth and role-based permissions
        +-- Device and graph APIs
        +-- MCP tools
        +-- Agent orchestrator
        +-- Ontology loader, validator, and reasoner
        |
        +-- MySQL
        +-- ArcadeDB
        +-- Ollama (Gemma4)
-->

---

## Repository Structure

```text
EcoNest-Capstone/
├── orchestrator/                 # FastAPI orchestrator package
│   ├── main.py                    # Application entrypoint
│   ├── config.py                  # Pydantic settings and env vars
│   ├── api/                       # Auth, command, autonomy, device, graph, MCP, ontology routes
│   ├── agents/                    # Energy, Security, Sensor, Device agents
│   ├── core/                      # Database, autonomy, audit, permissions, security helpers
│   ├── graph/                     # ArcadeDB graph models, seeds, queries
│   ├── llm/                       # Ollama client, prompts, and memory helpers
│   ├── mcp/                       # MCP protocol server and tools
│   ├── ontology/                  # RDF/Turtle ontology and reasoning helpers
│   ├── static/                    # Command Center and Autonomy Activity pages
│   └── tests/                     # pytest suite
├── Machine_learning/              # Analytics, trigger, inference, demo scripts
├── medium home/                   # Legacy backend, frontend, and sensor scripts
├── homeassistant/                 # Home Assistant YAML snippets
├── Documentation/                 # Reports, manuals, proposal, CDR
├── docker-compose.yml             # MySQL, ArcadeDB, Ollama, orchestrator
├── pyproject.toml                 # Python dependencies and poe tasks
├── arcade_schema.sql              # ArcadeDB schema
└── database_schema.txt            # MySQL schema
```

---

## Quick Start

### Prerequisites

| Component | Notes |
|-----------|-------|
| Docker Desktop | Recommended for the orchestrator stack |
| Python 3.11+ | Required for local orchestrator development |
| Home Assistant | Optional for live device state and actions |
| Ollama | Used for local Gemma4 inference |

### 1. Start the Stack

```bash
docker compose up --build
```

This starts:

- ArcadeDB on `http://localhost:2480`
- MySQL on `localhost:3306`
- Ollama on `http://localhost:11434`
- Orchestrator on `http://localhost:8000`

The orchestrator health check is:

```bash
curl http://localhost:8000/health
```

### 2. Local Development Commands

The project defines `poethepoet` tasks in `pyproject.toml`:

```bash
poetry run poe format
poetry run poe lint
poetry run poe test
poetry run poe dev
```

---

## Live Interfaces

These links are available to devices connected to the EcoNest Tailscale
network. They are deployment-specific rather than public Internet URLs.

| Link | What it is |
|------|------------|
| [Autonomy Activity](http://100.75.149.121:8001/autonomy) | Readable history of autonomous and energy recommendations, including confidence, reasoning, model/fallback source, and whether an action executed, was skipped, or failed. |
| [EcoNest Command Center](http://100.75.149.121:8000/command) | The EcoNest web interface for signing in and sending an explicit command to an exact Home Assistant entity. |
| [Home Assistant Overview](http://100.75.149.121:8123/home/overview) | The Home Assistant dashboard: the live source of device states and the system that ultimately carries out approved device service calls. |
| [ArcadeDB Studio](http://100.75.149.121:2481/) | The browser interface for inspecting and querying EcoNest's ArcadeDB graph database, which stores room, device, sensor, and relationship context. |
| [MySQL Monitor](http://100.75.149.121:8001/monitor) | EcoNest's browser-based monitoring view for inspecting MySQL-backed readings, tables, and operational data. |

---

## Command Center and Autonomy

### Manual command path

The Command Center at `/command` is the human-operated path. A homeowner signs
in, enters a natural-language instruction, selects the exact Home Assistant
entity ID and action, then submits it. EcoNest routes the resulting task to the
DeviceAgent, checks permissions and capability data, calls Home Assistant, and
verifies the resulting entity state.

EcoNest deliberately does not guess which physical device should be changed:
the exact Home Assistant entity ID is required.

### Autonomous path

When `AUTONOMY_MONITOR_ENABLED=true`, the background monitor runs once at
startup and then at `AUTONOMY_MONITOR_INTERVAL_SECONDS` intervals. Each cycle:

1. Reads the current Home Assistant `/api/states` snapshot.
2. Builds a snapshot of people/device trackers, lights and switches on, open
   covers, active motion, current power, and energy-today readings.
3. Uses Gemma4 for suggestion-only household feedback and, separately, one
   structured low-risk action recommendation.
4. Applies independent safety gates: action allowlist, entity allowlist,
   low-risk classification, valid current state, and confidence threshold.
5. Routes a permitted action through the DeviceAgent only when
   `AUTONOMY_ACTIONS_ENABLED=true`.
6. Writes recommendation, fallback reason when applicable, and execution
   outcome to the audit trail.

The Autonomy Activity page is intentionally readable without the Command
Center sign-in flow. It shows recommendation timestamp, reasoning, confidence,
source (`ollama` or `fallback_policy`), and outcome. A fallback means the
structured Ollama action response was unavailable or invalid; it does not
allow the fallback policy to bypass the same safety gates.

Current autonomous lighting is restricted to explicitly allowlisted low-risk
actions. Home-level occupancy and live motion are useful signals, but they do
not prove a particular room is vacant; add a room-specific presence signal
before relying on autonomous room-vacancy decisions.

### On-demand energy recommendations

`POST /autonomy/energy-recommendations` queues an advisory-only EnergyAgent
review. It uses recent MySQL history, current observations, and graph context
to return energy recommendations, anomalies, routine estimates, and demand
forecasts. This path does not control devices.

---

## Authentication

The orchestrator uses JWT access and refresh tokens. Access tokens identify the
caller for API and MCP requests. Refresh tokens are used to issue new access
tokens and are designed to be stored as hashes rather than plaintext.

Important endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/register` | POST | Register an admin account |
| `/auth/login` | POST | Get access and refresh tokens |
| `/auth/refresh` | POST | Refresh an access token |
| `/auth/logout` | POST | Revoke a refresh token |
| `/auth/me` | GET | Read the current profile |

The active identity model is:

- `admin` for human operators.
- `service` for orchestrator internals, MCP tools, agents, and trusted
  automation scripts.

Sensor scripts should use a `service` JWT through:

```env
SERVICE_ACCOUNT_TOKEN=eyJ...
```

Create or rotate a local service-account token from the orchestrator container:

```bash
docker compose -f docker-compose.real.yml exec orchestrator \
  poetry run python scripts/create_service_account.py \
  --email service@econest.local
```

Copy the printed `SERVICE_ACCOUNT_TOKEN` into the Mac mini `.env` used by
trusted automation scripts. Re-running the command rotates the account password
and refresh sessions by default.

During migration from legacy scripts, a long-lived fallback key may be supplied:

```env
LEGACY_API_KEY=your-long-lived-key
```

### Service Identities

Automation and sensor scripts authenticate using service identities.

Examples:

- frontend
- logger
- sound_logger
- energy_sound_logger

Scripts provide:

Authorization: Bearer <SERVICE_ACCOUNT_TOKEN>

and identify themselves using:

X-Service-Name

This allows event attribution, diagnostics, agent memory tracking, and future
autonomous service coordination.

---

## Orchestrator API

The FastAPI application is defined in `orchestrator/main.py` and includes:

- `/auth` for registration, login, refresh, logout, and profile lookup.
- `/command` for the browser-based Command Center.
- `/autonomy` for the Autonomy Activity page and recommendation APIs.
- `/devices` for device listing, capabilities, actions, and basic control.
- `/graph` for ArcadeDB-backed room/device graph access.
- `/mcp` for task submission and MCP protocol routes.
- `/monitor` and `/readings` for operational and sensor-reading APIs.
- `/ontology` for ontology listing, validation, reasoning, and upload.
- `/users` for admin user management.

---

## MCP Tools and Agents

MCP tools are registered in `orchestrator/mcp/server.py`.

Current tool groups:

- MySQL read helpers: `query_mysql`, `get_readings`
- ArcadeDB graph helpers: `query_arcadedb`, `get_device_neighbors`
- Home Assistant helpers: `ha_get_state`, `ha_call_service`
- Device helpers: `device_turn_on`, `device_turn_off`,
  `device_set_brightness`, `device_get_status`

Agent routing is handled by `orchestrator/agents/orchestrator.py`.

Current agents:

- `EnergyAgent` — advisory energy analysis using recent readings, graph context,
  demand/routine estimates, anomaly checks, and optional tariff context. It does
  not control devices.
- `SecurityAgent` — security and event-oriented analysis.
- `SensorAgent` — sensor-health and reading diagnostics.
- `DeviceAgent` — capability- and permission-checked Home Assistant control,
  followed by state verification.

### Agent Runtime Flow

Most autonomous workflows follow the same execution path:

Event
→ Task
→ AgentOrchestrator
→ Specialized Agent
→ MCP Tool
→ External System

Examples:

- Energy anomaly → EnergyAgent → query_mysql/query_arcadedb
- Motion alert → SecurityAgent → Home Assistant state tools
- Sensor health issue → SensorAgent → diagnostics workflow
- Device request → DeviceAgent → capability verification → action

Submit work through:

```http
POST /mcp/task
```

with a JSON body containing an `intent`, `payload`, and optional
`timeout_seconds`.

---

## Ontology and Graph

The ontology lives in:

```text
orchestrator/ontology/smart_home.ttl
```

The ontology API can:

- list ontology classes/properties
- validate graph consistency
- run simple reasoning
- upload replacement Turtle files

### Capability-Based Device Control

Device actions are capability-driven rather than device-type driven.

Examples:

- OnOff
- Dimmable
- ColorControl

The DeviceAgent validates:

1. Device capability
2. User permission
3. Requested action
4. Result verification

before executing device operations.

This allows future agents to reason about available actions without relying on
hardcoded device types.

ArcadeDB graph helpers live in `orchestrator/graph/` and model relationships
such as:

- home contains rooms
- rooms contain devices
- devices are powered by circuits
- sensors monitor rooms
- users own homes or have device access
- devices expose capabilities and actions

---

## Sensor Polling and ML Scripts

Legacy and migration scripts remain under `medium home/` and
`Machine_learning/scripts/`.

Important scripts:

| Script | Purpose |
|--------|---------|
| `analytics.py` | Computes hourly historical baselines |
| `trigger.py` | Detects real-time anomalies |
| `inference.py` | Runs LLM classification and recommendations |
| `demo.py` | Interactive scenario showcase |
| `frontend.py` / `logger.py` / `sound_logger.py` | Sensor polling and reading submission |

`inference.py` can optionally use MCP tools for Home Assistant state:

```env
USE_MCP_TOOLS=true
ORCHESTRATOR_URL=http://localhost:8000
SERVICE_ACCOUNT_TOKEN=eyJ...
```

When MCP is disabled or unavailable, it falls back to direct Home Assistant
HTTP calls.

---

## Home Assistant

The `homeassistant/` directory contains YAML snippets for:

- lighting
- security
- climate
- irrigation
- garage/window covering automations
- sound and motion alerts

Copy the relevant snippets into Home Assistant or recreate them in the UI
automation editor.

---

## Database Schema

MySQL schema:

```text
database_schema.txt
scripts/migrate_add_users.sql
```

ArcadeDB schema:

```text
arcade_schema.sql
```

Primary MySQL tables include:

- `households`
- `rooms`
- `devices`
- `home_assistant_entities`
- `sensor_readings`
- `home_snapshot`
- `home_analytics`
- `device_profiles`
- `users`
- `user_sessions`
- `user_room_access`
- `user_device_access`

---

## Documentation

Project documents are stored under `Documentation/`:

- Proposal
- Critical Design Review
- Weekly reports
- Hardware manuals
- Home Assistant and Docker setup notes
