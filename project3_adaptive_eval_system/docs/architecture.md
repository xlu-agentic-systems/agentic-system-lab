# Project 3 Architecture

Project 3 is an out-of-band adaptive evaluation harness. It reviews completed
agent conversations and proposes improvements, but it does not mutate production
prompts or execute production actions.

Project 1 is the first production-style target system for this loop. Project 1
continues to own the return-chatbot request path; Project 3 imports its durable
conversation traces and can optionally query Loki for session-scoped
observability context.

## Sequence

```text
Project 1 completed trace
  -> Project1FeedbackAdapter
  -> normalized ConversationTrace
  -> ConversationTraceStore
  -> Evaluation Agent
  -> Test Case Generator
  -> Prompt Improvement Agent
  -> generated_tests/
  -> prompt_patches.jsonl
  -> optional prompt_candidates/
  -> evaluation_report.md
```

## Components

1. `ConversationTraceStore` reads durable JSONL traces from `sample_traces/`.
2. `Project1FeedbackAdapter` converts Project 1 `ReturnConversationTrace`
   records into Project 3 `ConversationTrace` records. The adapter keeps the
   production path out of band and can add a summary of Loki events for the same
   `session_id`.
3. `EvaluationAgent` scores each trace across intent, policy correctness, tool
   safety, response helpfulness, escalation correctness, latency awareness, and
   context preservation.
4. `TestCaseGenerator` converts failed traces into regression cases stored in
   `generated_tests/`.
5. `PromptImprovementAgent` proposes a single prompt patch per failed trace.
6. `LabeledCaseStore` loads benchmark cases with expected pass/fail outcomes,
   issue categories, regression requirements, and patch targets.
7. `EvaluationService` orchestrates the run and writes the Markdown report.
8. Approved prompt patches can be rendered into candidate prompt files under
   `prompt_candidates/`.
9. `POST /prompt-patches/review` simulates human approval or rejection of a
   proposed patch.

## Project 1 Feedback Loop

```text
Project 1 /returns/chat
  -> JSONL ReturnConversationTrace
  -> optional Loki structured events
  -> Project 3 import
  -> evaluation + regression generation
  -> proposed prompt patch targeting prompts/project1_multi_agent.md
  -> human review
  -> approved candidate prompt file
```

Grafana remains the human UI for logs. Project 3 does not read Grafana directly.
When observability context is requested, Project 3 queries Loki through the safe
`LokiLogQueryTool`, using the Project 1 `session_id`.

The Project 3 prompts now distinguish trace facts from telemetry evidence:

- the Evaluation Agent may use `loki_context` to corroborate agent decisions,
  handoffs, and tool execution status
- the Test Case Generator can turn log-supported failures into assertions about
  observable decisions and validation outcomes
- the Prompt Improvement Agent can cite Grafana/Loki evidence in the patch
  rationale, while keeping the patch proposed for human review

The integration tests use Loki/Grafana-shaped responses captured from the local
observability flow: user message events, routing/planner/QA decisions, and
backend tool execution events. They verify both the passing path and a blocked
unsafe-refund path.

## Evaluation Depth

Project 3 includes a labeled benchmark harness. Each case defines the source
`ConversationTrace`, expected pass/fail result, expected issue categories,
whether a regression should be generated, and the expected prompt patch target
when a patch is needed.

The benchmark reports pass/fail accuracy, issue-category recall, patch-target
accuracy, and a quality assessment. This is stronger than only checking whether
an LLM produced plausible prose because it measures evaluator behavior against
known failures such as purchase-date policy mistakes, unsafe refund proposals,
missing clarification questions, hallucinated policy details, and context loss.

## Prompt Promotion

Prompt patches still require human approval. After approval, Project 3 can
render a candidate prompt file under `prompt_candidates/`. That file contains
the original prompt plus the approved proposed instruction and rationale. The
production prompt is not modified by the adaptive loop; a developer must review
the candidate, run regressions, and merge the prompt change through normal code
review.

## LLM Boundary

The default runtime client is `OpenAILlmClient`, which calls the OpenAI Responses
API with Pydantic structured outputs. Tests use `RuleBasedLlmClient` so CI does
not depend on network access.

The LLM returns structured `EvaluationResult`, `GeneratedTestCase`, and
`PromptPatch` objects. The backend owns file persistence, report rendering, and
the safety rule that prompt patches stay proposed until human approval.

## Safety

Adaptive feedback is intentionally separated from production behavior:

- evaluation happens after conversations complete
- generated tests are regression artifacts
- prompt patches are stored with `status="proposed"`
- prompt patch status changes only through the explicit review endpoint
- no prompt patch is applied automatically
- approved patches produce candidate files instead of directly editing
  production prompts
- human review is required before production prompts change
- imported Project 1 traces are normalized copies; importing does not change
  Project 1 sessions, tools, or prompt behavior
