# Agentic System Lab

Repository layout:

```text
agentic-system-lab/
  project1_multi_agent_return_bot/
  project2_agent_orchestrator/
  project3_adaptive_eval_system/
  project4_agentic_project_copilot/
  docs/
  prompts/
```

## Projects

- `project1_multi_agent_return_bot`: LLM-backed e-commerce return chatbot.
- `project2_agent_orchestrator`: LLM-backed customer-support agent orchestrator.
- `project3_adaptive_eval_system`: LLM-backed adaptive evaluation harness.
- `project4_agentic_project_copilot`: orchestrated RAG, safe text-to-SQL, and
  confirmed project API tool-use copilot.

Shared architecture notes live in `docs/`. Project-specific implementation and
tests live inside each project folder so PRs can coexist without competing over a
root-level `app/` package.

## Docs

- `docs/architecture.md`: current architecture for the three implemented projects.
- `docs/tradeoffs.md`: cross-project design tradeoffs and non-goals.
- `docs/example_conversations.md`: representative request/response traces.
- `docs/latency_benchmark.md`: local latency/concurrency benchmark strategy,
  tooling, and interpretation guidance.
- `project4_agentic_project_copilot/docs/architecture.md`: retrieval and
  tool-use copilot architecture.
