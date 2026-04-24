# Agentic System Lab

Small FastAPI prototype for agentic customer-support workflows.

The main interview project in this branch is a multi-agent e-commerce return
chatbot available at `POST /returns/chat`. It demonstrates routing, planning,
Q&A response generation, backend tool proposals, session persistence, and safe
refund validation.

The repository also keeps the existing `/chat` support orchestrator endpoint for
comparison.

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
uvicorn app.main:app --reload
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

The LLM-style agents only propose tool calls. Refund execution is guarded by
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

Coverage includes routing, aggregation, policy checks, and backend refund safety.

## Docs

- `docs/architecture.md`: architecture, sequence diagram, safety boundary
- `docs/example_conversations.md`: example single, sequential, and parallel traces
- `docs/project_notes.md`: rationale, current no-LLM runtime model, and workflow notes
