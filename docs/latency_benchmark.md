# Architecture Latency Benchmark

This benchmark compares caller-level latency and concurrency behavior across:

- Project 1: multi-agent return bot
- Project 2: orchestrated specialist agents
- Project 3: adaptive evaluation workflow

It uses local in-process service calls only. No network calls or real OpenAI calls
are made. Each project uses its existing deterministic `RuleBasedLlmClient`,
wrapped by `DelayedLlmClient` to add configurable `asyncio.sleep` delay per LLM
task. That makes concurrency differences observable while keeping outputs
deterministic.

The benchmark exists to justify design tradeoffs, not to crown one architecture
as globally faster. Project 1, Project 2, and Project 3 serve different runtime
roles, so the useful comparison is how latency changes under different caller
patterns and workload shapes.

## Run

```bash
python3 -m benchmarks.cli --requests 6 --llm-delay 0.02 --output-dir benchmarks/results
```

Artifacts:

- `benchmarks/results/latency_results.json`
- `benchmarks/results/latency_raw_events.jsonl`
- `benchmarks/results/latency_summary.md`

The default output directory is intentionally outside the tracked source unless
you choose to commit a specific run. Local benchmark numbers are machine- and
load-dependent, so the repo commits the tooling and interpretation guidance, not
a universal performance claim.

## Caller Patterns

- `sequential`: awaits each request one at a time.
- `concurrent_burst`: schedules all requests at once with `asyncio.gather`.
- `mixed_workload`: alternates one sequential request with a small concurrent burst.

These patterns test different assumptions:

- Sequential calls show baseline per-request cost.
- Concurrent burst calls show whether independent requests can overlap waiting
  time from delayed LLM tasks.
- Mixed workload calls approximate a service that receives a steady stream with
  occasional short spikes.

## Metrics

Each architecture/pattern result includes:

- total requests
- success count
- errors
- min, mean, p50, p90, p95, and max latency
- throughput in requests per second
- LLM task call counts by task name
- total LLM calls
- raw per-request events with latency, success, and error fields

## Local Integration Strategy

The benchmark calls each service directly with temp-backed stores:

- Project 1 calls `ReturnConversationService.handle_message`.
- Project 2 calls `ConversationService.handle_message`.
- Project 3 calls `EvaluationService.run_evaluation`.

This is an integration benchmark for the local service layer. It exercises
session stores, backend facts, tool validation, agent orchestration, evaluation
artifact generation, and Markdown/JSON output without starting FastAPI or calling
the OpenAI network.

## Architecture Questions

Use the generated artifacts to answer narrow questions:

- Does Project 1 keep a stable LLM task profile across return scenarios?
- Does Project 2's `parallel` execution mode reduce wall-clock latency for
  independent multi-domain specialist work?
- How much overhead does Project 2 add for simple single-domain support turns?
- How does Project 3 batch evaluation cost change when traces fail and trigger
  generated tests plus prompt patches?
- Do caller-level concurrent bursts improve throughput when LLM work is mostly
  async waiting?

## Interpreting Results

Project 1 typically has a fixed route-plan-answer chain, so request-level
concurrency mainly overlaps independent request LLM waits. It is expected to be
predictable for return workflows because the pipeline shape is stable.

Project 2 can show additional benefit on multi-domain requests because the
orchestrator can run independent specialist agents in parallel inside a single
request. It can also be slower for simple single-domain requests because it adds
orchestrator routing and aggregation around specialist work.

Project 3 should not be compared as a user-facing request architecture. Its
useful metric is evaluation throughput and artifact cost, especially traces per
second and extra LLM calls for failed traces that generate regression tests and
prompt patches.

Use the Markdown summary for the high-level comparison, then inspect JSON task
counts and raw events to separate architecture effects from workload
composition. A faster result with fewer LLM calls is not the same as a faster
architecture with identical work.

## Constraints

- The benchmark uses deterministic delayed local LLM clients. Real OpenAI calls
  should be measured separately because network latency, model choice, rate
  limits, and retries can dominate architecture effects.
- Projects 1 and 2 use JSON session files. This is inspectable for a lab, but it
  is not a production concurrency store.
- Project 3 writes generated test, prompt patch, and report artifacts. The
  benchmark isolates each run with temp paths to avoid mutating repo artifacts.
- Hot-session contention is intentionally excluded from the default run. It would
  be a storage-safety benchmark, not a fair architecture latency comparison.
