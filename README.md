# Agentic System Lab

Repository layout:

```text
agentic-system-lab/
  project1_multi_agent_return_bot/
  project2_agent_orchestrator/
  project3_adaptive_eval_system/
  project4_agentic_project_copilot/
  project5_agentic_metadata_demo/
  project6_autonomous_eval_agent/
  project7_video_provider_mock/
  docs/
  prompts/
```

## Projects

- `project1_multi_agent_return_bot`: LLM-backed e-commerce return chatbot.
- `project2_agent_orchestrator`: LLM-backed customer-support agent orchestrator.
- `project3_adaptive_eval_system`: LLM-backed adaptive evaluation harness.
- `project4_agentic_project_copilot`: orchestrated RAG, safe text-to-SQL, and
  confirmed project API tool-use copilot.
- `project5_agentic_metadata_demo`: deterministic metadata microservice with an
  optional agentic natural-language layer that uses the service endpoints as
  tools.
- `project6_autonomous_eval_agent`: bounded autonomous agent loop for adaptive
  evaluation repair, regression artifacts, benchmark gates, and candidate prompt
  files.
- `project7_video_provider_mock`: local mock video-generation provider with
  async job state, webhook callbacks, and signed event delivery for backend
  practice.

Shared architecture notes live in `docs/`. Project-specific implementation and
tests live inside each project folder so PRs can coexist without competing over a
root-level `app/` package.

## Docs

- `docs/architecture.md`: current architecture for Projects 1-7, including
  project-specific boundaries and comparison matrix.
- `docs/tradeoffs.md`: cross-project design tradeoffs and non-goals.
- `docs/example_conversations.md`: representative request/response traces.
- `docs/latency_benchmark.md`: local latency/concurrency benchmark strategy,
  tooling, and interpretation guidance.
- `docs/observability.md`: local Grafana/Loki stack, structured agent logs, and
  the safe read-only `query_agent_logs` tool.
- `docs/project4_rag_pipeline.md`: Project 4 RAG pipeline, document
  persistence, document library, retrieval, citations, and index freshness.
- `docs/project4_rag_chunking_freshness.md`: Project 4 chunking benchmark and
  SQLite event-driven embedding refresh notes.
- `project4_agentic_project_copilot/docs/architecture.md`: retrieval and
  tool-use copilot architecture.
- `project5_agentic_metadata_demo/README.md`: metadata REST service, agentic
  access layer, structured task endpoint, and MCP adapter workflow.
- `project6_autonomous_eval_agent/docs/architecture.md`: autonomous evaluation
  loop architecture.
- `project7_video_provider_mock/README.md`: mock video provider API and webhook
  workflow.
