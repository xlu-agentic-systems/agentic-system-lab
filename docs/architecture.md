# Architecture

## Return Chatbot Core Flow

The return chatbot is implemented in `app/return_service.py` and exposed at
`POST /returns/chat`.

1. Load session state from `JsonSessionStore`.
2. Run LLM-backed `RoutingAgent` to classify intent and extract structured fields.
3. Ask for clarification if order ID, item ID, or return reason is missing.
4. Run LLM-backed `PlannerAgent` with backend order, item, and policy facts.
5. Execute proposed tool calls through backend validation.
6. Run LLM-backed `QAAgent` to explain approval, rejection, escalation, or next steps.
7. Save updated session state.

Agents are logical roles that call OpenAI through `OpenAILlmClient`. They are
instantiated per service process and do not hold user session state. Session
context is keyed by `session_id` and loaded on each request.

Structured model outputs are parsed into Pydantic models, so the backend receives
typed `RoutingOutput`, `PlannerOutput`, `AgentResult`, and `QAOutput` objects
instead of free-form text.

## Safety

Refunds are unsafe writes. The planner can propose `issue_refund`, but
`validate_and_execute_tool` independently verifies order existence, ownership,
item membership, refundable status, exact refund amount, and policy eligibility.

If validation fails, the tool result has `executed=false` and no refund record is
created.

## Legacy Orchestrator

The orchestrator is implemented in `app/service.py`. It loads session state,
routes the message, builds an execution plan, runs specialist agents, optionally
adds escalation, validates backend actions, aggregates the final answer, and
saves session state.

Domain agents are stateless. They receive `message`, `user_id`, the loaded
session context, and relevant backend facts, then use an LLM call to return a
Pydantic `AgentResult`.

## Sequence Diagram

```mermaid
sequenceDiagram
    participant C as Client
    participant API as FastAPI /chat
    participant O as OrchestratorService
    participant S as JsonSessionStore
    participant A as Specialist Agents
    participant T as BackendTools

    C->>API: POST /chat(session_id, user_id, message)
    API->>O: handle_message(request)
    O->>S: load(session_id, user_id)
    S-->>O: SessionState
    O->>O: select agents and execution mode
    alt single_agent
        O->>A: run one specialist
    else sequential
        O->>A: run specialist 1
        A-->>O: AgentResult
        O->>A: run specialist 2
    else parallel
        par independent specialists
            O->>A: run specialist A
            O->>A: run specialist B
        end
    end
    A->>T: read fake order/payment/shipping/account data
    T-->>A: structured facts
    A-->>O: AgentResult
    opt low confidence, disagreement, or sensitive change
        O->>A: escalation_agent.run(prior_results)
        A-->>O: ticket proposal
        O->>T: validate_and_execute_action(create_support_ticket)
    end
    O->>O: aggregate coherent response
    O->>S: save(updated SessionState)
    O-->>API: ConversationResponse JSON
    API-->>C: selected agents, plan, results, response, trace
```

## Example Traces

### Single Agent

Request:

```text
Where is package order-1004?
```

Trace:

```json
{
  "selected_agents": ["shipping_agent"],
  "execution_plan": {"mode": "single_agent"},
  "agent_results": ["shipping_agent: shipment_delayed"],
  "final_response": "Order order-1004 is delayed with FedEx..."
}
```

### Multi-Agent Parallel

Request:

```text
I was charged twice and I also want to return the shoes.
```

Trace:

```json
{
  "selected_agents": ["return_agent", "payment_agent"],
  "execution_plan": {"mode": "parallel"},
  "agent_results": ["return_agent: return_eligible", "payment_agent: duplicate_charge_found"],
  "final_response": "City Runner Shoes is eligible for return... I found a likely duplicate charge..."
}
```

### Multi-Agent Sequential

Request:

```text
My package order-1004 is delayed and I want a refund status update.
```

Trace:

```json
{
  "selected_agents": ["shipping_agent", "payment_agent"],
  "execution_plan": {"mode": "sequential"},
  "agent_results": [
    "shipping_agent: shipment_delayed",
    "payment_agent: refund_timeline",
    "escalation_agent: human_ticket_recommended"
  ],
  "final_response": "Order order-1004 is delayed... I created support ticket ..."
}
```

## Safety Boundary

Specialist agents do not execute irreversible changes. They return structured
proposals. The orchestrator/backend layer decides whether a proposed action is
allowed. Unsafe payment actions remain pending for approval.

## Session Isolation

`JsonSessionStore` keys state by `session_id`, and each request also includes
`user_id`. Agents do not retain in-memory user state; context is loaded at the
start of a request and saved at the end.
