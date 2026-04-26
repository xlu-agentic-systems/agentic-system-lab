from __future__ import annotations

import argparse
import asyncio

from project3_adaptive_eval_system.app.llm import RuleBasedLlmClient
from project3_adaptive_eval_system.app.service import EvaluationService


def _service(use_rule_based: bool) -> EvaluationService:
    if use_rule_based:
        return EvaluationService(llm_client=RuleBasedLlmClient())
    return EvaluationService()


async def _evaluate(limit: int | None, *, use_rule_based: bool) -> None:
    result = await _service(use_rule_based).run_evaluation(limit)
    print(f"evaluated={result.trace_count}")
    print(f"generated_tests={len(result.generated_tests)}")
    print(f"prompt_patches={len(result.prompt_patches)}")
    print(f"report={result.report_path}")


async def _project1_feedback(
    *,
    trace_path: str | None,
    limit: int | None,
    include_loki_context: bool,
    loki_since_minutes: int,
    use_rule_based: bool,
) -> None:
    result = await _service(use_rule_based).run_project1_feedback(
        trace_path=trace_path,
        trace_limit=limit,
        include_loki_context=include_loki_context,
        loki_since_minutes=loki_since_minutes,
    )
    print(f"imported={result.import_result.imported_count}")
    print(f"evaluated={result.evaluation_result.trace_count}")
    print(f"generated_tests={len(result.evaluation_result.generated_tests)}")
    print(f"prompt_patches={len(result.evaluation_result.prompt_patches)}")
    print(f"report={result.evaluation_result.report_path}")


async def _benchmark(case_path: str | None, *, use_rule_based: bool) -> None:
    result = await _service(use_rule_based).run_labeled_benchmark(case_path)
    print(f"cases={result.case_count}")
    print(f"passed_cases={result.passed_cases}")
    print(f"pass_fail_accuracy={result.pass_fail_accuracy}")
    print(f"issue_category_recall={result.issue_category_recall}")
    print(f"patch_target_accuracy={result.patch_target_accuracy}")
    if result.quality_assessment:
        print(f"evaluation_depth_score={result.quality_assessment.evaluation_depth_score}")
        print(f"production_readiness_score={result.quality_assessment.production_readiness_score}")


def _promote_candidate(patch_id: str, source_prompt_path: str | None) -> None:
    result = EvaluationService().promote_prompt_patch_candidate(
        patch_id,
        source_prompt_path=source_prompt_path,
    )
    print(f"patch_id={result.patch_id}")
    print(f"validation_passed={result.validation_passed}")
    print(f"candidate_prompt_path={result.candidate_prompt_path}")
    for message in result.validation_messages:
        print(f"- {message}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Project 3 adaptive evaluation CLI")
    subcommands = parser.add_subparsers(dest="command", required=True)
    evaluate = subcommands.add_parser("evaluate", help="Evaluate stored traces")
    evaluate.add_argument("--limit", type=int, default=None)
    evaluate.add_argument("--use-rule-based", action="store_true")
    project1 = subcommands.add_parser(
        "project1-feedback",
        help="Import Project 1 traces and run adaptive evaluation over them",
    )
    project1.add_argument("--trace-path", default=None)
    project1.add_argument("--limit", type=int, default=None)
    project1.add_argument("--include-loki-context", action="store_true")
    project1.add_argument("--loki-since-minutes", type=int, default=60)
    project1.add_argument("--use-rule-based", action="store_true")
    benchmark = subcommands.add_parser(
        "benchmark",
        help="Run labeled evaluation cases and quality gates",
    )
    benchmark.add_argument("--case-path", default=None)
    benchmark.add_argument("--use-rule-based", action="store_true")
    promote = subcommands.add_parser(
        "promote-candidate",
        help="Render an approved prompt patch into a candidate prompt file",
    )
    promote.add_argument("patch_id")
    promote.add_argument("--source-prompt-path", default=None)
    args = parser.parse_args()

    if args.command == "evaluate":
        asyncio.run(_evaluate(args.limit, use_rule_based=args.use_rule_based))
    elif args.command == "project1-feedback":
        asyncio.run(
            _project1_feedback(
                trace_path=args.trace_path,
                limit=args.limit,
                include_loki_context=args.include_loki_context,
                loki_since_minutes=args.loki_since_minutes,
                use_rule_based=args.use_rule_based,
            )
        )
    elif args.command == "benchmark":
        asyncio.run(_benchmark(args.case_path, use_rule_based=args.use_rule_based))
    elif args.command == "promote-candidate":
        _promote_candidate(args.patch_id, args.source_prompt_path)


if __name__ == "__main__":
    main()
