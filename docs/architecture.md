# Architecture

## Core Flow

The orchestrator is implemented in `app/service.py`. It loads session state,
routes the message through an OpenAI-backed structured-output client, builds an
execution plan, runs specialist agents, optionally adds escalation, validates
backend actions, aggregates the final answer, and saves session state.

Domain agents are stateless. They receive `message`, `user_id`, and the loaded
session context, fetch backend facts, then ask the LLM to return a Pydantic
`AgentResult`. Tests inject `RuleBasedLlmClient` for deterministic offline
coverage; production services default to `OpenAILlmClient`, which calls the
OpenAI Responses API.

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
    O->>O: LLM selects agents and execution mode
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
