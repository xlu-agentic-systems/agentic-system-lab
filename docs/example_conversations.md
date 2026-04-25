# Example Conversation Traces

## Single Agent

```json
{
  "session_id": "trace-single",
  "user_id": "user-1",
  "message": "Where is package order-1004?"
}
```

Expected highlights:

```json
{
  "selected_agents": ["shipping_agent"],
  "execution_plan": {"mode": "single_agent"},
  "agent_results": ["shipping_agent: shipment_delayed"]
}
```

## Parallel Multi-Agent

```json
{
  "session_id": "trace-parallel",
  "user_id": "user-1",
  "message": "I was charged twice and I also want to return the shoes."
}
```

Expected highlights:

```json
{
  "selected_agents": ["return_agent", "payment_agent"],
  "execution_plan": {"mode": "parallel"},
  "agent_results": ["return_agent: return_eligible", "payment_agent: duplicate_charge_found"]
}
```

## Sequential Multi-Agent

```json
{
  "session_id": "trace-sequential",
  "user_id": "user-1",
  "message": "My package order-1004 is delayed and I want a refund status update."
}
```

Expected highlights:

```json
{
  "selected_agents": ["shipping_agent", "payment_agent"],
  "execution_plan": {"mode": "sequential"},
  "agent_results": [
    "shipping_agent: shipment_delayed",
    "payment_agent: refund_timeline",
    "escalation_agent: human_ticket_recommended"
  ]
}
```
