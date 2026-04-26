from __future__ import annotations

import logging
import re
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
    EvaluationBenchmarkResult,
    GeneratedTestCase,
    LabeledCaseResult,
    Project1FeedbackRunResult,
    Project1TraceImportResult,
    PromptPatch,
    PromptPatchPromotionResult,
    QualityAssessment,
    QualityGate,
)
from project3_adaptive_eval_system.app.project1_feedback import Project1FeedbackAdapter
from project3_adaptive_eval_system.app.store import (
    ConversationTraceStore,
    DEFAULT_LABELED_CASE_PATH,
    DEFAULT_PROMPT_CANDIDATE_DIR,
    GeneratedTestStore,
    LabeledCaseStore,
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
        prompt_candidate_dir: Path | str = DEFAULT_PROMPT_CANDIDATE_DIR,
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
        self.prompt_candidate_dir = Path(prompt_candidate_dir)

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

        quality_assessment = assess_quality()
        self.generated_test_store.save_tests(generated_tests)
        prompt_patches = self.prompt_patch_store.save_patches(prompt_patches)
        self._write_report(evaluations, generated_tests, prompt_patches, quality_assessment)
        return EvaluationRunResult(
            trace_count=len(traces),
            evaluations=evaluations,
            generated_tests=generated_tests,
            prompt_patches=prompt_patches,
            report_path=str(self.report_path),
            quality_assessment=quality_assessment,
        )

    def review_prompt_patch(self, patch_id: str, approved: bool) -> PromptPatch:
        return self.prompt_patch_store.review_patch(patch_id, approved)

    async def run_labeled_benchmark(self, case_path: str | None = None) -> EvaluationBenchmarkResult:
        cases = LabeledCaseStore(case_path or DEFAULT_LABELED_CASE_PATH).load_cases()
        case_results: list[LabeledCaseResult] = []
        matched_pass_fail_count = 0
        expected_issue_total = 0
        matched_issue_total = 0
        patch_target_checks = 0
        matched_patch_targets = 0

        for case in cases:
            evaluation = _normalize_evaluation(await self.evaluation_agent.run(case.trace))
            detected_categories = [issue.category for issue in evaluation.detected_issues]
            matched_pass_fail = evaluation.passed == case.expected_passed
            matched_regression = evaluation.requires_regression == case.expected_requires_regression
            expected_categories = set(case.expected_issue_categories)
            detected_set = set(detected_categories)
            matched_issue_categories = expected_categories.issubset(detected_set)
            expected_issue_total += len(expected_categories)
            matched_issue_total += len(expected_categories.intersection(detected_set))

            matched_patch_target: bool | None = None
            if case.expected_patch_target is not None:
                patch_target_checks += 1
                patch = await self.prompt_improvement_agent.run(case.trace, evaluation)
                matched_patch_target = patch.target_prompt == case.expected_patch_target
                if matched_patch_target:
                    matched_patch_targets += 1

            if (
                matched_pass_fail
                and matched_regression
                and matched_issue_categories
                and matched_patch_target is not False
            ):
                matched_pass_fail_count += 1

            case_results.append(
                LabeledCaseResult(
                    case_id=case.case_id,
                    trace_id=case.trace.trace_id,
                    passed=evaluation.passed,
                    expected_passed=case.expected_passed,
                    requires_regression=evaluation.requires_regression,
                    expected_requires_regression=case.expected_requires_regression,
                    detected_issue_categories=detected_categories,
                    expected_issue_categories=case.expected_issue_categories,
                    matched_pass_fail=matched_pass_fail,
                    matched_regression=matched_regression,
                    matched_issue_categories=matched_issue_categories,
                    matched_patch_target=matched_patch_target,
                )
            )

        pass_fail_accuracy = _ratio(
            sum(1 for result in case_results if result.matched_pass_fail),
            len(case_results),
        )
        issue_category_recall = _ratio(matched_issue_total, expected_issue_total)
        patch_target_accuracy = (
            _ratio(matched_patch_targets, patch_target_checks) if patch_target_checks else None
        )
        result = EvaluationBenchmarkResult(
            case_count=len(case_results),
            passed_cases=matched_pass_fail_count,
            pass_fail_accuracy=pass_fail_accuracy,
            issue_category_recall=issue_category_recall,
            patch_target_accuracy=patch_target_accuracy,
            case_results=case_results,
        )
        return result.model_copy(update={"quality_assessment": assess_quality(result)})

    def promote_prompt_patch_candidate(
        self,
        patch_id: str,
        *,
        source_prompt_path: str | None = None,
    ) -> PromptPatchPromotionResult:
        patch = self.prompt_patch_store.get_patch(patch_id)
        source_path = _resolve_source_prompt_path(patch, source_prompt_path)
        candidate_path = _candidate_path(patch_id, self.prompt_candidate_dir)
        messages = _validate_prompt_promotion(patch, source_path)
        validation_passed = not messages
        if validation_passed:
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            source_text = source_path.read_text()
            candidate_text = _candidate_prompt_text(source_text, patch)
            candidate_path.write_text(candidate_text)
            messages.append("candidate prompt written for review")
        return PromptPatchPromotionResult(
            patch_id=patch_id,
            source_prompt_path=str(source_path),
            candidate_prompt_path=str(candidate_path),
            validation_passed=validation_passed,
            validation_messages=messages,
        )

    def _write_report(
        self,
        evaluations: list[EvaluationResult],
        generated_tests: list[GeneratedTestCase],
        prompt_patches: list[PromptPatch],
        quality_assessment: QualityAssessment,
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
            f"Evaluation depth score: {quality_assessment.evaluation_depth_score}",
            f"Production readiness score: {quality_assessment.production_readiness_score}",
            "",
            "## Quality Gates",
            "",
        ]
        for gate in quality_assessment.gates:
            lines.append(f"- {gate.name}: {gate.status} - {gate.summary}")
        lines.extend(
            [
                "",
                "## Trace Results",
                "",
            ]
        )
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


def assess_quality(benchmark: EvaluationBenchmarkResult | None = None) -> QualityAssessment:
    gates = [
        QualityGate(
            name="human_review_required",
            status="passed",
            summary="Prompt patches remain proposed until an explicit review call approves or rejects them.",
        ),
        QualityGate(
            name="candidate_prompt_only",
            status="passed",
            summary="Approved patches can be rendered to a candidate prompt file without mutating production prompts.",
        ),
        QualityGate(
            name="labeled_eval_harness",
            status="passed" if benchmark is not None and benchmark.case_count > 0 else "failed",
            summary=(
                "Labeled benchmark cases measure pass/fail, issue-category recall, and patch target accuracy."
                if benchmark is not None and benchmark.case_count > 0
                else "Run the labeled benchmark to measure evaluation quality."
            ),
        ),
    ]
    evaluation_depth = 7.5
    production_readiness = 7.0
    if benchmark is not None and benchmark.case_count > 0:
        patch_accuracy = benchmark.patch_target_accuracy
        if patch_accuracy is None:
            patch_accuracy = 1.0
        evaluation_depth = round(
            5.0
            + 2.0 * benchmark.pass_fail_accuracy
            + 1.0 * benchmark.issue_category_recall
            + 0.5 * patch_accuracy,
            2,
        )
        production_readiness = 7.0
        if benchmark.pass_fail_accuracy >= 0.8 and benchmark.issue_category_recall >= 0.8:
            production_readiness += 0.5
    return QualityAssessment(
        evaluation_depth_score=min(evaluation_depth, 10.0),
        production_readiness_score=min(production_readiness, 10.0),
        gates=gates,
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 4)


def _resolve_source_prompt_path(patch: PromptPatch, override: str | None) -> Path:
    selected = override
    if selected is None:
        selected = patch.target_prompt.split("#", maxsplit=1)[0]
    source_path = Path(selected)
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path
    return source_path.resolve()


def _candidate_path(patch_id: str, candidate_dir: Path) -> Path:
    safe_patch_id = re.sub(r"[^A-Za-z0-9_.-]", "_", patch_id)
    return candidate_dir / f"{safe_patch_id}.md"


def _validate_prompt_promotion(patch: PromptPatch, source_path: Path) -> list[str]:
    messages: list[str] = []
    if patch.status != "approved":
        messages.append("patch must be approved before candidate prompt generation")
    if not source_path.exists():
        messages.append(f"source prompt does not exist: {source_path}")
    try:
        source_path.relative_to(Path.cwd())
    except ValueError:
        messages.append("source prompt must be inside the repository")
    if not patch.proposed_instruction.strip():
        messages.append("patch instruction is empty")
    if "auto" in patch.proposed_instruction.lower() and "apply" in patch.proposed_instruction.lower():
        messages.append("patch instruction appears to request automatic application")
    return messages


def _candidate_prompt_text(source_text: str, patch: PromptPatch) -> str:
    return "\n".join(
        [
            source_text.rstrip(),
            "",
            "## Candidate Prompt Patch",
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
        ]
    )
