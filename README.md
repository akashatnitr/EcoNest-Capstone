# EcoNest

EcoNest is an AI-assisted smart-home system. It connects to Home Assistant,
understands user requests, reviews energy and security signals, and makes
carefully constrained device-control recommendations.

The model can recommend or propose an action, but it cannot bypass EcoNest's
safety checks. Every device action goes through MCP, live Home Assistant
validation, state verification, and an audit record.

## Start here

Use EcoNest in two ways:

- **User action:** Type a request in the Command Center, review EcoNest's
  interpretation, and confirm it before a device changes.
- **Autonomous recommendation:** The scheduled monitor reviews home state and
  records recommendations. It can execute only explicitly allowed low-risk
  actions after every safety check passes.

Examples:

```text
Turn off the media room light
How can I reduce my home's energy use?
Give me a security assessment
```

Device commands require confirmation. Energy and security reviews are advisory
and do not control devices.

## Open the EcoNest pages

These services are available through the EcoNest Tailscale network. Replace
`<MAC_MINI_IP>` with the Mac mini's Tailscale IP address.

| Page | Address | What it does |
| --- | --- | --- |
| Autonomous actions | `http://<MAC_MINI_IP>:8001/autonomy` | Shows autonomous and energy recommendations, their reason, confidence, risk, timestamp, source, and outcome. |
| User actions | `http://<MAC_MINI_IP>:8001/command` | The Command Center for natural-language device commands and energy/security reviews. |
| MCP Activities | `http://<MAC_MINI_IP>:8001/mcp/activity` | Shows the actual MCP calls for each task, including the agent, source, timing, and result. |
| Home Assistant | `http://<MAC_MINI_IP>:8123/home/overview` | The live smart-home dashboard and final authority for device state and service calls. |
| MySQL | `http://<MAC_MINI_IP>:8001/monitor` | Read-only EcoNest monitor for MySQL-backed readings and operational records. |
| MySQL schema | `http://<MAC_MINI_IP>:8001/schema` | Visual guide to the MySQL tables and their relationships. |
| ArcadeDB | `http://<MAC_MINI_IP>:2481/` | ArcadeDB Studio for inspecting rooms, devices, sensors, capabilities, and relationships. |
| Demo | `http://<MAC_MINI_IP>:8001/demo` | Guided demonstration of EcoNest workflows and their intermediate steps. |
| Orchestrator API | `http://<MAC_MINI_IP>:8001/docs` | Interactive FastAPI documentation for developers and troubleshooting. |

## How it works

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

| Part | Role |
| --- | --- |
| Home Assistant | Connects to physical devices, supplies live state, and performs approved service calls. |
| Orchestrator | Receives requests, selects agents, applies policy, and records outcomes. |
| Gemma 3 (4B) through Ollama | Local model for request interpretation, recommendations, and explanations. |
| MCP | Controlled, auditable bridge that agents use to read context or take approved actions. |
| MySQL | Structured data store for rooms, devices, readings, users, sessions, and audit history. |
| ArcadeDB | Relationship graph for rooms, devices, sensors, capabilities, and context. |

### Agents

- **Energy Agent:** Produces advisory energy analysis and recommendations.
- **Security Agent:** Assesses security signals such as motion, sound,
  occupancy, and unusual events.
- **Sensor Agent:** Diagnoses sensor readings and health.
- **Device Agent:** Validates a device capability and action, calls Home
  Assistant through MCP, and verifies the resulting state.

## Sending user requests

1. Open **User actions** and sign in.
2. Type a request in ordinary language; an entity ID or action dropdown is not
   required.
3. EcoNest reads the live Home Assistant device inventory through MCP and
   proposes an interpretation.
4. Revise an incorrect interpretation, or confirm a correct one.
5. After confirmation, EcoNest routes it to the appropriate agent.

For a device command, the Device Agent runs the capability, policy, and live
state checks before it calls Home Assistant. An energy or security request runs
an advisory review only; it cannot change a device.

## Autonomous recommendations and actions

The background monitor runs at its configured interval and can form a low-risk
recommendation from the current Home Assistant snapshot. Before execution,
EcoNest checks the allowed action, exact allowlisted entity, confidence
threshold, device capability, current state, and verified Home Assistant result.

Recommendations remain visible in the Autonomy page even when automatic actions
are disabled or a safety check stops them. That is expected and is not a failure.

## Reading MCP Activity

| Label | Meaning |
| --- | --- |
| `resource` | Read-only context, such as a live device inventory or defined home snapshot. |
| `tool` | Approved operation, such as querying ArcadeDB, checking Home Assistant state, or calling a Home Assistant service. |
| `energy`, `security`, `sensor`, `device` | Specialist agent that made the call. |
| `orchestrator` | Coordinator that routed the task or recorded its result. |
| `background_monitor` | Scheduled autonomous check rather than a Command Center request. |
| `completed` / `failed` | Result of that individual MCP call. Check the final task outcome to determine whether a device action succeeded. |

Raw queries and sensitive values are intentionally not displayed.

## Running the stack

From the repository root:

```bash
docker compose -f docker-compose.real.yml up -d
docker compose -f docker-compose.real.yml ps
poetry run poe test
```

Configuration and secrets remain in the local `.env` file and must not be
committed. Home Assistant registry exports and real household data also remain
outside git.

## Repository map

```text
orchestrator/
├── agents/       # Energy, Security, Sensor, and Device agents
├── api/          # Browser pages and HTTP routes
├── core/         # Database, policy, audit, security, and Home Assistant helpers
├── graph/        # ArcadeDB graph models and sync helpers
├── llm/          # Ollama client and prompts
├── mcp/          # MCP resources, tools, server, and execution boundary
├── static/       # Command Center, autonomy, MCP, monitor, schema, and demo pages
├── training/     # Privacy-filtered fine-tuning dataset helpers
└── tests/        # Automated tests
```
