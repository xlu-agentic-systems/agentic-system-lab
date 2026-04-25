from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable, Literal

from project1_multi_agent_return_bot.app.llm import RuleBasedLlmClient as Project1RuleLlm
from project1_multi_agent_return_bot.app.return_models import ReturnConversationRequest
from project1_multi_agent_return_bot.app.return_service import ReturnConversationService
from project1_multi_agent_return_bot.app.session_store import JsonSessionStore as Project1SessionStore
from project1_multi_agent_return_bot.app.trace_store import JsonlTraceStore as Project1TraceStore
from project2_agent_orchestrator.app.llm import RuleBasedLlmClient as Project2RuleLlm
from project2_agent_orchestrator.app.models import ConversationRequest
from project2_agent_orchestrator.app.service import ConversationService
from project2_agent_orchestrator.app.session_store import JsonSessionStore as Project2SessionStore
from project3_adaptive_eval_system.app.llm import RuleBasedLlmClient as Project3RuleLlm
from project3_adaptive_eval_system.app.models import ConversationTrace
from project3_adaptive_eval_system.app.service import EvaluationService
from project3_adaptive_eval_system.app.store import (
    ConversationTraceStore,
    GeneratedTestStore,
    PromptPatchStore,
)

from benchmarks.delayed_llm import DelayedLlmClient


Pattern = Literal["sequential", "concurrent_burst", "mixed_workload"]
Architecture = Literal["project1", "project2", "project3"]
Clock = Callable[[], float]


@dataclass(frozen=True)
class BenchmarkConfig:
    requests: int = 6
    llm_delay_seconds: float = 0.02
    mixed_burst_size: int = 2


@dataclass
class TimedResult:
    request_index: int
    latency_seconds: float
    success: bool
    error: str | None = None


@dataclass
class Workload:
    architecture: Architecture
    request_count: int
    make_call: Callable[[int], Awaitable[Any]]
    llm_client: DelayedLlmClient


class BenchmarkRunner:
    def __init__(self, config: BenchmarkConfig | None = None, *, clock: Clock = perf_counter) -> None:
        self.config = config or BenchmarkConfig()
        self.clock = clock

    async def run_all(self) -> dict[str, Any]:
        started_at = self.clock()
        results = []
        for architecture in ("project1", "project2", "project3"):
            for pattern in ("sequential", "concurrent_burst", "mixed_workload"):
                workload = build_workload(
                    architecture,
                    request_count=self.config.requests,
                    llm_delay_seconds=self.config.llm_delay_seconds,
                )
                wall_start = self.clock()
                timed = await self.run_workload(workload, pattern)
                wall_time = self.clock() - wall_start
                results.append(self._summarize(workload, pattern, timed, wall_time_seconds=wall_time))
        return {
            "config": {
                "requests": self.config.requests,
                "llm_delay_seconds": self.config.llm_delay_seconds,
                "mixed_burst_size": self.config.mixed_burst_size,
            },
            "elapsed_seconds": self.clock() - started_at,
            "results": results,
        }

    async def run_workload(self, workload: Workload, pattern: Pattern) -> list[TimedResult]:
        if pattern == "sequential":
            return [await self._timed_call(workload, index) for index in range(workload.request_count)]
        if pattern == "concurrent_burst":
            return await asyncio.gather(
                *(self._timed_call(workload, index) for index in range(workload.request_count))
            )
        if pattern == "mixed_workload":
            return await self._run_mixed(workload)
        raise ValueError(f"unknown benchmark pattern: {pattern}")

    async def _run_mixed(self, workload: Workload) -> list[TimedResult]:
        results: list[TimedResult] = []
        index = 0
        while index < workload.request_count:
            results.append(await self._timed_call(workload, index))
            index += 1
            burst_indexes = range(index, min(index + self.config.mixed_burst_size, workload.request_count))
            burst = await asyncio.gather(*(self._timed_call(workload, item) for item in burst_indexes))
            results.extend(burst)
            index += len(burst)
        return results

    async def _timed_call(self, workload: Workload, index: int) -> TimedResult:
        start = self.clock()
        try:
            await workload.make_call(index)
        except Exception as exc:  # pragma: no cover - exercised through metrics in manual runs
            return TimedResult(
                request_index=index,
                latency_seconds=self.clock() - start,
                success=False,
                error=str(exc),
            )
        return TimedResult(request_index=index, latency_seconds=self.clock() - start, success=True)

    def _summarize(
        self,
        workload: Workload,
        pattern: Pattern,
        timed: list[TimedResult],
        *,
        wall_time_seconds: float | None = None,
    ) -> dict[str, Any]:
        latencies = [result.latency_seconds for result in timed]
        total = len(timed)
        errors = [result.error for result in timed if result.error]
        total_elapsed = wall_time_seconds
        if total_elapsed is None:
            total_elapsed = sum(latencies) if pattern == "sequential" else max(latencies, default=0.0)
        return {
            "architecture": workload.architecture,
            "pattern": pattern,
            "total_requests": total,
            "success_count": sum(1 for result in timed if result.success),
            "errors": errors,
            "latency_seconds": latency_summary(latencies),
            "wall_time_seconds": total_elapsed,
            "throughput_rps": total / total_elapsed if total_elapsed > 0 else 0.0,
            "llm_task_call_counts": dict(sorted(workload.llm_client.task_counts.items())),
            "llm_total_calls": workload.llm_client.total_calls,
            "raw_events": [
                {
                    "architecture": workload.architecture,
                    "pattern": pattern,
                    "request_index": result.request_index,
                    "latency_seconds": result.latency_seconds,
                    "success": result.success,
                    "error": result.error,
                }
                for result in timed
            ],
        }


def latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {key: 0.0 for key in ("min", "mean", "p50", "p90", "p95", "max")}
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "mean": statistics.fmean(ordered),
        "p50": percentile(ordered, 50),
        "p90": percentile(ordered, 90),
        "p95": percentile(ordered, 95),
        "max": ordered[-1],
    }


def percentile(sorted_values: list[float], percentile_value: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * percentile_value / 100
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = rank - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def build_workload(
    architecture: Architecture,
    *,
    request_count: int,
    llm_delay_seconds: float,
) -> Workload:
    temp_root = Path(tempfile.mkdtemp(prefix=f"{architecture}-latency-bench-"))
    if architecture == "project1":
        return _project1_workload(temp_root, request_count, llm_delay_seconds)
    if architecture == "project2":
        return _project2_workload(temp_root, request_count, llm_delay_seconds)
    if architecture == "project3":
        return _project3_workload(temp_root, request_count, llm_delay_seconds)
    raise ValueError(f"unknown architecture: {architecture}")


def _project1_workload(root: Path, request_count: int, delay: float) -> Workload:
    llm = DelayedLlmClient(Project1RuleLlm(), delay_seconds=delay)

    async def call(index: int) -> Any:
        service = ReturnConversationService(
            session_store=Project1SessionStore(root / f"project1-session-{index}.json"),
            trace_store=Project1TraceStore(root / f"project1-traces-{index}.jsonl"),
            llm_client=llm,
        )
        message = _return_messages()[index % len(_return_messages())]
        return await service.handle_message(
            ReturnConversationRequest(
                session_id=f"bench-p1-{index}",
                user_id="user-1",
                message=message,
            )
        )

    return Workload("project1", request_count, call, llm)


def _project2_workload(root: Path, request_count: int, delay: float) -> Workload:
    llm = DelayedLlmClient(Project2RuleLlm(), delay_seconds=delay)

    async def call(index: int) -> Any:
        service = ConversationService(
            session_store=Project2SessionStore(root / f"project2-session-{index}.json"),
            llm_client=llm,
        )
        message = _orchestrator_messages()[index % len(_orchestrator_messages())]
        return await service.handle_message(
            ConversationRequest(
                session_id=f"bench-p2-{index}",
                user_id="user-1",
                message=message,
            )
        )

    return Workload("project2", request_count, call, llm)


def _project3_workload(root: Path, request_count: int, delay: float) -> Workload:
    llm = DelayedLlmClient(Project3RuleLlm(), delay_seconds=delay)

    async def call(index: int) -> Any:
        trace_path = root / f"traces-{index}.jsonl"
        trace_path.write_text(_trace_for(index).model_dump_json() + "\n")
        service = EvaluationService(
            trace_store=ConversationTraceStore(trace_path),
            generated_test_store=GeneratedTestStore(root / f"generated-{index}.jsonl"),
            prompt_patch_store=PromptPatchStore(root / f"patches-{index}.jsonl"),
            report_path=root / f"report-{index}.md",
            llm_client=llm,
        )
        return await service.run_evaluation(trace_limit=1)

    return Workload("project3", request_count, call, llm)


def _return_messages() -> list[str]:
    return [
        "I want to return order-1001 item-1 because it is too small.",
        "Can I return order-1002 item-3? It arrived damaged.",
        "What is the return policy for shoes?",
    ]


def _orchestrator_messages() -> list[str]:
    return [
        "I want to return order-1001 item-1 because it is too small.",
        "My package for order-1001 is delayed and I also want a refund timeline.",
        "I was charged twice for order-3001 on my card.",
    ]


def _trace_for(index: int) -> ConversationTrace:
    failed = index % 2 == 1
    return ConversationTrace(
        trace_id=f"bench-trace-{index}",
        project="project1",
        session_id=f"trace-session-{index}",
        user_id="user-1",
        user_message="I want to return order-1002 item-3 because it arrived damaged.",
        agent_outputs=[
            {
                "agent_name": "planner",
                "output": "Used delivery date correctly." if not failed else "Used purchase date instead of delivery date.",
            }
        ],
        tool_calls=[],
        backend_validations=[],
        final_response="Return request handled.",
        outcome="failure" if failed else "success",
        expected_behavior="Use delivery date for eligibility and require backend validation before refunds.",
        policy_basis="Return eligibility is based on delivery date.",
    )


def write_artifacts(results: dict[str, Any], output_dir: Path | str) -> tuple[Path, Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "latency_results.json"
    raw_path = output_path / "latency_raw_events.jsonl"
    md_path = output_path / "latency_summary.md"
    json_path.write_text(json.dumps(results, indent=2, sort_keys=True))
    raw_path.write_text(
        "\n".join(
            json.dumps(event, sort_keys=True)
            for item in results["results"]
            for event in item.get("raw_events", [])
        )
        + "\n"
    )
    md_path.write_text(markdown_summary(results))
    return json_path, raw_path, md_path


def markdown_summary(results: dict[str, Any]) -> str:
    rows = [
        "| Architecture | Pattern | Requests | Success | Mean (s) | P95 (s) | Throughput (rps) | LLM calls |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in results["results"]:
        lat = item["latency_seconds"]
        rows.append(
            "| {architecture} | {pattern} | {total_requests} | {success_count} | "
            "{mean:.4f} | {p95:.4f} | {throughput:.2f} | {llm_total_calls} |".format(
                architecture=item["architecture"],
                pattern=item["pattern"],
                total_requests=item["total_requests"],
                success_count=item["success_count"],
                mean=lat["mean"],
                p95=lat["p95"],
                throughput=item["throughput_rps"],
                llm_total_calls=item["llm_total_calls"],
            )
        )

    conclusions = _conclusions(results)
    return "\n".join(
        [
            "# Latency Benchmark Summary",
            "",
            "Local benchmark run using deterministic rule-based LLM clients with async delay injection.",
            "",
            *rows,
            "",
            "## Conclusion",
            "",
            *[f"- {line}" for line in conclusions],
            "",
        ]
    )


def _conclusions(results: dict[str, Any]) -> list[str]:
    by_arch: dict[str, dict[str, Any]] = {}
    for item in results["results"]:
        by_arch.setdefault(item["architecture"], {})[item["pattern"]] = item

    lines = []
    for architecture, patterns in sorted(by_arch.items()):
        sequential = patterns.get("sequential", {}).get("throughput_rps", 0.0)
        burst = patterns.get("concurrent_burst", {}).get("throughput_rps", 0.0)
        if sequential and burst > sequential:
            lines.append(
                f"{architecture}: concurrent burst improved throughput by {burst / sequential:.2f}x over sequential callers."
            )
        else:
            lines.append(f"{architecture}: concurrency did not improve throughput in this run.")
    lines.append(
        "Compare LLM task counts with latency percentiles before optimizing; lower latency may come from caller concurrency, internal agent parallelism, or fewer LLM tasks."
    )
    return lines


def aggregate_task_counts(results: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for item in results["results"]:
        counts.update(item.get("llm_task_call_counts", {}))
    return counts
