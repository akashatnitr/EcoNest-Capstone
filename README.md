# EcoNest

EcoNest is an AI-assisted smart-home system. It connects to Home Assistant,
understands user requests, reviews energy and security signals, and makes
carefully constrained device-control recommendations.

Its central principle is simple: **the model can recommend or propose an
action, but it cannot bypass EcoNest safety checks.** Every device action is
validated through MCP, checked against the live Home Assistant inventory, and
recorded in the audit trail.

## Start here

EcoNest has two main ways to interact with the home:

- **User action:** Open the Command Center, type a natural-language request,
  review EcoNest's interpretation, and confirm it before a device changes.
- **Autonomous recommendation:** The background monitor reviews the latest
  home state on a schedule. It can display recommendations and, only for
  explicitly allowed low-risk actions, carry out an action after safety checks.

For example, you can type:

```text
Turn off the media room light
How can I reduce my home's energy use?
Give me a security assessment
```

EcoNest identifies whether the request is a device command, energy review, or
security review. Device commands require confirmation. Energy and security
reviews are advisory and do not control devices.

## Open the EcoNest pages

These services are available from a device connected to the EcoNest Tailscale
network. Replace `<MAC_MINI_IP>` with the Tailscale IP address of the Mac mini.

| Page | Address | What it does |
| --- | --- | --- |
| **Autonomous actions** | `http://<MAC_MINI_IP>:8001/autonomy` | Shows recent autonomous and energy recommendations, including the reason, confidence, risk, model source, timestamp, and outcome. |
| **User actions** | `http://<MAC_MINI_IP>:8001/command` | The Command Center. Sign in, type a request in plain English, check the proposed interpretation, then confirm a device action or an energy/security review. |
| **MCP Activities** | `http://<MAC_MINI_IP>:8001/mcp/activity` | Shows the actual MCP resource reads and tool calls for each EcoNest task. Use it to verify which agent ran and how it accessed Home Assistant, MySQL, or ArcadeDB. |
| **Home Assistant** | `http://<MAC_MINI_IP>:8123/home/overview` | The live smart-home dashboard. Home Assistant is the source of device state and carries out approved service calls. |
| **MySQL** | `http://<MAC_MINI_IP>:8001/monitor` | A read-only EcoNest monitor for MySQL-backed operational data, including readings and stored application records. |
| **MySQL schema** | `http://<MAC_MINI_IP>:8001/schema` | A visual guide to the MySQL tables and how stored EcoNest data relates. |
| **ArcadeDB** | `http://<MAC_MINI_IP>:2481/` | ArcadeDB Studio, used to inspect EcoNest's graph of rooms, devices, sensors, capabilities, and relationships. |
| **Demo** | `http://<MAC_MINI_IP>:8001/demo` | A guided demonstration of EcoNest workflows and their intermediate steps. Useful for presentations and debugging. |
| **Orchestrator API** | `http://<MAC_MINI_IP>:8001/docs` | Interactive FastAPI documentation for EcoNest's HTTP API. This is mainly for developers and advanced troubleshooting. |

## How EcoNest works

```text
User, scheduled monitor, or Home Assistant event
                    ↓
             EcoNest Orchestrator
                    ↓
         Correct specialist agent is selected
                    ↓
       MCP tools read live facts or request an action
                    ↓
  Safety checks, Home Assistant call, state verification, audit record
```

### The main parts

| Part | Plain-English role |
| --- | --- |
| **Home Assistant** | Connects to the real home devices. It supplies live state and receives final approved commands. |
| **Orchestrator** | The EcoNest backend. It receives requests, selects agents, applies policy, manages authentication, and records outcomes. |
| **Gemma 3 (4B) through Ollama** | The local language model. It interprets requests, forms recommendations, and explains reasoning from supplied home context. |
| **MCP** | The controlled bridge agents use to obtain data or take an action. It creates an auditable trace and prevents direct, uncontrolled backend access. |
| **Agents** | Specialists that handle energy, security, sensor, or device-control work. |
| **MySQL** | Stores structured operational data: users, rooms, devices, readings, sessions, and audit history. |
| **ArcadeDB** | Stores relationships, such as which device is in which room, which sensor monitors an area, and which capabilities a device has. |

### The agents

- **Energy Agent:** Reviews energy readings, trends, anomalies, and available
  context to provide advisory energy recommendations. It does not directly
  control devices.
- **Security Agent:** Reviews security-related signals such as motion, sound,
  occupancy, and unusual events, then returns an assessment and recommendations.
- **Sensor Agent:** Diagnoses sensor readings, health, and data-related issues.
- **Device Agent:** Safely controls a Home Assistant entity after checking its
  capability, requested action, policy, and resulting state.

## Manual user actions

Use the **User actions** page when a person wants to control or ask something
of the home.

1. Sign in to the Command Center.
2. Type your request in ordinary language. You do not need to enter an entity
   ID or choose an action from a dropdown.
3. EcoNest reads the live Home Assistant device inventory through MCP and asks
   the local model to identify the target and action.
4. Read the proposed interpretation. If it is wrong or unclear, revise the
   request; EcoNest should ask for clarification rather than guess.
5. Confirm the interpretation. Only then does EcoNest send the task to the
   appropriate agent.
6. For a device command, the Device Agent validates the request, calls Home
   Assistant through MCP, checks the resulting state, and displays the outcome.

For energy or security questions, confirmation starts an advisory review. The
result appears in the Command Center and is recorded in activity history. No
device is changed by that review.

## Autonomous recommendations and actions

The autonomous monitor runs at the configured interval. On each cycle it reads
a current Home Assistant snapshot and can form one low-risk recommendation.

Before an automatic action is allowed, EcoNest independently checks:

1. The action is permitted by the autonomy action allowlist.
2. The exact entity is in the autonomy entity allowlist.
3. The recommendation meets the configured confidence threshold.
4. The live device state and required capability still support the action.
5. Home Assistant accepts the request and the expected state is verified.

Automatic actions are deliberately limited. A recommendation can be visible in
the Autonomy page even when actions are disabled or safety checks prevent
execution. This is expected behavior, not a failure.

## Understanding MCP Activity

An MCP Activity card groups all calls associated with one EcoNest task.

| Label | Meaning |
| --- | --- |
| `resource` | Read-only context supplied through MCP, such as the live device inventory or a defined snapshot. |
| `tool` | An approved operation, such as querying ArcadeDB, checking a Home Assistant state, or calling a Home Assistant service. |
| `energy`, `security`, `sensor`, `device` | The specialist agent that made the MCP call. |
| `orchestrator` | The coordinator that routed the task or recorded its result. |
| `background_monitor` | A scheduled autonomous check, rather than a Command Center request. |
| `completed` / `failed` | Whether that individual MCP call succeeded. A failed audit call does not automatically mean a device action failed; read the final task outcome. |

MCP Activity hides raw queries and sensitive values by design.

## Data and safety boundaries

- Home Assistant remains the live authority for device state and execution.
- MySQL is the structured system of record; ArcadeDB adds relationship context.
- MCP is required for agent access to Home Assistant and data systems.
- The model proposes; deterministic code validates.
- User commands require confirmation before execution.
- Autonomous actions require explicit configuration and are restricted to
  allowlisted, low-risk actions.
- Every task, recommendation, MCP call, and action outcome is recorded for
  troubleshooting and future model-evaluation work.

## Running the stack

The deployed Mac mini stack uses Docker Compose. From the repository root:

```bash
docker compose -f docker-compose.real.yml up -d
docker compose -f docker-compose.real.yml ps
```

The second command should show healthy MySQL, ArcadeDB, Ollama, and
orchestrator services. For local development and tests:

```bash
poetry run poe test
poetry run poe lint
poetry run poe format
```

Configuration and secrets stay in the local `.env` file and must not be
committed. Home Assistant registry exports and real household data must also
remain outside git.

## Repository map

```text
orchestrator/
├── agents/       # Energy, Security, Sensor, and Device agents
├── api/          # Browser pages and HTTP routes
├── core/         # Database, policy, audit, security, and Home Assistant helpers
├── graph/        # ArcadeDB graph models and sync helpers
├── llm/          # Ollama client and model prompts
├── mcp/          # MCP resources, tools, server, and execution boundary
├── static/       # Command Center, autonomy, MCP, monitor, schema, and demo pages
├── training/     # Privacy-filtered fine-tuning dataset helpers
└── tests/        # Automated tests
```

Legacy sensor and analytics work remains in `Machine_learning/` and `medium home/`.
New EcoNest orchestration work belongs in `orchestrator/`.
