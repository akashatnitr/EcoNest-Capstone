# EcoNest

EcoNest is an AI-assisted smart-home system. It connects to Home Assistant,
understands user requests, reviews energy and security signals, and makes
carefully constrained device-control recommendations.

The model can recommend or propose an action, but it cannot bypass EcoNest's
safety checks. Every device action goes through MCP, live Home Assistant
validation, state verification, and an audit record.

## How to use

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

## Open EcoNest

Open the **EcoNest Launchpad** at the root of the EcoNest Orchestrator. It is
the single entry point for every dashboard, integration, and developer tool.
The external Home Assistant and ArcadeDB buttons automatically use the same
host as the Launchpad.

**Launchpad:** `http://<MAC_MINI_IP>:8001/launchpad`

| Launchpad button | What it opens |
| --- | --- |
| User prompts | Natural-language device commands and advisory energy or security reviews. |
| Autonomous actions | Scheduled energy, security, and watering recommendations with recorded outcomes. |
| Insights | Learned energy, comfort, irrigation, and electricity-cost analytics. |
| MCP activity | The audited MCP calls behind EcoNest tasks, including tool, agent, timing, and result. |
| Demo | Guided EcoNest workflows and intermediate steps. |
| MySQL Explorer | Read-only operational records and retained sensor data. |
| MySQL relationship map | Visual guide to the live MySQL tables and their relationships. |
| Home Assistant | The live smart-home dashboard and final authority for device states and service calls. |
| ArcadeDB | The room, device, sensor, capability, and relationship graph. |
| Orchestrator API | Interactive API documentation for integration and troubleshooting. |
| System health | Current MySQL and ArcadeDB health status. |

## How it works

![EcoNest workflow: trigger, orchestration, specialist agent, MCP data, read-only answer or safety-gated device action, and audit record](docs/econest-workflow.svg)

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

## Gemma prompts and when they run

EcoNest uses the local **Gemma 3 4B** model through Ollama for bounded
interpretation, routing, and explanations. The prompts below show the actual
instructions sent to the model; values in angle brackets are filled with the
current task data. Secrets, Home Assistant tokens, passwords, and raw audit
records are never included.

Structured prompts also include a system instruction generated from the
relevant Pydantic response schema: `Respond with valid JSON matching this
schema. Output ONLY JSON.` This lets EcoNest validate the model's answer before
using it.

### Manual Command Center interpretation

**Trigger:** A signed-in user enters a request in **User actions** and selects
**Understand command**. EcoNest first reads the live device inventory through
MCP, selects the most relevant entries, and sends this prompt. It only
interprets the request; no device action happens until the user confirms.

```text
Interpret this smart-home request. First choose request_kind:
energy_recommendation when the user asks about energy use, efficiency, power,
cost, or savings; security_recommendation when they ask for a security
assessment, safety advice, or suspicious activity review; and device_control
only when they request a concrete device action. Energy and security
recommendation requests are advisory: return their request_kind with null
entity_id and action.

For device_control, select an entity_id and action ONLY from the supplied
device inventory. Do not invent an entity, action, or value. Never ask the user
for an entity ID or other technical identifier. Treat singular and plural
device wording as equivalent. When one device is the clear name-and-room match,
select it even if the wording is not identical to its friendly name. If a
device command is ambiguous, lacks a required value, or does not match one
listed device, return null for entity_id and action and write a short
clarification question. set_brightness requires brightness (0-100);
set_temperature requires temperature. This only prepares a confirmation; it
does not control a device.

User command: <user text>
Device inventory: <matching live Home Assistant entities and allowed actions>
```

### Agent routing after a confirmed request

**Trigger:** A user confirms a Command Center interpretation, or EcoNest
submits an internal task. This small routing prompt selects one specialist
agent; it does not decide whether a device is allowed to change.

```text
Classify this smart-home task into exactly one category: energy, security,
sensor, device, multi, or unknown.

Use the payload as the strongest signal. If the payload asks for a concrete
device action such as turn_on, turn_off, set_brightness, open, close, or
includes a controllable domain like light or switch, classify it as device even
if the text also mentions motion or security.

Intent: <task intent>
Payload: <approved task fields>
Metadata: <source and role metadata>
```

If Gemma is unavailable or returns low confidence, EcoNest uses deterministic
routing rules. This fallback routes work; it never authorizes an unsafe action.

### Energy recommendation prompt

**Trigger:** An energy review submitted from the Autonomy page with **Request
energy review**, when the review has a measured anomaly, schedule violation, or
tariff forecast. It is recommendation-only. A Command Center energy request
currently uses the same Energy Agent but remains deterministic unless its task
explicitly enables LLM analysis.

Template: [`orchestrator/llm/prompts/energy.j2`](orchestrator/llm/prompts/energy.j2)

```text
You are EcoNest's energy optimization agent.

Intent: <energy-review request>
Pricing snapshot: <measured tariff context, or "unknown">
Detected anomalies: <measured energy anomalies>
Schedule violations: <detected schedule conflicts>
Current deterministic recommendations: <evidence-based candidates>

Return one concise, recommendation-only suggestion for reducing waste or using
a lower-price period only when the supplied pricing snapshot establishes one.
Ground every claim in the supplied snapshots and deterministic recommendations.
If pricing is unknown, do not mention a cheap, peak, off-peak, or lower-price
window. Never issue a device command, claim a fixed peak schedule, or imply
that EcoNest will switch a device on or off.
```

### Security and sensor assessment prompts

**Trigger:** A Security Agent or Sensor Agent task with `use_llm=true`.
These are analysis-only prompts and are not used to control a device. The
current Command Center security-review path is deterministic unless a caller
explicitly enables this LLM assessment.

Security template: [`orchestrator/llm/prompts/security.j2`](orchestrator/llm/prompts/security.j2)

```text
You are EcoNest SecurityAgent.
Context: <task context>
Recent observations: <security observations>
Detected anomalies: <security incidents>
Current severity: <LOW, MEDIUM, or HIGH>

Provide:
1. Security assessment
2. Likely explanation
3. Recommended action

Be concise.
```

Sensor template: [`orchestrator/llm/prompts/sensor.j2`](orchestrator/llm/prompts/sensor.j2)

```text
You are EcoNest SensorAgent.
Context: <task context>
Sensor observations: <sensor observations>
Detected issues: <sensor health issues>

Provide:
1. Sensor health assessment
2. Likely cause of issues
3. Calibration recommendations
4. Maintenance recommendations

Be concise.
```

### Scheduled autonomous-action decision

**Trigger:** The background monitor at its configured interval. This is the
only prompt that considers a possible automatic action. It receives a current
home snapshot plus the allowed entity/action lists. Before Gemma sees the
prompt, EcoNest's policy code prepares an unambiguous list of safe light
turn-off targets. After Gemma answers, policy, capability, live-state, and
Home Assistant result verification still run before any action executes.

```text
You are EcoNest's autonomous smart-home policy model. Decide whether EcoNest
should execute exactly one low-risk action now.

Safety rules:
- Only recommend an action if confidence is high.
- Only use allowed actions and entities.
- Do not recommend climate, lock, garage, cover, alarm, or security actions.
- The Pre-validated safe turn-off targets list was produced by EcoNest's
  deterministic allowlist, state, and motion checks. It is not ambiguous. If
  that list is non-empty, you MUST choose its first target: return
  should_act=true, domain=light, action=turn_off, that entity_id,
  risk_level=LOW, and confidence at least 0.85.
- Prefer doing nothing if context is ambiguous.

Allowed actions: <configured allowlist>
Allowed entities: <configured entity allowlist>
Pre-validated safe turn-off targets: <policy-approved targets>
Feedback JSON: <current snapshot and measured feedback>

Return JSON only.
```

If this model call fails, EcoNest may use a narrowly scoped policy fallback for
an already pre-validated light turn-off target. MCP and Home Assistant checks
are still mandatory, and the Autonomy page labels the recommendation source.

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

### Use the Apple GPU for Gemma on macOS

Docker Desktop does not pass the Apple GPU through to the Ollama container. To
run EcoNest's Gemma model on the Mac's Metal GPU while keeping the orchestrator
and databases in Docker:

1. Start the native Ollama macOS app and run `ollama pull gemma3:4b` on the Mac.
2. Check that the orchestrator container can reach it:
   `docker exec econest-real-orchestrator python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/version', timeout=5).read().decode())"`.
3. Add `OLLAMA_URL=http://host.docker.internal:11434` to the ignored local
   `.env` file. Keep `OLLAMA_MODEL=gemma3:4b` and
   `OLLAMA_FALLBACK_MODEL=gemma3:4b`.
4. Recreate **only** the orchestrator so Compose applies the new environment:
   `docker compose -f docker-compose.real.yml up -d --no-deps orchestrator`.
   A plain `restart` does not apply Compose environment changes.
5. Send a safe read-only prompt, then run `ollama ps` on the Mac. Its
   `PROCESSOR` column should report GPU use. Compare the response latency with
   the CPU baseline; GPU acceleration alone does not guarantee a particular
   end-to-end speedup.

The existing Docker Ollama service remains installed as a fallback. To revert,
remove the `OLLAMA_URL` line from `.env` and recreate only the orchestrator as
in step 4. This does not delete models or database volumes. Native Ollama
defaults to listening on localhost; do not expose its unauthenticated API to
the public network.

To reproduce the model-only CPU/GPU comparison shown at `/benchmarks`, run
`python3 scripts/benchmark_ollama_backends.py` on the Mac, then refresh the
report page. It sends the same generic, read-only prompt to Docker Ollama on
port 11435 and native Ollama on port 11434, measuring one cold request and
three warm requests on each. It temporarily unloads the model to measure cold
starts; the next live request may need to load it again. These timings do not
include EcoNest routing, databases, Home Assistant, or browser rendering.

For an advisory workflow comparison that includes EcoNest routing, MCP data
retrieval, model generation, and result polling, run:

```bash
docker exec econest-real-orchestrator poetry run python /app/scripts/benchmark_workflow_backends.py --samples 3 --output-dir /app/econest_exports/benchmarks
```

This creates temporary loopback-only API processes with background monitors,
ingestion, and device actions disabled; it does not switch the live server.
It asks only for an energy recommendation and stores timings without the
household answer. The test preloads the model before each request, briefly
unloading the native GPU model during the CPU phase, then restores it.
The benchmark page also shows browser-side timings from the most recent
completed User Prompts request in *that browser*. Those timings stay in browser
storage and are not a controlled CPU/GPU comparison.

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
