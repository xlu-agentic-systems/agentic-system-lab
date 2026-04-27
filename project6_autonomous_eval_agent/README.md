# Project 6: Autonomous Evaluation Agent

Project 6 turns the Project 3 adaptive evaluation idea into a bounded
autonomous agent system.

Project 3 is an out-of-band workflow: evaluate traces, generate regression
cases, propose prompt patches, and stop for human review.

Project 6 adds an autonomous loop:

```text
goal -> agent chooses next tool -> host validates/executes -> observe -> repeat
```

The agent can decide to:

- evaluate stored traces
- run the labeled evaluator benchmark
- generate non-production candidate prompt files
- finish when acceptance gates pass

The host application still owns execution, persistence, and safety boundaries.
The autonomous agent cannot mutate production prompts.

## Run

Deterministic offline run:

```bash
python3 -m project6_autonomous_eval_agent.app.cli run --use-rule-based
```

Live LLM run:

```bash
set -a
source .env
set +a
python3 -m project6_autonomous_eval_agent.app.cli run
```

API:

```bash
uvicorn project6_autonomous_eval_agent.app.main:app --reload --port 8006
```

Then call:

```text
POST /autonomous-runs
```

## Acceptance Gates

A run completes only when these gates pass:

- evaluation generated regression tests and prompt patches
- labeled benchmark meets configured pass/fail accuracy
- labeled benchmark meets configured issue-category recall
- candidate prompt files are generated when required
- production prompt guard remains active

If the planner tries to finish early, the host marks the run blocked.
If the loop cannot satisfy gates before `max_iterations`, the host marks the run
`max_iterations_reached`.

## Safety Model

Project 6 is autonomous over review artifacts, not production behavior:

- candidate prompt files are written under the run directory
- production prompt files are not edited
- tool choices are validated by host code
- max-iteration bounds prevent unbounded loops
- deterministic tests use the rule-based planner and Project 3 rule-based LLM

This is the repo's first full autonomous loop, while preserving the same
principle used across Projects 1-5: the model decides what to attempt, and the
host decides what is allowed and executes it.

## Tests

```bash
pytest project6_autonomous_eval_agent/tests -q
```
