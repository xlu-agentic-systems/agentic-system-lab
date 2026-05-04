# Project Notes

## Current Runtime Model

The LLM-backed projects call OpenAI models at runtime through `OpenAILlmClient`,
which calls the Responses API and parses Pydantic structured outputs:

- `POST /returns/chat`: a fixed return-chatbot pipeline
- `POST /chat` in Project 2: an orchestrator-led support flow
- `POST /evaluations/run`: an out-of-band adaptive evaluation flow
- `POST /chat` in Project 4: a project copilot that routes across RAG,
  read-only SQL, project API tools, context, and clarification
- `POST /agent/query` and `POST /agent/tasks` in Project 5: an agentic access
  layer over deterministic metadata service tools
- `POST /autonomous-runs` in Project 6: a bounded autonomous evaluation loop
- Project 7 is not LLM-backed. It is a local mock video provider for backend
  integration practice.

Set `OPENAI_API_KEY` before running the API. The repo includes `.env.example`;
copy it to `.env`, fill the key, and source it before starting FastAPI:

```bash
set -a
source .env
set +a
```

The application does not auto-load `.env`. Projects 1-4 and 6 default
`OPENAI_MODEL` to `gpt-5.5`; Project 5's optional tool-calling agent defaults to
`gpt-4.1-mini`. The model can be overridden for evals or cost/latency tradeoffs.

Unit tests inject `RuleBasedLlmClient` instead of calling the network. That is
intentional for the interview-focused version:

- The production code path is genuinely LLM-backed.
- Routing and aggregation are deterministic enough for unit tests.
- The backend control boundary is explicit.
- The same Pydantic contracts validate model outputs and test doubles.

## Where Architecture Notes Belong

Use `docs/` for design and workflow notes. It keeps the repository readable:

- `README.md`: quick start, purpose, and high-level explanation.
- `docs/architecture.md`: system design, sequence diagrams, safety boundaries,
  and project-by-project architecture review.
- `docs/tradeoffs.md`: cross-project architecture tradeoffs and non-goals.
- `docs/latency_benchmark.md`: local benchmark strategy for comparing latency,
  caller concurrency, and architecture tradeoffs.
- `docs/project4_rag_pipeline.md`: Project 4 upload, persistence, retrieval,
  citation, document-library, and event-driven index freshness flow.
- `docs/project4_rag_chunking_freshness.md`: Project 4 chunking benchmark and
  freshness mechanism.
- `project4_agentic_project_copilot/docs/architecture.md`: the retrieval,
  text-to-SQL, and project-tool copilot pattern.
- `project6_autonomous_eval_agent/docs/architecture.md`: the bounded autonomous
  evaluation loop.
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

That is represented by the Project 2 support orchestrator at `POST /chat`.

The important difference is ownership of workflow. In this project, the backend
orchestrator decides which agents run and how they run. The agents do not call
each other, execute irreversible tools, or own session state.

## Project 1 Session Storage

Project 1 separates short-lived active session state from durable trace records.
The local prototype uses `JsonSessionStore` with TTL metadata for active
sessions and `JsonlTraceStore` for per-turn audit traces. In production, the
same shape should map to Redis or another low-latency key-value store for active
sessions, plus Postgres, DynamoDB, MongoDB, or a data lake table for durable
conversation traces. A vector database should not be the source of truth for
active session lifecycle.

Project 2 specialists receive backend facts before model calls. The model
summarizes and proposes actions, but the backend still decides execution order,
escalation insertion, safe action execution, and final aggregation.

## Adaptive Evaluation

Project 3 treats improvement as a harness around production systems, not as
self-modifying production behavior. Completed traces are reviewed after the fact,
failures become regression tests, and prompt changes remain proposed until a
human approves them.

The current Project 3 generated regressions replay two concrete Project 1
failure classes: delivery-date return eligibility and refund ownership gating.
New failure classes should add similarly concrete replay helpers rather than only
checking that generated JSON exists.

## Project 4 RAG Pipeline

Project 4 is still an orchestrated copilot, not a pure RAG app. The RAG path is
used for uploaded documents, while separate paths handle read-only SQL, project
API tool calls, session context, and clarification.

Uploaded documents are persisted in local SQLite as document metadata, extracted
text chunks, and JSON embeddings. The original file bytes are not stored. The UI
document library can list, select, and delete stored documents. Insert and delete
operations enqueue `document_index_events`; the app processes pending events to
refresh only affected chunk embeddings. This is still simple and inspectable for
the MVP while avoiding full-corpus reindexing after every document change.

For the detailed sequence, see `docs/project4_rag_pipeline.md`.

## Project 5 Metadata Boundary

Project 5 makes sense as an agentic access-layer demo, not as a replacement for
traditional metadata services. The metadata REST API, SQLAlchemy models, SQLite
database, and `auth.py` policy checks stay deterministic. The natural-language
agent, structured task endpoint, and MCP adapter all use `MetadataTools`, which
calls the same REST API with the caller identity attached.

The key review criterion for future changes is whether a new agent capability
still goes through service policy. Avoid adding raw SQL, filesystem, or
privileged MCP tools unless they are explicitly wrapped by deterministic checks.

## Project 6 Autonomy Boundary

Project 6 is a bounded loop around Project 3 artifacts. It is useful because the
planner can choose the next evaluation repair step, but the host owns tool
execution, `max_iterations`, acceptance gates, and output locations.

Future Project 6 changes should preserve the distinction between review
artifacts and production behavior. Candidate prompts belong under the per-run
directory until a human reviews, tests, and merges them.

## Project 7 Provider Mock Boundary

Project 7 fills a different role from the agentic projects. It is local
integration infrastructure for practicing async provider workflows. The mock
state machine, asset store, webhook endpoint registry, signed deliveries, and
delivery history are useful for backend practice, but they are process-local and
not production-safe.

Future Project 7 changes should keep that local-provider boundary explicit:
document any new endpoint, keep default credentials out of public deployments,
and add tests for provider-shaped behavior such as signatures, retry semantics,
pagination, or state transitions when those behaviors are introduced.

## LLM Integration

The LLM receives:

- Input: `message`, `user_id`, loaded session context, and relevant backend/tool facts.
- Output: validated Pydantic objects such as `OrchestratorDecision`,
  `RoutingOutput`, `PlannerOutput`, Project 2 LLM-facing `AgentOutput`, backend
  `AgentResult`, `QAOutput`, `EvaluationResult`, `GeneratedTestCase`, and
  `PromptPatch`.
- Execution: backend invokes tools after policy validation.

The LLM should help classify, summarize, and reason over ambiguous customer
language. It should not directly own tool execution, persistence, or cross-agent
workflow.
