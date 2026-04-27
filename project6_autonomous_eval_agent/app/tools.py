from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from project3_adaptive_eval_system.app.models import (
    EvaluationBenchmarkResult,
    EvaluationRunResult,
    PromptPatch,
)
from project3_adaptive_eval_system.app.service import EvaluationService
from project3_adaptive_eval_system.app.store import PromptPatchStore


class ToolState(BaseModel):
    evaluation_result: EvaluationRunResult | None = None
    benchmark_result: EvaluationBenchmarkResult | None = None
    candidate_prompt_paths: list[str] = []


class ToolExecution(BaseModel):
    ok: bool
    summary: str


class AutonomousEvalTools:
    def __init__(
        self,
        *,
        service: EvaluationService,
        prompt_patch_store: PromptPatchStore,
        run_dir: Path,
    ) -> None:
        self.service = service
        self.prompt_patch_store = prompt_patch_store
        self.run_dir = run_dir
        self.state = ToolState()

    async def evaluate_traces(self, trace_limit: int | None = None) -> ToolExecution:
        result = await self.service.run_evaluation(trace_limit=trace_limit)
        self.state.evaluation_result = result
        return ToolExecution(
            ok=True,
            summary=(
                f"evaluated {result.trace_count} traces, generated "
                f"{len(result.generated_tests)} tests and {len(result.prompt_patches)} prompt patches"
            ),
        )

    async def run_labeled_benchmark(self) -> ToolExecution:
        result = await self.service.run_labeled_benchmark()
        self.state.benchmark_result = result
        return ToolExecution(
            ok=True,
            summary=(
                f"benchmark cases={result.case_count}, pass_fail_accuracy={result.pass_fail_accuracy}, "
                f"issue_category_recall={result.issue_category_recall}"
            ),
        )

    async def generate_candidate_prompts(self) -> ToolExecution:
        patches = self.prompt_patch_store.read(PromptPatch)
        candidate_dir = self.run_dir / "prompt_candidates"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for patch in patches:
            source_path = _resolve_source_prompt_path(patch)
            if not source_path.exists():
                continue
            candidate_path = candidate_dir / f"{_safe_filename(patch.patch_id)}.md"
            candidate_path.write_text(_candidate_prompt_text(source_path.read_text(), patch))
            paths.append(str(candidate_path))
        self.state.candidate_prompt_paths = paths
        return ToolExecution(
            ok=True,
            summary=f"generated {len(paths)} non-production candidate prompt files",
        )


def _resolve_source_prompt_path(patch: PromptPatch) -> Path:
    source = patch.target_prompt.split("#", maxsplit=1)[0]
    path = Path(source)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def _candidate_prompt_text(source_text: str, patch: PromptPatch) -> str:
    return "\n".join(
        [
            source_text.rstrip(),
            "",
            "## Autonomous Candidate Patch",
            "",
            f"Patch ID: {patch.patch_id}",
            f"Trace ID: {patch.trace_id}",
            f"Target: {patch.target_prompt}",
            "",
            patch.proposed_instruction.strip(),
            "",
            "Rationale:",
            patch.rationale.strip(),
            "",
            "Safety:",
            "This file is an autonomous candidate artifact. It is not applied to production prompts.",
            "",
        ]
    )
