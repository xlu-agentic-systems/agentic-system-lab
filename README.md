# Agentic System Lab

Repository layout:

```text
agentic-system-lab/
  project1_multi_agent_return_bot/
  project2_agent_orchestrator/
  project3_adaptive_eval_system/
  docs/
  prompts/
```

## Projects

- `project1_multi_agent_return_bot`: LLM-backed e-commerce return chatbot.
- `project2_agent_orchestrator`: LLM-backed customer-support agent orchestrator.
- `project3_adaptive_eval_system`: reserved for the adaptive eval system workstream.

Shared architecture notes live in `docs/`. Project-specific implementation and
tests live inside each project folder so PRs can coexist without competing over a
root-level `app/` package.

## Docs

- `docs/architecture.md`: current architecture for the three implemented projects.
- `docs/tradeoffs.md`: cross-project design tradeoffs and non-goals.
- `docs/mcp_architecture.md`: conceptual MCP host/client/server architecture and
  an e-commerce support example.
