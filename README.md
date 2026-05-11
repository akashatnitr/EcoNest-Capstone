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

```text
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
```

Legacy Flask and standalone ML scripts are still present under `medium home/`
and `Machine_learning/`. They are useful for sensor polling, analytics, demos,
and migration support, while new orchestrator work should live under
`orchestrator/`.

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
OLLAMA_FALLBACK_MODEL=mistral

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

The orchestrator uses JWT access and refresh tokens.

Important endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/register` | POST | Register a homeowner account |
| `/auth/login` | POST | Get access and refresh tokens |
| `/auth/refresh` | POST | Refresh an access token |
| `/auth/logout` | POST | Revoke a refresh token |
| `/auth/me` | GET | Read the current profile |

Roles are defined in `orchestrator/core/permissions.py`:

- `guest`
- `family_member`
- `homeowner`
- `service_account`
- `superadmin`

Sensor scripts should use a `service_account` JWT through:

```env
SERVICE_ACCOUNT_TOKEN=eyJ...
```

During migration from legacy scripts, a long-lived fallback key may be supplied:

```env
LEGACY_API_KEY=your-long-lived-key
```

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

- `rooms`
- `devices`
- `sensor_readings`
- `home_snapshot`
- `home_analytics`
- `device_profiles`
- `users`
- `user_sessions`

---

## Documentation

Project documents are stored under `Documentation/`:

- Proposal
- Critical Design Review
- Weekly reports
- Hardware manuals
- Home Assistant and Docker setup notes
