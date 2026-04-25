# Project 3 Architecture

Project 3 is an out-of-band adaptive evaluation harness. It reviews completed
agent conversations and proposes improvements, but it does not mutate production
prompts or execute production actions.

## Sequence

```text
completed trace
  -> ConversationTraceStore
  -> Evaluation Agent
  -> Test Case Generator
  -> Prompt Improvement Agent
  -> generated_tests/
  -> prompt_patches.jsonl
  -> evaluation_report.md
```

## Components

1. `ConversationTraceStore` reads durable JSONL traces from `sample_traces/`.
2. `EvaluationAgent` scores each trace across intent, policy correctness, tool
   safety, response helpfulness, escalation correctness, latency awareness, and
   context preservation.
3. `TestCaseGenerator` converts failed traces into regression cases stored in
   `generated_tests/`.
4. `PromptImprovementAgent` proposes a single prompt patch per failed trace.
5. `EvaluationService` orchestrates the run and writes the Markdown report.
6. `POST /prompt-patches/review` simulates human approval or rejection of a
   proposed patch.

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
- human review is required before production prompts change
