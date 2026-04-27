from __future__ import annotations

import argparse
import asyncio
import json

from project6_autonomous_eval_agent.app.models import AutonomousGoal
from project6_autonomous_eval_agent.app.service import AutonomousEvalService


async def _run(args) -> None:
    goal = AutonomousGoal(
        objective=args.objective,
        trace_limit=args.trace_limit,
        max_iterations=args.max_iterations,
        min_pass_fail_accuracy=args.min_pass_fail_accuracy,
        min_issue_category_recall=args.min_issue_category_recall,
        require_candidate_prompts=not args.skip_candidate_prompts,
    )
    result = await AutonomousEvalService().run(goal=goal, use_rule_based=args.use_rule_based)
    if args.json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
        return
    print(f"run_id={result.run_id}")
    print(f"status={result.status}")
    print(f"iterations={result.iterations}")
    print(f"generated_tests={result.generated_test_count}")
    print(f"prompt_patches={result.prompt_patch_count}")
    print(f"candidate_prompts={result.candidate_prompt_count}")
    print(f"pass_fail_accuracy={result.pass_fail_accuracy}")
    print(f"issue_category_recall={result.issue_category_recall}")
    print(f"report={result.report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Project 6 autonomous evaluation agent CLI")
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run", help="Run the autonomous evaluation loop")
    run.add_argument("--objective", default=AutonomousGoal().objective)
    run.add_argument("--trace-limit", type=int, default=None)
    run.add_argument("--max-iterations", type=int, default=8)
    run.add_argument("--min-pass-fail-accuracy", type=float, default=0.8)
    run.add_argument("--min-issue-category-recall", type=float, default=0.8)
    run.add_argument("--skip-candidate-prompts", action="store_true")
    run.add_argument("--use-rule-based", action="store_true")
    run.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(_run(args))


if __name__ == "__main__":
    main()
