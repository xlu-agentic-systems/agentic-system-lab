# Agentic System Tradeoffs

This document compares the three system designs in this repo. It is grounded in
the current codebase and avoids describing capabilities that are not implemented.

## Fixed Pipeline vs Orchestrator

Project 1 uses a fixed pipeline:

```text
Routing Agent -> Planner Agent -> backend tools -> Q&A Agent
```

Project 2 uses an orchestrator:

```text
Orchestrator -> selected specialists -> optional escalation -> aggregation
```

### Fixed Pipeline Tradeoffs

The fixed pipeline is easier to reason about because every request goes through a
known sequence. That makes test coverage straightforward: routing tests,
planner tests, policy tests, and refund validation tests line up with the runtime
flow.

The cost is flexibility. If the request is outside returns, Project 1 either
rejects/escalates or needs custom branches. It is a good architecture for a
narrow workflow, not a general customer-support router.

### Orchestrator Tradeoffs

The orchestrator handles mixed requests more naturally. A request like “I was
charged twice and I also want to return the shoes” can select Payment and Return
specialists and run them in parallel.

The cost is more orchestration risk. The system must validate selected agents,
convert execution modes into valid backend plans, handle disagreement, and
aggregate results. Project 2 keeps those controls in backend code rather than
letting agents call each other directly.

## In-Band Agents vs Out-of-Band Evaluation

Projects 1 and 2 run in the user-facing path. Project 3 runs after conversations
are complete.

### In-Band Tradeoffs

In-band agents must optimize for correctness, latency, and safe side effects in
the active customer flow. Their model calls produce decisions and customer-facing
text. Backend validation is mandatory because mistakes can affect refunds,
support tickets, or user trust.

### Out-of-Band Tradeoffs

Out-of-band evaluation can spend more effort reviewing traces because it does not
block the customer request. It can generate reports, tests, and patch proposals.
The tradeoff is delayed impact: Project 3 does not fix production behavior by
itself. It creates artifacts that humans can review and apply later.

## Structured Outputs

All three projects use Pydantic structured outputs through the OpenAI Responses
API. This gives the backend typed contracts for agent outputs.

Tradeoffs:

- Better validation and easier tests.
- More schema maintenance.
- Arbitrary dictionaries are avoided in LLM-facing response models because strict
  structured-output schemas need explicit shapes.
- Backend models can be richer than LLM-facing models. Project 2 uses
  `AgentOutput` for the LLM and wraps it as `AgentResult` with backend facts.

## Tool Safety

Projects 1 and 2 expose fake backend tools. The important design choice is that
the LLM proposes, while backend code validates and executes.

Project 1 validates refunds before execution. Project 2 only auto-executes safe
support-ticket creation; unsafe payment changes remain proposed. Project 3 does
not execute production tools at all.

The tradeoff is extra backend code, but that code is what makes the architecture
defensible: policy, ownership, amount checks, and approval status are not left to
model text.

## Session and Trace Persistence

Projects 1 and 2 use JSON session stores keyed by `session_id` and guarded by
`user_id`. If a session ID is reused by another user, a fresh state is returned.

Project 3 uses JSONL stores for traces, generated tests, and prompt patches.

Tradeoffs:

- JSON/JSONL is simple and inspectable for a prototype.
- It is not a production concurrency or scale solution.
- The file-based approach makes the system easy to review in interviews and PRs.

## Evaluation Quality

Project 3 can only evaluate what traces contain. The sample traces include user
messages, agent outputs, tool calls, backend validations, final responses,
outcomes, expected behavior, and policy basis.

Tradeoffs:

- Richer traces enable better failure detection.
- Sparse traces require the evaluation model to infer more, which is less reliable.
- Generated tests should replay real behavior where possible. The current
  generated tests replay Project 1 policy and refund-ownership behaviors for the
  included sample failures.

## What This Repo Does Not Implement

The repo intentionally does not include:

- production database persistence
- authentication middleware
- background workers
- full trace capture from Projects 1 and 2 into Project 3
- automatic prompt deployment
- real payment/refund integrations
- a dedicated LLM final aggregator for Project 2

Those omissions keep the prototypes focused on architecture patterns rather than
full product infrastructure.

## Retrieval And Tool-Use Copilot

Project 4 adds a different pattern from Projects 1-3. It combines an
orchestrator with capability paths:

- RAG over uploaded files
- safe text-to-SQL over local structured data
- confirmed API tool invocation for project actions
- session context for current project/task/document

The tradeoff is a broader safety surface. Retrieval needs citations and source
grounding; SQL needs read-only validation; API actions need confirmation before
writes. This is more complex than a pure RAG app, but it better matches a real
project copilot that must work across files, data, and actions.
