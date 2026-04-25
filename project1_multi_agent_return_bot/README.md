# Project 1: Multi-Agent Return Bot

Small FastAPI prototype for an LLM-backed e-commerce return workflow.

The main interview project in this branch is a multi-agent e-commerce return
chatbot available at `POST /returns/chat`. It demonstrates routing, planning,
Q&A response generation, backend tool proposals, session persistence, and safe
refund validation.

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
