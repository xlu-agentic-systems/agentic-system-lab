# Agentic System Architecture

This repo contains three small agentic-system prototypes. They share the same
runtime conventions but intentionally demonstrate different architecture shapes:

1. **Project 1:** fixed multi-agent return pipeline
2. **Project 2:** orchestrator-led support routing
3. **Project 3:** out-of-band adaptive evaluation harness

The implementations are deliberately small and interview-focused. The agents are
logical roles invoked per request, not long-running processes. Backend code owns
state, tool execution, validation, persistence, and side effects.

## Shared Runtime Pattern

All three projects use these common boundaries:

- **LLM boundary:** `OpenAILlmClient` calls the OpenAI Responses API with
  Pydantic structured outputs. Unit tests inject `RuleBasedLlmClient`.
- **State boundary:** request handlers load state or traces from local storage,
  run agent roles, then write updated state/artifacts back.
- **Tool boundary:** agents propose actions; backend code validates and executes
  only allowed side effects.
- **Safety boundary:** model output is treated as advisory until normalized or
  checked by deterministic code.

The `.env` file is not auto-loaded. For local real-LLM runs:

```bash
set -a
source .env
set +a
```

## Project 1: Multi-Agent Return Bot

**Purpose:** demonstrate a fixed, returns-only agent workflow with routing,
planning, tool proposals, backend validation, and final customer messaging.

**Entry point:** `POST /returns/chat`

**Primary files:**

- `project1_multi_agent_return_bot/app/return_service.py`
- `project1_multi_agent_return_bot/app/return_agents.py`
- `project1_multi_agent_return_bot/app/tools.py`
- `project1_multi_agent_return_bot/app/policy.py`

### Architecture

```text
HTTP request
  -> JsonSessionStore.load(session_id, user_id)
  -> RoutingAgent
       output: RoutingOutput
  -> backend routing normalization
       fills missing_fields from merged session context
  -> PlannerAgent
       input: routing + backend order/item/policy facts
       output: PlannerOutput
  -> backend planner normalization
       adds read tools, strips unsafe refund proposals when facts do not allow them
  -> validate_and_execute_tool()
       executes get_order / policy / eligibility / refund only after validation
  -> QAAgent
       output: customer response
  -> JsonSessionStore.save()
  -> ReturnConversationResponse
```

### Agent Roles

- **Routing Agent:** classifies the user intent and extracts `order_id`,
  `item_id`, return reason, refund request state, and product hint.
- **Planner Agent:** receives structured routing output and deterministic
  backend facts, then decides whether the request should be approved, rejected,
  clarified, or escalated.
- **Q&A Agent:** converts the planner decision and backend tool results into the
  final support response.

### Backend-Owned Decisions

The model does not have the final say on side effects. Backend code:

- merges session context with routing output
- computes missing required fields for return requests
- loads order, product, policy, and eligibility facts before planner execution
- adds required read-only tool proposals when missing
- removes `issue_refund` proposals when eligibility, ownership, or intent is not valid
- validates refund execution independently in `tools.py`

Refund validation checks:

- order exists
- authenticated user owns the order
- item belongs to the order
- refund amount matches order item data
- return policy allows the refund

### Tradeoffs

**Strengths**

- Predictable control flow: every return request follows the same pipeline.
- Easy to test: each step has a stable contract and fixed order.
- Strong safety boundary: backend validation sits between LLM proposals and writes.
- Good fit for narrow domains where nearly every request needs the same stages.

**Costs**

- Less flexible for mixed-domain support requests. Shipping or payment questions
  do not naturally belong in this fixed return pipeline.
- Can spend LLM calls on stages that are not needed for simple policy questions.
- Adding new domains tends to add branches inside the pipeline instead of a clean
  routing layer.
- Planner correctness depends on the quality of facts and normalization around
  the LLM output.

**Why this design here**

Project 1 is meant to teach the base vocabulary: agent, routing, planner,
handoff, tool proposal, backend validation, session state, and traceable final
response. A fixed pipeline makes those concepts visible without introducing
orchestration complexity too early.

## Project 2: Agent Orchestrator

**Purpose:** demonstrate an orchestrator-led architecture where a central routing
decision selects one or more specialist agents and chooses execution mode.

**Entry point:** `POST /chat`

**Primary files:**

- `project2_agent_orchestrator/app/service.py`
- `project2_agent_orchestrator/app/models.py`
- `project2_agent_orchestrator/app/tools.py`

### Architecture

```text
HTTP request
  -> JsonSessionStore.load(session_id, user_id)
  -> deterministic context extraction from message
  -> Orchestrator LLM decision
       output: OrchestratorDecision
       selected_agents + execution_mode + extracted_context
  -> backend execution-plan builder
       single_agent | sequential | parallel
  -> specialist agents
       ReturnAgent / ShippingAgent / PaymentAgent / AccountAgent
       output: AgentOutput from LLM
  -> backend conversion
       AgentOutput -> AgentResult with backend facts in details
  -> escalation check
       low confidence, needs_escalation, or disagreement
  -> optional EscalationAgent
  -> safe backend action execution
       currently support ticket creation only
  -> deterministic response aggregation
  -> JsonSessionStore.save()
  -> ConversationResponse
```

### Agent Roles

- **Orchestrator:** selects specialists and requests an execution mode.
- **Return Agent:** resolves order/item and checks return eligibility.
- **Shipping Agent:** loads shipment status and flags delayed/lost/missing cases.
- **Payment Agent:** checks duplicate charges and refund timeline facts.
- **Account Agent:** loads profile data and flags sensitive account changes.
- **Escalation Agent:** proposes support-ticket creation when human review is
  needed.

### Execution Modes

The LLM proposes an execution mode, but backend code builds the final plan:

- `single_agent`: one specialist is enough.
- `parallel`: independent domains can run concurrently through `asyncio.gather`.
- `sequential`: sensitive requests or shipping-dependent requests run in order.

The backend also converts invalid combinations. For example, if multiple agents
are selected with `single_agent`, the plan becomes sequential.

### Backend-Owned Decisions

Project 2 gives the LLM more routing authority than Project 1, but the backend
still controls execution:

- specialist agent instances are selected from a fixed map
- backend tools load facts before specialist LLM calls
- specialist LLMs return `AgentOutput`, then backend wraps it as `AgentResult`
- unsafe actions stay proposed and are not executed automatically
- only non-approval support-ticket actions can be auto-executed
- disagreement or low confidence adds escalation

### Tradeoffs

**Strengths**

- Handles multi-domain requests without hardcoding every combination.
- Can run independent specialists in parallel.
- Domain agents stay smaller and easier to reason about.
- The final response can combine results from payment, return, shipping, account,
  and escalation paths.

**Costs**

- More moving parts than Project 1: routing, planning, specialist execution,
  escalation, aggregation, and action execution are separate concerns.
- LLM routing mistakes can select the wrong specialists; backend normalization
  reduces but does not eliminate that risk.
- Parallel execution makes shared context updates harder, so this prototype runs
  specialists against the same merged context and aggregates afterward.
- The final aggregator is deterministic string assembly, not a dedicated LLM
  synthesis agent. This is safer and predictable, but less polished for complex
  multi-agent answers.

**Why this design here**

Project 2 contrasts with Project 1. It shows when a central orchestrator is more
appropriate than a fixed pipeline: requests can span independent support domains,
and the system needs to choose which specialists run for each turn.

## Project 3: Adaptive Evaluation System

**Purpose:** demonstrate out-of-band evaluation and improvement for agentic
workflows without letting production agents rewrite themselves.

**Entry points:**

- `POST /evaluations/run`
- `POST /prompt-patches/review`
- CLI: `python -m project3_adaptive_eval_system.app.cli evaluate`

**Primary files:**

- `project3_adaptive_eval_system/app/service.py`
- `project3_adaptive_eval_system/app/agents.py`
- `project3_adaptive_eval_system/app/store.py`
- `project3_adaptive_eval_system/sample_traces/traces.jsonl`
- `project3_adaptive_eval_system/generated_tests/`

### Architecture

```text
completed traces
  -> ConversationTraceStore
  -> EvaluationAgent
       output: EvaluationResult
  -> backend evaluation normalization
       detected issues force failed status and regression generation
  -> TestCaseGenerator
       output: GeneratedTestCase for failed traces
  -> PromptImprovementAgent
       output: PromptPatch(status="proposed")
  -> GeneratedTestStore
       JSONL cases + pytest regression file
  -> PromptPatchStore
       preserves approved/rejected status across regeneration
  -> evaluation_report.md
  -> optional human review endpoint
       proposed -> approved | rejected
```

### Agent Roles

- **Evaluation Agent:** scores completed traces across intent understanding,
  policy correctness, tool safety, response helpfulness, escalation correctness,
  latency awareness, and context preservation.
- **Test Case Generator:** converts failed traces into regression cases with
  expected behavior and assertions.
- **Prompt Improvement Agent:** proposes prompt patches that target the detected
  failure.

### Durable Artifacts

Project 3 writes artifacts to the repo so the evaluation loop is inspectable:

- `sample_traces/traces.jsonl`: sample good and bad traces
- `generated_tests/regression_cases.jsonl`: generated regression cases
- `generated_tests/test_generated_regressions.py`: executable regression checks
- `prompt_patches.jsonl`: proposed/reviewed prompt patches
- `evaluation_report.md`: Markdown report from the latest evaluation run

The generated pytest currently replays two Project 1 behaviors grounded in the
sample failures:

- delivery-date-based return policy behavior
- ownership validation blocking unsafe refund execution

### Backend-Owned Decisions

Project 3 is intentionally not an auto-tuning system:

- prompt patches are stored with `status="proposed"`
- patch approval or rejection only happens through the review endpoint
- approved/rejected status is preserved if evaluation regenerates the same patch
- evaluation normalization prevents contradictory LLM output from suppressing
  regression generation when issues are present
- generated tests are artifacts for future verification, not production changes

### Tradeoffs

**Strengths**

- Keeps improvement out of the production request path.
- Converts observed failures into durable regression artifacts.
- Makes prompt changes reviewable instead of automatic.
- Can evaluate Projects 1 and 2 traces without coupling evaluation code into
  their runtime services.

**Costs**

- It depends on trace quality. Missing tool results, policy facts, or expected
  behavior will reduce evaluation quality.
- It adds a second system to maintain: schemas, generated artifacts, reports, and
  review state.
- Generated tests are only as strong as the replay logic written for known
  failure classes. New failure categories may need new replay helpers.
- Prompt patches are suggestions, not guarantees. Human review and follow-up
  implementation are still required.

**Why this design here**

Project 3 demonstrates adaptive system design without uncontrolled self-learning.
The feedback loop can identify failures and propose improvements, but production
behavior changes only after human approval and code/prompt updates outside the
evaluation run.

## Comparison

| Dimension | Project 1 | Project 2 | Project 3 |
|---|---|---|---|
| Runtime shape | Fixed pipeline | Dynamic orchestration | Out-of-band evaluation |
| Primary decision maker | Backend pipeline + role agents | Orchestrator + backend planner | Evaluation harness |
| Main LLM output types | `RoutingOutput`, `PlannerOutput`, `QAOutput` | `OrchestratorDecision`, `AgentOutput` | `EvaluationResult`, `GeneratedTestCase`, `PromptPatch` |
| Side effects | Refunds after validation | Support tickets only when safe | File artifacts and patch review status |
| Best fit | Narrow workflow | Multi-domain support | Continuous improvement loop |
| Main risk | Rigid branching | Routing/aggregation complexity | Weak traces or weak generated tests |

## Design Rules Used Across Projects

1. Keep agents stateless and invoke them per request or per evaluation run.
2. Store state outside agent roles.
3. Use structured outputs for model responses.
4. Pass backend facts into agents instead of asking agents to invent facts.
5. Validate every proposed side effect in deterministic code.
6. Normalize model output when safety or consistency requires it.
7. Keep tests deterministic by injecting rule-based LLM clients.
8. Exercise real LLM calls separately with `.env` sourced.
