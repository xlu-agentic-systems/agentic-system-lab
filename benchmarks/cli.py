from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from benchmarks.runner import BenchmarkConfig, BenchmarkRunner, write_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local latency benchmarks across Projects 1, 2, and 3.")
    parser.add_argument("--requests", type=int, default=6, help="Requests per architecture/pattern.")
    parser.add_argument("--llm-delay", type=float, default=0.02, help="Async delay per rule-based LLM call.")
    parser.add_argument("--mixed-burst-size", type=int, default=2, help="Concurrent burst size inside mixed workload.")
    parser.add_argument("--output-dir", type=Path, default=Path("benchmarks/results"), help="Artifact output directory.")
    args = parser.parse_args()

    runner = BenchmarkRunner(
        BenchmarkConfig(
            requests=args.requests,
            llm_delay_seconds=args.llm_delay,
            mixed_burst_size=args.mixed_burst_size,
        )
    )
    results = asyncio.run(runner.run_all())
    json_path, raw_path, md_path = write_artifacts(results, args.output_dir)
    print(f"Wrote {json_path}")
    print(f"Wrote {raw_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
