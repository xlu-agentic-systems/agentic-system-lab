from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from project4_agentic_project_copilot.app.eval_framework import (
    EVAL_CASES_PATH,
    OPENAI_SMOKE_CASES_PATH,
    EvaluationRun,
    load_cases,
    run_evaluation,
    run_goal_harness,
    run_mode_comparison,
    run_openai_evaluation,
    run_openai_goal_harness,
)

__all__ = [
    "EVAL_CASES_PATH",
    "OPENAI_SMOKE_CASES_PATH",
    "EvaluationRun",
    "load_cases",
    "run_evaluation",
    "run_goal_harness",
    "run_mode_comparison",
    "run_openai_evaluation",
    "run_openai_goal_harness",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Project 4 evaluation suites.")
    parser.add_argument("--live-openai", action="store_true", help="Run the real OpenAI-backed smoke suite.")
    parser.add_argument("--cases", type=Path, default=None, help="Optional JSONL cases file to run.")
    args = parser.parse_args()

    cases_path = args.cases
    try:
        if args.live_openai:
            run = asyncio.run(run_openai_evaluation(cases_path or OPENAI_SMOKE_CASES_PATH))
        else:
            run = asyncio.run(run_evaluation(cases_path or EVAL_CASES_PATH))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(run.model_dump(mode="json"), indent=2))
    if run.passed < run.total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
