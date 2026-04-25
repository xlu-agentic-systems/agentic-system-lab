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

Policy and status questions do not enter refund execution. Refund tool proposals
are normalized out unless the routing intent is a confirmed `return_request` with
the required return context.

## Project 2: Agent Orchestrator

The support orchestrator is implemented in
`project2_agent_orchestrator/app/service.py` and exposed at `POST /chat`.

1. Load session state from `JsonSessionStore`.
2. Run LLM-backed orchestration to select specialist agents and an execution mode.
3. Merge model-extracted context with deterministic ID extraction.
4. Run selected specialists as `single_agent`, `sequential`, or `parallel`.
5. Add escalation when specialist confidence is low, results disagree, or a sensitive change is requested.
6. Execute only validated safe backend actions automatically.
7. Aggregate specialist results into one customer-facing response.

Project 2 uses the same `OpenAILlmClient` pattern as Project 1. The default
runtime path calls the OpenAI Responses API for the orchestration decision and
for each specialist `AgentOutput`, then the backend converts that LLM-facing
draft into the richer `AgentResult` returned by the API. Tests inject
`RuleBasedLlmClient` and include an explicit assertion that the orchestrator hits
the LLM boundary.

## Shared Safety Boundary

Agents are logical roles that call OpenAI through `OpenAILlmClient`. The model
classifies, plans, proposes actions, and summarizes; backend tools remain
deterministic and own validation, session persistence, execution planning, and
side-effect control.

Refunds and payment changes are unsafe writes. Backend validation independently
verifies order existence, ownership, item membership, exact amounts, and policy
eligibility before any write is executed.
