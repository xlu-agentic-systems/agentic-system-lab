from __future__ import annotations

import logging
from pathlib import Path

from agentic_system_lab.observability import log_agent_event
from project3_adaptive_eval_system.app.agents import (
    EvaluationAgent,
    PromptImprovementAgent,
    TestCaseGenerator,
)
from project3_adaptive_eval_system.app.llm import LlmClient, OpenAILlmClient
from project3_adaptive_eval_system.app.models import (
    ConversationTrace,
    EvaluationRunResult,
    EvaluationResult,
    GeneratedTestCase,
    Project1FeedbackRunResult,
    Project1TraceImportResult,
    PromptPatch,
)
from project3_adaptive_eval_system.app.project1_feedback import Project1FeedbackAdapter
from project3_adaptive_eval_system.app.store import (
    ConversationTraceStore,
    GeneratedTestStore,
    PROJECT_ROOT,
    PromptPatchStore,
)


DEFAULT_REPORT_PATH = PROJECT_ROOT / "evaluation_report.md"
logger = logging.getLogger(__name__)


class EvaluationService:
    def __init__(
        self,
        *,
        trace_store: ConversationTraceStore | None = None,
        generated_test_store: GeneratedTestStore | None = None,
        prompt_patch_store: PromptPatchStore | None = None,
        report_path: Path | str = DEFAULT_REPORT_PATH,
        llm_client: LlmClient | None = None,
        project1_adapter: Project1FeedbackAdapter | None = None,
    ) -> None:
        self.trace_store = trace_store or ConversationTraceStore()
        self.generated_test_store = generated_test_store or GeneratedTestStore()
        self.prompt_patch_store = prompt_patch_store or PromptPatchStore()
        self.report_path = Path(report_path)
        self.llm_client = llm_client or OpenAILlmClient()
        self.evaluation_agent = EvaluationAgent(self.llm_client)
        self.test_case_generator = TestCaseGenerator(self.llm_client)
        self.prompt_improvement_agent = PromptImprovementAgent(self.llm_client)
        self.project1_adapter = project1_adapter or Project1FeedbackAdapter()

    async def run_evaluation(self, trace_limit: int | None = None) -> EvaluationRunResult:
        traces = self.trace_store.load_traces(trace_limit)
        return await self._evaluate_traces(traces, trace_limit=trace_limit)

    def import_project1_traces(
        self,
        *,
        trace_path: str | None = None,
        trace_limit: int | None = None,
        include_loki_context: bool = False,
        loki_since_minutes: int = 60,
    ) -> Project1TraceImportResult:
        adapter = self.project1_adapter
        if trace_path is not None:
            adapter = Project1FeedbackAdapter(trace_path=trace_path, log_query_tool=adapter.log_query_tool)
        imported, result = adapter.convert_traces(
            limit=trace_limit,
            include_loki_context=include_loki_context,
            loki_since_minutes=loki_since_minutes,
        )
        existing = {trace.trace_id: trace for trace in self.trace_store.load_traces()}
        for trace in imported:
            existing[trace.trace_id] = trace
        self.trace_store.write_all(existing.values())
        log_agent_event(
            logger,
            event="project1_trace_imported",
            message="project3 imported Project 1 traces for adaptive evaluation",
            agent="evaluation_service",
            attributes={
                "imported_count": result.imported_count,
                "source_path": result.source_path,
                "included_loki_context": result.included_loki_context,
            },
        )
        return result

    async def run_project1_feedback(
        self,
        *,
        trace_path: str | None = None,
        trace_limit: int | None = None,
        include_loki_context: bool = False,
        loki_since_minutes: int = 60,
    ) -> Project1FeedbackRunResult:
        adapter = self.project1_adapter
        if trace_path is not None:
            adapter = Project1FeedbackAdapter(trace_path=trace_path, log_query_tool=adapter.log_query_tool)
        imported, import_result = adapter.convert_traces(
            limit=trace_limit,
            include_loki_context=include_loki_context,
            loki_since_minutes=loki_since_minutes,
        )
        existing = {trace.trace_id: trace for trace in self.trace_store.load_traces()}
        for trace in imported:
            existing[trace.trace_id] = trace
        self.trace_store.write_all(existing.values())
        evaluation_result = await self._evaluate_traces(imported, trace_limit=trace_limit)
        return Project1FeedbackRunResult(
            import_result=import_result,
            evaluation_result=evaluation_result,
        )

    async def _evaluate_traces(
        self,
        traces: list[ConversationTrace],
        *,
        trace_limit: int | None = None,
    ) -> EvaluationRunResult:
        log_agent_event(
            logger,
            event="evaluation_run_started",
            message="project3 evaluation run started",
            agent="evaluation_service",
            attributes={"trace_count": len(traces), "trace_limit": trace_limit},
        )
        evaluations: list[EvaluationResult] = []
        generated_tests: list[GeneratedTestCase] = []
        prompt_patches: list[PromptPatch] = []

        for trace in traces:
            evaluation = await self.evaluation_agent.run(trace)
            evaluation = _normalize_evaluation(evaluation)
            evaluations.append(evaluation)
            log_agent_event(
                logger,
                event="agent_decision",
                message="project3 evaluation agent scored trace",
                agent="evaluation_agent",
                session_id=trace.session_id,
                status="passed" if evaluation.passed else "failed",
                attributes={
                    "trace_id": trace.trace_id,
                    "overall_score": evaluation.overall_score,
                    "issue_count": len(evaluation.detected_issues),
                    "requires_regression": evaluation.requires_regression,
                },
            )
            if not evaluation.requires_regression:
                continue
            generated_tests.append(await self.test_case_generator.run(trace, evaluation))
            prompt_patches.append(await self.prompt_improvement_agent.run(trace, evaluation))
            log_agent_event(
                logger,
                event="agent_decision",
                message="project3 improvement agents proposed regression artifacts",
                agent="prompt_improvement_agent",
                session_id=trace.session_id,
                status="prompt_patch_proposed",
                attributes={"trace_id": trace.trace_id},
            )

        self.generated_test_store.save_tests(generated_tests)
        prompt_patches = self.prompt_patch_store.save_patches(prompt_patches)
        self._write_report(evaluations, generated_tests, prompt_patches)
        return EvaluationRunResult(
            trace_count=len(traces),
            evaluations=evaluations,
            generated_tests=generated_tests,
            prompt_patches=prompt_patches,
            report_path=str(self.report_path),
        )

    def review_prompt_patch(self, patch_id: str, approved: bool) -> PromptPatch:
        return self.prompt_patch_store.review_patch(patch_id, approved)

    def _write_report(
        self,
        evaluations: list[EvaluationResult],
        generated_tests: list[GeneratedTestCase],
        prompt_patches: list[PromptPatch],
    ) -> None:
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        passed = sum(1 for evaluation in evaluations if evaluation.passed)
        lines = [
            "# Evaluation Report",
            "",
            f"Traces evaluated: {len(evaluations)}",
            f"Passed: {passed}",
            f"Failed: {len(evaluations) - passed}",
            f"Generated regression tests: {len(generated_tests)}",
            f"Prompt patches proposed: {len(prompt_patches)}",
            "",
            "## Trace Results",
            "",
        ]
        for evaluation in evaluations:
            lines.extend(
                [
                    f"### {evaluation.trace_id}",
                    "",
                    f"- Passed: {evaluation.passed}",
                    f"- Overall score: {evaluation.overall_score}",
                    f"- Summary: {evaluation.summary}",
                ]
            )
            for issue in evaluation.detected_issues:
                lines.append(
                    f"- Issue: {issue.category} ({issue.severity}) - {issue.description}"
                )
            lines.append("")

        if prompt_patches:
            lines.extend(["## Proposed Prompt Patches", ""])
            for patch in prompt_patches:
                lines.extend(
                    [
                        f"### {patch.patch_id}",
                        "",
                        f"- Target: {patch.target_prompt}",
                        f"- Status: {patch.status}",
                        f"- Instruction: {patch.proposed_instruction}",
                        f"- Rationale: {patch.rationale}",
                        "",
                    ]
                )

        self.report_path.write_text("\n".join(lines))


def _normalize_evaluation(evaluation: EvaluationResult) -> EvaluationResult:
    if evaluation.passed and not evaluation.detected_issues:
        return evaluation.model_copy(
            update={
                "overall_score": max(evaluation.overall_score, 4),
                "requires_regression": False,
            }
        )
    return evaluation.model_copy(
        update={
            "passed": False,
            "overall_score": min(evaluation.overall_score, 3),
            "requires_regression": True,
        }
    )
