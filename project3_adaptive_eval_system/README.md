# Project 3: Adaptive Evaluation System

Small adaptive evaluation prototype for agentic customer-support workflows.

The system reviews completed conversation traces, detects failures, generates
regression test cases, and proposes prompt patches for human review. The
production path is intentionally out of band: prompt changes are stored as
proposals and are never applied automatically.

## Flow

`trace store -> Evaluation Agent -> Test Case Generator -> Prompt Improvement Agent -> Markdown report`

- `ConversationTraceStore`: stores and loads durable JSONL traces.
- `Project1FeedbackAdapter`: imports Project 1 return-bot traces into the
  Project 3 evaluation schema and can add optional Loki event-count context.
- `EvaluationAgent`: scores completed traces and detects failures.
- `TestCaseGenerator`: turns failed traces into replayable regression cases.
- `PromptImprovementAgent`: proposes prompt patches that require human approval.
- `EvaluationService`: orchestrates the feedback loop and writes artifacts.

The Project 3 agent prompts are explicitly observability-aware. When imported
traces include `loki_context`, the Evaluation Agent treats it as read-only
Grafana/Loki evidence for agent decisions, handoffs, and tool execution status.
The Prompt Improvement Agent may cite that telemetry in the rationale, but the
trace remains the source of truth and production prompts are not edited by the
feedback loop.

## Run

```bash
pip install -e ".[dev]"
cp .env.example .env
# fill OPENAI_API_KEY in .env
set -a
source .env
set +a
python -m project3_adaptive_eval_system.app.cli evaluate
```

To run the adaptive feedback loop over Project 1 traces:

```bash
python -m project3_adaptive_eval_system.app.cli project1-feedback \
  --trace-path project1_multi_agent_return_bot/data/conversation_traces.jsonl
```

If local Loki is running and Project 1 was started with
`AGENTIC_LAB_LOKI_URL=http://localhost:3100`, include observability context:

```bash
python -m project3_adaptive_eval_system.app.cli project1-feedback \
  --trace-path project1_multi_agent_return_bot/data/conversation_traces.jsonl \
  --include-loki-context
```

To run the API:

```bash
uvicorn project3_adaptive_eval_system.app.main:app --reload
```

Then call `POST /evaluations/run`, or `POST /project1/feedback/run` for the
Project 1 adaptive feedback path.

Prompt patches can be reviewed through `POST /prompt-patches/review`. This is
the simulated human review layer; patches remain `proposed` until that endpoint
explicitly approves or rejects them.

## Real LLM Calls

The default runtime path uses `OpenAILlmClient`, which calls the OpenAI
Responses API with Pydantic structured outputs. Unit tests inject
`RuleBasedLlmClient` so tests stay deterministic and do not require network
access.

## Safety Boundary

Adaptive does not mean uncontrolled self-learning. Project 3 only proposes
prompt improvements:

- production prompts are not rewritten automatically
- proposed patches are stored with `status="proposed"`
- a human must approve a patch before it can become active
- approval is simulated through the prompt-patch review endpoint
- generated tests are regression artifacts, not production behavior changes
- Loki/Grafana logs are observability evidence only; Project 3 queries Loki
  through the safe read-only log tool and does not scrape Grafana or mutate
  Project 1 from logs.

## Generated Artifacts

- `sample_traces/`: good and bad conversation traces
- `generated_tests/`: generated regression test cases
- `prompt_patches.jsonl`: proposed prompt patches
- `evaluation_report.md`: latest Markdown evaluation report

## Tests

```bash
pytest -q project3_adaptive_eval_system/tests
```

Coverage includes trace storage, evaluation behavior, regression generation,
prompt patch safety, the OpenAI structured-output adapter, and Project 1
feedback integration using Loki/Grafana-shaped log payloads. The integration
suite verifies that successful traces stay passed, blocked unsafe refunds
produce proposed prompt patches with log evidence, and production prompt files
are not mutated automatically.
