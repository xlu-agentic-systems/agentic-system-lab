# Project 1: Multi-Agent Return Bot

Small FastAPI prototype for an LLM-backed e-commerce return workflow.

The main interview project in this branch is a multi-agent e-commerce return
chatbot available at `POST /returns/chat`. It demonstrates routing, planning,
Q&A response generation, backend tool proposals, active session persistence,
durable trace capture, and safe refund validation.

The runtime agents call OpenAI models through the Responses API with Pydantic
structured outputs. Unit tests inject `RuleBasedLlmClient` so tests stay
deterministic and do not require network access.

## Return Chatbot Flow

`request -> Routing Agent -> Planner Agent -> validated backend tools -> Q&A Agent`

- `RoutingAgent`: classifies intent, extracts `order_id`, `item_id`, return
  reason, and refund request state.
- `PlannerAgent`: checks order data and return policy, then proposes tool calls.
- `QAAgent`: turns planner decisions and tool results into customer-facing
  support replies.
- `BackendTools`: validates every proposed tool call before execution.

## Session And Trace Storage

Project 1 now separates active conversation state from durable audit traces.

Active sessions are stored by `JsonSessionStore` in:

```text
project1_multi_agent_return_bot/data/sessions.json
```

This prototype store behaves like a small file-backed equivalent of a Redis
session cache:

- sessions are keyed by `user_id:session_id`
- sessions carry `created_at`, `updated_at`, `expires_at`, and `status`
- the default TTL is 24 hours
- expired sessions load as fresh empty sessions
- the same `session_id` cannot leak history across different `user_id` values

Durable per-turn traces are stored by `JsonlTraceStore` in:

```text
project1_multi_agent_return_bot/data/conversation_traces.jsonl
```

Each trace records the user message, routing output, planner output, validated
tool results, and final response. In production, the analogous design would use
Redis or another low-latency key-value store for active sessions, and a durable
database such as Postgres, DynamoDB, MongoDB, or a data lake table for traces.
A vector database is not the source of truth for active sessions; it is only
useful for semantic retrieval over policies, knowledge base content, or older
conversation summaries.

## Run

```bash
pip install -e ".[dev]"
cp .env.example .env
# fill OPENAI_API_KEY in .env
set -a
source .env
set +a
uvicorn project1_multi_agent_return_bot.app.main:app --reload
```

Then post to `http://127.0.0.1:8000/returns/chat`:

```json
{
  "session_id": "demo",
  "user_id": "user-1",
  "message": "I want to return item-1 from order-1001 because it is damaged and get a refund"
}
```

## Safety Boundary

The LLM agents only propose tool calls. Refund execution is guarded by
backend validation in `app/tools.py`, which verifies:

- the order exists
- the authenticated user owns the order
- the item belongs to the order
- the item is refundable
- the refund amount matches order data
- the return policy allows the refund

Unsafe proposals are returned as blocked tool results and no refund record is
created.

## Tests

```bash
pytest -q
```

Coverage includes routing, policy checks, structured-output wiring, and backend
refund safety. Unit tests use `RuleBasedLlmClient`; run the API with `.env`
sourced to exercise real OpenAI calls.

## Docs

- `docs/architecture.md`: architecture, sequence diagram, safety boundary
- `docs/example_conversations.md`: example single, sequential, and parallel traces
- `docs/project_notes.md`: rationale, LLM runtime model, and workflow notes
