# Example Conversation Traces

These examples show expected highlights rather than exact model wording. Exact
customer-facing text can vary because the runtime path uses real LLM calls.

## Project 1: Fixed Return Pipeline

Request:

```json
{
  "session_id": "return-approved",
  "user_id": "user-1",
  "message": "I want to return item-1 from order-1001 because it is damaged and get a refund"
}
```

Expected architecture highlights:

```json
{
  "flow": ["RoutingAgent", "PlannerAgent", "BackendTools", "QAAgent"],
  "routing": {
    "intent": "return_request",
    "order_id": "order-1001",
    "item_id": "item-1"
  },
  "planner": {
    "status": "approved"
  },
  "tool_results": [
    "get_order: executed",
    "get_return_policy: executed",
    "check_refund_eligibility: executed",
    "issue_refund: executed after backend validation"
  ]
}
```

Request with missing fields:

```json
{
  "session_id": "return-clarify",
  "user_id": "user-1",
  "message": "I need a refund"
}
```

Expected highlights:

```json
{
  "planner_status": "needs_clarification",
  "missing_fields": ["order_id", "item_id", "return_reason"],
  "unsafe_tools_executed": false
}
```

## Project 2: Orchestrator

### Single Agent

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

### Parallel Multi-Agent

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

### Sequential Multi-Agent

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

## Project 3: Adaptive Evaluation

CLI:

```bash
python3 -m project3_adaptive_eval_system.app.cli evaluate --limit 3
```

Expected highlights:

```json
{
  "evaluated": 3,
  "generated_tests": 2,
  "prompt_patches": 2,
  "artifacts": [
    "project3_adaptive_eval_system/generated_tests/regression_cases.jsonl",
    "project3_adaptive_eval_system/generated_tests/test_generated_regressions.py",
    "project3_adaptive_eval_system/prompt_patches.jsonl",
    "project3_adaptive_eval_system/evaluation_report.md"
  ]
}
```

Prompt patch review:

```json
{
  "patch_id": "patch-refund-ownership-gate-001",
  "approved": true
}
```

Expected behavior:

```json
{
  "status_transition": "proposed -> approved",
  "production_prompt_auto_applied": false
}
```
