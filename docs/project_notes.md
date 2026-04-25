# Project Notes

## Current Runtime Model

Project 1 calls OpenAI models at runtime. The return-chatbot agents use
`OpenAILlmClient`, which calls the Responses API and parses Pydantic structured
outputs:

- `POST /returns/chat`: a fixed return-chatbot pipeline

Set `OPENAI_API_KEY` before running the API. `OPENAI_MODEL` defaults to
`gpt-5.5` and can be overridden for evals or cost/latency tradeoffs.

Unit tests inject `RuleBasedLlmClient` instead of calling the network. That is
intentional for the interview-focused version:

- The production code path is genuinely LLM-backed.
- Routing and aggregation are deterministic enough for unit tests.
- The backend control boundary is explicit.
- The same Pydantic contracts validate model outputs and test doubles.

## Where Architecture Notes Belong

Use `docs/` for design and workflow notes. It keeps the repository readable:

- `README.md`: quick start, purpose, and high-level explanation.
- `docs/architecture.md`: system design, sequence diagram, safety boundaries.
- `docs/example_conversations.md`: representative request/response traces.
- `docs/project_notes.md`: implementation rationale and agentic coding workflow.

Avoid mixing architecture rationale into source files unless it explains a
non-obvious local code decision.

## Agentic Coding Workflow

1. Clarify the product goal and safety constraints.
2. Read the existing repo before editing.
3. Make the smallest architecture that demonstrates the target behavior.
4. Keep agents stateless and domain-specific.
5. Put workflow control in backend orchestration code.
6. Require structured outputs from agents.
7. Validate all proposed actions before execution.
8. Add trace events for decisions and handoffs.
9. Write tests around routing, execution mode, aggregation, and safety.
10. Update docs with the architecture tradeoffs and example traces.

## Fixed Pipeline vs Orchestrator

A fixed pipeline is useful when every request should go through the same stages.
For example, a returns-only bot can do:

```text
route -> plan -> tool validation -> answer
```

That is represented by the return chatbot at `POST /returns/chat`.

The orchestrator is useful when the request can span domains:

```text
request -> select specialists -> choose execution mode -> aggregate -> respond
```

That is represented by the Project 2 support orchestrator workstream.

The important difference is ownership of workflow. In this project, the backend
orchestrator decides which agents run and how they run. The agents do not call
each other, execute irreversible tools, or own session state.

## LLM Integration

The LLM receives:

- Input: `message`, `user_id`, loaded session context, and relevant backend/tool facts.
- Output: validated Pydantic objects such as `RoutingOutput`, `PlannerOutput`,
  `AgentResult`, and `QAOutput`.
- Execution: backend invokes tools after policy validation.

The LLM should help classify, summarize, and reason over ambiguous customer
language. It should not directly own tool execution, persistence, or cross-agent
workflow.
