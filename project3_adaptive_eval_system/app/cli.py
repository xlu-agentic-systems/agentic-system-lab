from __future__ import annotations

import argparse
import asyncio

from project3_adaptive_eval_system.app.service import EvaluationService


async def _evaluate(limit: int | None) -> None:
    result = await EvaluationService().run_evaluation(limit)
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
) -> None:
    result = await EvaluationService().run_project1_feedback(
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Project 3 adaptive evaluation CLI")
    subcommands = parser.add_subparsers(dest="command", required=True)
    evaluate = subcommands.add_parser("evaluate", help="Evaluate stored traces")
    evaluate.add_argument("--limit", type=int, default=None)
    project1 = subcommands.add_parser(
        "project1-feedback",
        help="Import Project 1 traces and run adaptive evaluation over them",
    )
    project1.add_argument("--trace-path", default=None)
    project1.add_argument("--limit", type=int, default=None)
    project1.add_argument("--include-loki-context", action="store_true")
    project1.add_argument("--loki-since-minutes", type=int, default=60)
    args = parser.parse_args()

    if args.command == "evaluate":
        asyncio.run(_evaluate(args.limit))
    elif args.command == "project1-feedback":
        asyncio.run(
            _project1_feedback(
                trace_path=args.trace_path,
                limit=args.limit,
                include_loki_context=args.include_loki_context,
                loki_since_minutes=args.loki_since_minutes,
            )
        )


if __name__ == "__main__":
    main()
