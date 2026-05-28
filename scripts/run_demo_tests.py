"""Run an EcoNest Docker demo test suite and render an HTML report.

This script intentionally reads secrets from the current environment and does
not write them to the report. Set HA_TOKEN before running it.
"""

from __future__ import annotations

import argparse
import getpass
import html
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "test_reports"
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")


@dataclass
class StepResult:
    name: str
    status: str
    detail: str
    output: str
    command: str


def redact(text: str, token: str) -> str:
    if token:
        text = text.replace(token, "[REDACTED_HA_TOKEN]")
    return text


def output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_command(
    name: str,
    command: list[str],
    env: dict[str, str],
    token: str,
    detail: str = "",
    timeout: int | None = None,
) -> StepResult:
    display_command = " ".join(command)
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = output_text(exc.stdout) + "\n" + output_text(exc.stderr)
        return StepResult(
            name=name,
            status="fail",
            detail=f"Timed out after {timeout} seconds.",
            output=redact(output, token),
            command=display_command,
        )

    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    return StepResult(
        name=name,
        status="pass" if completed.returncode == 0 else "fail",
        detail=detail,
        output=redact(output.strip(), token),
        command=display_command,
    )


def compose_base() -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.test.yml",
    ]


def render_report(results: list[StepResult], report_path: Path) -> None:
    passed = sum(1 for result in results if result.status == "pass")
    failed = sum(1 for result in results if result.status == "fail")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cards = []
    for result in results:
        output = html.escape(result.output[-6000:] or "(no output)")
        command = html.escape(result.command)
        detail = html.escape(result.detail)
        cards.append(
            f"""
            <section class="step {result.status}">
              <div class="step-head">
                <h2>{html.escape(result.name)}</h2>
                <span>{result.status.upper()}</span>
              </div>
              <p>{detail}</p>
              <details>
                <summary>Command and output</summary>
                <code>{command}</code>
                <pre>{output}</pre>
              </details>
            </section>
            """
        )

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EcoNest Demo Test Report</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1f2933;
      --muted: #64748b;
      --line: #d8dee8;
      --pass: #0f766e;
      --pass-bg: #e6fffa;
      --fail: #b42318;
      --fail-bg: #fff1f0;
      --panel: #ffffff;
      --page: #f6f7f9;
    }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--page);
    }}
    header {{
      padding: 28px 32px 18px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 30px;
      letter-spacing: 0;
    }}
    .summary {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 18px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 16px;
      min-width: 140px;
      background: var(--panel);
    }}
    .metric strong {{
      display: block;
      font-size: 28px;
    }}
    main {{
      max-width: 1100px;
      margin: 24px auto 48px;
      padding: 0 20px;
    }}
    .step {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-left-width: 6px;
      border-radius: 8px;
      margin: 14px 0;
      padding: 18px;
    }}
    .step.pass {{ border-left-color: var(--pass); }}
    .step.fail {{ border-left-color: var(--fail); }}
    .step-head {{
      align-items: center;
      display: flex;
      gap: 16px;
      justify-content: space-between;
    }}
    h2 {{
      margin: 0;
      font-size: 18px;
      letter-spacing: 0;
    }}
    .step-head span {{
      border-radius: 6px;
      font-weight: 700;
      padding: 6px 10px;
    }}
    .pass .step-head span {{
      background: var(--pass-bg);
      color: var(--pass);
    }}
    .fail .step-head span {{
      background: var(--fail-bg);
      color: var(--fail);
    }}
    p {{
      color: var(--muted);
      line-height: 1.5;
    }}
    details {{
      border-top: 1px solid var(--line);
      margin-top: 14px;
      padding-top: 12px;
    }}
    summary {{
      cursor: pointer;
      font-weight: 600;
    }}
    code {{
      display: block;
      margin-top: 12px;
      overflow-wrap: anywhere;
      color: #334155;
    }}
    pre {{
      background: #111827;
      border-radius: 8px;
      color: #f8fafc;
      overflow: auto;
      padding: 14px;
      white-space: pre-wrap;
    }}
  </style>
</head>
<body>
  <header>
    <h1>EcoNest Demo Test Report</h1>
    <p>Generated {html.escape(generated_at)}. Secrets are redacted from this report.</p>
    <div class="summary">
      <div class="metric"><strong>{passed}</strong>Passed</div>
      <div class="metric"><strong>{failed}</strong>Failed</div>
      <div class="metric"><strong>{len(results)}</strong>Total Checks</div>
    </div>
  </header>
  <main>
    {''.join(cards)}
  </main>
</body>
</html>
"""
    REPORT_DIR.mkdir(exist_ok=True)
    report_path.write_text(html_doc, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="Leave the isolated econest-test stack running after the report.",
    )
    args = parser.parse_args()

    token = os.environ.get("HA_TOKEN", "")
    if not token:
        token = getpass.getpass("Home Assistant token: ").strip()
    if not token:
        print("HA_TOKEN must be set in the environment.", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env["HA_TOKEN"] = token
    env.setdefault("OLLAMA_MODEL", MODEL)
    env.setdefault("OLLAMA_FALLBACK_MODEL", env["OLLAMA_MODEL"])

    compose = compose_base()
    results: list[StepResult] = []

    try:
        checks = [
            (
                "Start isolated Docker stack",
                compose + ["up", "-d"],
                "Starts MySQL, ArcadeDB, Ollama, and orchestrator on test ports.",
                300,
            ),
            (
                "Show test stack status",
                compose + ["ps"],
                "Confirms the econest-test containers and published ports.",
                60,
            ),
            (
                "Orchestrator infrastructure health",
                compose
                + [
                    "exec",
                    "-T",
                    "orchestrator",
                    "python",
                    "-c",
                    (
                        "import time, urllib.request\n"
                        "last = None\n"
                        "for _ in range(30):\n"
                        "    try:\n"
                        "        body = urllib.request.urlopen("
                        "'http://127.0.0.1:8000/health', timeout=5"
                        ").read().decode()\n"
                        "        print(body)\n"
                        "        raise SystemExit(0)\n"
                        "    except Exception as exc:\n"
                        "        last = exc\n"
                        "        time.sleep(2)\n"
                        "raise SystemExit(f'health check never became ready: {last}')"
                    ),
                ],
                "Checks FastAPI startup plus MySQL and ArcadeDB connectivity.",
                90,
            ),
            (
                "Real Home Assistant API state list",
                compose
                + [
                    "exec",
                    "-T",
                    "orchestrator",
                    "python",
                    "-c",
                    (
                        "import asyncio, httpx\n"
                        "from orchestrator.config import get_settings\n"
                        "async def main():\n"
                        "    settings = get_settings()\n"
                        "    async with httpx.AsyncClient(timeout=10) as client:\n"
                        "        response = await client.get("
                        "f'{settings.HA_URL}/api/states', "
                        "headers={'Authorization': f'Bearer {settings.HA_TOKEN}'})\n"
                        "        print('status', response.status_code)\n"
                        "        data = response.json() if response.status_code == 200 else []\n"
                        "        print('state_count', len(data) if isinstance(data, list) else 'n/a')\n"
                        "        if isinstance(data, list):\n"
                        "            for item in data[:8]:\n"
                        "                print(item.get('entity_id'), item.get('state'))\n"
                        "asyncio.run(main())"
                    ),
                ],
                "Uses the real HA token for a read-only state list.",
                60,
            ),
            (
                "Real HA MCP tool read",
                compose
                + [
                    "exec",
                    "-T",
                    "orchestrator",
                    "python",
                    "-c",
                    (
                        "import asyncio\n"
                        "from orchestrator.mcp.tools.ha_tools import "
                        "HAGetStateInput, ha_get_state_handler\n"
                        "async def main():\n"
                        "    result = await ha_get_state_handler("
                        "HAGetStateInput(entity_id='person.econest'))\n"
                        "    print({'entity_id': result.get('entity_id'), "
                        "'state': result.get('state'), "
                        "'has_attributes': isinstance(result.get('attributes'), dict)})\n"
                        "asyncio.run(main())"
                    ),
                ],
                "Invokes the real read-only HA MCP handler.",
                60,
            ),
            (
                "Bootstrap HA data into ArcadeDB",
                compose
                + [
                    "exec",
                    "-T",
                    "orchestrator",
                    "python",
                    "scripts/bootstrap_ha_graph.py",
                ],
                "Creates the econest graph database/schema and imports live HA states.",
                300,
            ),
            (
                "Verify ArcadeDB graph counts",
                compose
                + [
                    "exec",
                    "-T",
                    "orchestrator",
                    "python",
                    "-c",
                    (
                        "import asyncio\n"
                        "from orchestrator.core.database import "
                        "init_databases, close_databases, arcadedb_query\n"
                        "async def main():\n"
                        "    await init_databases()\n"
                        "    try:\n"
                        "        for label in ['Home', 'Room', 'Device', 'Sensor', "
                        "'Observation', 'LOCATED_IN', 'MONITORS', 'OBSERVED_IN']:\n"
                        "            result = await arcadedb_query("
                        "'sql', f'SELECT count(*) AS count FROM {label}')\n"
                        "            print(label, result.get('result'))\n"
                        "    finally:\n"
                        "        await close_databases()\n"
                        "asyncio.run(main())"
                    ),
                ],
                "Shows imported HA-derived vertices and graph relationships.",
                120,
            ),
            (
                "Pull small Ollama demo model",
                compose
                + [
                    "exec",
                    "-T",
                    "ollama",
                    "ollama",
                    "pull",
                    env["OLLAMA_MODEL"],
                ],
                "Ensures a small local model is available for live inference.",
                600,
            ),
            (
                "Direct Ollama inference",
                compose
                + [
                    "exec",
                    "-T",
                    "orchestrator",
                    "python",
                    "-c",
                    (
                        "import asyncio, httpx, os\n"
                        "async def main():\n"
                        "    async with httpx.AsyncClient(timeout=120) as client:\n"
                        "        response = await client.post("
                        "'http://ollama:11434/api/generate', "
                        "json={'model': os.environ['OLLAMA_MODEL'], "
                        "'prompt': 'Reply with one short sentence saying the EcoNest LLM test passed.', "
                        "'stream': False, 'options': {'num_predict': 24, 'temperature': 0}})\n"
                        "        response.raise_for_status()\n"
                        "        print(response.json().get('response', '').strip())\n"
                        "asyncio.run(main())"
                    ),
                ],
                "Calls the real Ollama HTTP API from the orchestrator network.",
                180,
            ),
            (
                "Orchestrator LLM client inference",
                compose
                + [
                    "exec",
                    "-T",
                    "orchestrator",
                    "python",
                    "-c",
                    (
                        "import asyncio\n"
                        "from orchestrator.llm.client import LLMClient\n"
                        "async def main():\n"
                        "    client = LLMClient()\n"
                        "    try:\n"
                        "        result = await client.generate("
                        "'Reply in one short sentence saying the orchestrator LLM path passed.', "
                        "temperature=0)\n"
                        "        print(result.strip())\n"
                        "    finally:\n"
                        "        await client.close()\n"
                        "asyncio.run(main())"
                    ),
                ],
                "Verifies the orchestrator can call Ollama through its LLM client.",
                180,
            ),
            (
                "Combined HA plus LLM workflow",
                compose
                + [
                    "exec",
                    "-T",
                    "orchestrator",
                    "python",
                    "-c",
                    (
                        "import asyncio\n"
                        "from orchestrator.llm.client import LLMClient\n"
                        "from orchestrator.mcp.tools.ha_tools import "
                        "HAGetStateInput, ha_get_state_handler\n"
                        "async def main():\n"
                        "    ha = await ha_get_state_handler("
                        "HAGetStateInput(entity_id='person.econest'))\n"
                        "    client = LLMClient()\n"
                        "    try:\n"
                        "        prompt = ("
                        "f\"Home Assistant entity {ha.get('entity_id')} is currently "
                        "{ha.get('state')}. Summarize this for an EcoNest operator "
                        "in one short sentence.\")\n"
                        "        text = await client.generate(prompt, temperature=0)\n"
                        "        print({'ha_entity': ha.get('entity_id'), "
                        "'ha_state': ha.get('state'), "
                        "'llm_summary': text.strip()[:400]})\n"
                        "    finally:\n"
                        "        await client.close()\n"
                        "asyncio.run(main())"
                    ),
                ],
                "Feeds a live HA state into the real orchestrator LLM path.",
                180,
            ),
            (
                "Automated pytest suite",
                compose
                + [
                    "run",
                    "--rm",
                    "--no-deps",
                    "orchestrator",
                    "poetry",
                    "run",
                    "poe",
                    "test",
                ],
                "Runs the current orchestrator automated tests.",
                300,
            ),
            (
                "Ruff lint",
                compose
                + [
                    "run",
                    "--rm",
                    "--no-deps",
                    "orchestrator",
                    "poetry",
                    "run",
                    "poe",
                    "lint",
                ],
                "Runs static lint checks.",
                180,
            ),
        ]

        for name, command, detail, timeout in checks:
            print(f"==> {name}")
            result = run_command(name, command, env, token, detail, timeout)
            print(f"    {result.status.upper()}")
            results.append(result)
    finally:
        if not args.keep_running:
            print("==> Stop isolated Docker stack")
            results.append(
                run_command(
                    "Stop isolated Docker stack",
                    compose + ["down"],
                    env,
                    token,
                    "Stops only the econest-test stack. Home Assistant is not touched.",
                    180,
                )
            )

    report_path = REPORT_DIR / (
        "econest_demo_report_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".html"
    )
    render_report(results, report_path)
    failed = sum(1 for result in results if result.status == "fail")
    print(f"\nReport written to: {report_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
