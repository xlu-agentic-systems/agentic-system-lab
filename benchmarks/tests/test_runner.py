from __future__ import annotations

import asyncio

from benchmarks.runner import (
    BenchmarkConfig,
    BenchmarkRunner,
    Workload,
    latency_summary,
    markdown_summary,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        current = self.value
        self.value += 0.1
        return current


class CountingLlm:
    def __init__(self) -> None:
        self.task_counts = {"fake_task": 0}

    @property
    def total_calls(self) -> int:
        return sum(self.task_counts.values())


def test_latency_summary_uses_percentiles_without_wall_clock() -> None:
    summary = latency_summary([0.4, 0.1, 0.3, 0.2])

    assert summary["min"] == 0.1
    assert summary["mean"] == 0.25
    assert summary["p50"] == 0.25
    assert round(summary["p90"], 2) == 0.37
    assert round(summary["p95"], 2) == 0.39
    assert summary["max"] == 0.4


def test_runner_records_success_errors_and_task_counts_without_exact_timing() -> None:
    clock = FakeClock()
    llm = CountingLlm()

    async def call(index: int) -> None:
        llm.task_counts["fake_task"] += 1
        if index == 1:
            raise RuntimeError("boom")

    workload = Workload("project1", 3, call, llm)  # type: ignore[arg-type]
    runner = BenchmarkRunner(BenchmarkConfig(requests=3), clock=clock)

    timed = asyncio.run(runner.run_workload(workload, "sequential"))
    summary = runner._summarize(workload, "sequential", timed)

    assert summary["total_requests"] == 3
    assert summary["success_count"] == 2
    assert summary["errors"] == ["boom"]
    assert summary["llm_task_call_counts"] == {"fake_task": 3}
    assert summary["llm_total_calls"] == 3
    assert summary["latency_seconds"]["min"] > 0
    assert summary["raw_events"][1]["success"] is False
    assert summary["raw_events"][1]["error"] == "boom"
    assert summary["throughput_rps"] > 0


def test_mixed_workload_preserves_request_count() -> None:
    seen: list[int] = []
    llm = CountingLlm()

    async def call(index: int) -> None:
        seen.append(index)

    workload = Workload("project2", 5, call, llm)  # type: ignore[arg-type]
    runner = BenchmarkRunner(BenchmarkConfig(requests=5, mixed_burst_size=2), clock=FakeClock())

    timed = asyncio.run(runner.run_workload(workload, "mixed_workload"))

    assert len(timed) == 5
    assert sorted(seen) == [0, 1, 2, 3, 4]


def test_markdown_summary_contains_required_metrics() -> None:
    markdown = markdown_summary(
        {
            "results": [
                {
                    "architecture": "project1",
                    "pattern": "sequential",
                    "total_requests": 2,
                    "success_count": 2,
                    "latency_seconds": {"mean": 0.1, "p95": 0.2},
                    "throughput_rps": 10.0,
                    "llm_total_calls": 4,
                },
                {
                    "architecture": "project1",
                    "pattern": "concurrent_burst",
                    "total_requests": 2,
                    "success_count": 2,
                    "latency_seconds": {"mean": 0.05, "p95": 0.1},
                    "throughput_rps": 20.0,
                    "llm_total_calls": 4,
                },
            ]
        }
    )

    assert "| Architecture | Pattern | Requests | Success | Mean (s) | P95 (s) | Throughput (rps) | LLM calls |" in markdown
    assert "project1" in markdown
    assert "Conclusion" in markdown
