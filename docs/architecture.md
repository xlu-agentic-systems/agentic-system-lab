# Architecture

## Repository Layout

Projects are isolated by folder so independent PRs can coexist without sharing a
root-level `app/` package:

```text
agentic-system-lab/
  project1_multi_agent_return_bot/
  project2_agent_orchestrator/
  project3_adaptive_eval_system/
  docs/
  prompts/
```

## Project 1: Return Chatbot

The return chatbot is implemented in
`project1_multi_agent_return_bot/app/return_service.py` and exposed at
`POST /returns/chat`.

1. Load session state from `JsonSessionStore`.
2. Run LLM-backed `RoutingAgent` to classify intent and extract structured fields.
3. Ask for clarification if a return request is missing order ID, item ID, or return reason.
4. Run LLM-backed `PlannerAgent` with backend order, item, and policy facts.
5. Execute proposed tool calls through backend validation.
6. Run LLM-backed `QAAgent` to explain approval, rejection, escalation, or policy information.
7. Save updated session state.

Agents are logical roles that call OpenAI through `OpenAILlmClient`. Unit tests
inject `RuleBasedLlmClient` for deterministic offline coverage, but the default
runtime path uses the OpenAI Responses API with Pydantic structured outputs.

## Safety Boundary

Refunds are unsafe writes. The planner can propose `issue_refund`, but
`validate_and_execute_tool` independently verifies order existence, ownership,
item membership, refundable status, exact refund amount, and policy eligibility.

Policy and status questions do not enter refund execution. Refund tool proposals
are normalized out unless the routing intent is a confirmed `return_request` with
the required return context.
