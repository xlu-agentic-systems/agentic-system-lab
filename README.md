# Agentic System Lab

Small FastAPI prototype for a customer-support agent orchestrator.

The project demonstrates an LLM-backed orchestrator-led architecture: the
backend receives a support request, asks an OpenAI model for a structured
orchestration decision, runs one or more specialist agents through the same LLM
boundary, chooses `single_agent`, `sequential`, or `parallel` execution,
validates structured agent outputs, and returns one coherent JSON response.

## Why Orchestration

A fixed multi-agent pipeline runs the same steps every time:

`routing -> return planner -> tools -> answer`

That is easy to test, but wasteful and rigid. A shipping-only question still
passes through return logic, and a mixed payment/return request needs custom
branching.

This prototype uses a central orchestrator instead:

`request -> orchestrator decision -> selected specialists -> aggregation`

The orchestrator owns workflow control. Domain agents stay small and stateless.
The backend owns execution and validation.

## Specialists

- `return_agent`: return and refund eligibility
- `shipping_agent`: package status, delivery delay, tracking
- `payment_agent`: duplicate charge, refund timeline, payment questions
- `account_agent`: profile, address, login questions
- `escalation_agent`: safe human handoff when confidence is low or changes are sensitive

## Run

```bash
pip install -e ".[dev]"
export OPENAI_API_KEY="..."
# optional: export OPENAI_MODEL="gpt-5.5"
uvicorn app.main:app --reload
```

Then post to `http://127.0.0.1:8000/chat`:

```json
{
  "session_id": "demo",
  "user_id": "user-1",
  "message": "I was charged twice and I also want to return the shoes."
}
```

## Response Shape

The response includes:

- `selected_agents`
- `execution_plan`
- `agent_results`
- `final_response`
- `reasoning`
- `trace`

Agents propose actions. The backend only auto-executes validated safe actions,
such as support ticket creation. Unsafe payment/account changes remain proposed.

## Tests

```bash
pytest -q
```

Coverage includes routing, aggregation, policy checks, and backend refund safety.
Unit tests inject a deterministic `RuleBasedLlmClient`; the default runtime path
uses `OpenAILlmClient` and makes real Responses API calls.
