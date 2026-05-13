# EcoNest Benchmark Suite

Performance benchmarking infrastructure for the EcoNest orchestrator and agentic smart-home platform.

## Benchmarks

### benchmark_llm.py

Measures Ollama inference latency for edge reasoning models.

Benchmarked models:
- Gemma4
- Mistral

Metrics:
- response latency
- response size
- failure handling

---

### benchmark_mcp.py

Measures async MCP orchestration throughput using lightweight mocked tasks.

Metrics:
- tasks/sec
- average task latency
- concurrent worker scalability

---

### benchmark_mcp_tools.py

Measures throughput of real MCP tool handlers with mocked database dependencies.

Metrics:
- MCP handler throughput
- orchestration overhead
- async tool execution latency

---

### benchmark_graph.py

Measures ArcadeDB graph query orchestration performance using mocked graph queries.

Metrics:
- graph query throughput
- async query latency
- synthetic Gremlin traversal performance

---

## Notes

Current benchmark implementations primarily measure framework/orchestration overhead using mocked infrastructure dependencies.

Future deployment benchmarks on the Mac Mini edge node and Dockerized stack will measure:
- real ArcadeDB latency
- real Ollama inference performance
- end-to-end orchestration latency
- Home Assistant integration throughput