# EcoNest - Smart Home Energy Monitoring & Automation

EcoNest is a capstone smart home system for energy monitoring, anomaly
detection, graph-based home context, and AI-assisted automation. The current
architecture centers on a FastAPI orchestrator that coordinates MySQL,
ArcadeDB, Home Assistant, MCP tools, sub-agents, and local Ollama models.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Repository Structure](#repository-structure)
4. [Quick Start](#quick-start)
5. [Authentication](#authentication)
6. [Orchestrator API](#orchestrator-api)
7. [MCP Tools and Agents](#mcp-tools-and-agents)
8. [Ontology and Graph](#ontology-and-graph)
9. [Sensor Polling and ML Scripts](#sensor-polling-and-ml-scripts)
10. [Home Assistant](#home-assistant)
11. [Database Schema](#database-schema)
12. [Documentation](#documentation)

---

## Project Overview

EcoNest monitors energy consumption, motion, sound, and device state across a
home. Sensor data is stored in MySQL, mapped into an ArcadeDB graph, interpreted
through an RDF ontology, and routed through sub-agents for energy, security,
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
- **Sub-agents** for energy, security, sensor, and device workflows.
- **Local LLM inference** through Ollama with Gemma4 as the primary model and
  Mistral as fallback.
- **Home Assistant integration** for live device state, automations, and
  service calls.

---

## Architecture

EcoNest is being migrated from a static collection of scripts into an
orchestrated, agentic smart-home platform. The current target architecture has
one central FastAPI service, the `orchestrator`, that coordinates data storage,
Home Assistant access, graph context, model inference, MCP tools, and agent
workflows.

```text
Home Assistant + sensor/analytics scripts
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

### Identity Model

The current architecture intentionally keeps identities simple:

- `admin`: a human operator or owner of the EcoNest system.
- `service`: a non-human identity used by orchestrator internals, MCP tools,
  agents, and trusted automation scripts.

This is enough for the current dynamic/agentic migration. More user categories
can be added later if the product needs resident, guest, or room-specific human
accounts.

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
        +-- Ollama (Gemma4, fallback Mistral)
-->

---

## Repository Structure

```text
EcoNest-Capstone/
├── orchestrator/                 # FastAPI orchestrator package
│   ├── main.py                    # Application entrypoint
│   ├── config.py                  # Pydantic settings and env vars
│   ├── api/                       # Auth, device, graph, MCP, ontology routes
│   ├── agents/                    # Energy, Security, Sensor, Device agents
│   ├── core/                      # Database, permissions, security helpers
│   ├── graph/                     # ArcadeDB graph models, seeds, queries
│   ├── llm/                       # Ollama client and memory helpers
│   ├── mcp/                       # MCP protocol server and tools
│   ├── ontology/                  # RDF/Turtle ontology and reasoning helpers
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
| Ollama | Used for local Gemma4/Mistral inference |

### 1. Configure Environment

Create a `.env` file at the repository root when running locally:

```env
# MySQL
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=econest
MYSQL_DATABASE=econest

# ArcadeDB
ARCADEDB_HOST=localhost
ARCADEDB_PORT=2480
ARCADEDB_USER=root
ARCADEDB_PASSWORD=playwithdata
ARCADEDB_DATABASE=econest

# Ollama
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=gemma4
OLLAMA_FALLBACK_MODEL=gemma4

# Home Assistant
HA_URL=http://localhost:8123
HA_TOKEN=your_long_lived_access_token

# Auth
SECRET_KEY=replace-this-for-local-dev
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7
```

### 2. Start the Stack

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

### 3. Local Development Commands

The project defines `poethepoet` tasks in `pyproject.toml`:

```bash
poetry run poe format
poetry run poe lint
poetry run poe test
poetry run poe dev
```

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
- `/devices` for device listing, capabilities, actions, and basic control.
- `/graph` for ArcadeDB-backed room/device graph access.
- `/mcp` for task submission and MCP protocol routes.
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

- `EnergyAgent`
- `SecurityAgent`
- `SensorAgent`
- `DeviceAgent`

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
