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


def main() -> None:
    parser = argparse.ArgumentParser(description="Project 3 adaptive evaluation CLI")
    subcommands = parser.add_subparsers(dest="command", required=True)
    evaluate = subcommands.add_parser("evaluate", help="Evaluate stored traces")
    evaluate.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if args.command == "evaluate":
        asyncio.run(_evaluate(args.limit))


if __name__ == "__main__":
    main()
