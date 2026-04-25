import asyncio
from pathlib import Path

from project1_multi_agent_return_bot.app.models import (
    PlannerOutput,
    ReturnContext,
    RoutingOutput,
    ToolCallArgs,
    ToolCallProposal,
    ToolExecutionResult,
)
from project1_multi_agent_return_bot.app.trace_store import ReturnConversationTrace
from project3_adaptive_eval_system.app.llm import RuleBasedLlmClient
from project3_adaptive_eval_system.app.models import ConversationTrace
from project3_adaptive_eval_system.app.project1_feedback import Project1FeedbackAdapter
from project3_adaptive_eval_system.app.service import EvaluationService
from project3_adaptive_eval_system.app.store import (
    ConversationTraceStore,
    GeneratedTestStore,
    PromptPatchStore,
)


def run(coro):
    return asyncio.run(coro)


def trace(trace_id: str, outcome: str, *, final_response: str, expected_behavior: str) -> ConversationTrace:
    return ConversationTrace(
        trace_id=trace_id,
        project="project1_multi_agent_return_bot",
        session_id=trace_id,
        user_id="user-1",
        user_message="Can I return this item?",
        final_response=final_response,
        outcome=outcome,
        expected_behavior=expected_behavior,
        policy_basis="Return eligibility is based on delivery date.",
    )


def service(tmp_path: Path, traces: list[ConversationTrace]) -> EvaluationService:
    trace_store = ConversationTraceStore(tmp_path / "traces.jsonl")
    trace_store.write_all(traces)
    return EvaluationService(
        trace_store=trace_store,
        generated_test_store=GeneratedTestStore(tmp_path / "generated" / "regression_cases.jsonl"),
        prompt_patch_store=PromptPatchStore(tmp_path / "patches.jsonl"),
        report_path=tmp_path / "evaluation_report.md",
        llm_client=RuleBasedLlmClient(),
    )


def test_evaluation_generates_regression_and_prompt_patch_for_failure(tmp_path: Path) -> None:
    result = run(
        service(
            tmp_path,
            [
                trace(
                    "trace-bad",
                    "failure",
                    final_response="Rejected by purchase date.",
                    expected_behavior="Return eligibility must be calculated from delivery date unless policy says otherwise.",
                )
            ],
        ).run_evaluation()
    )

    assert result.trace_count == 1
    assert result.evaluations[0].requires_regression is True
    assert result.generated_tests[0].trace_id == "trace-bad"
    assert result.prompt_patches[0].status == "proposed"
    assert "delivery date" in result.prompt_patches[0].proposed_instruction


def test_successful_trace_does_not_generate_prompt_patch(tmp_path: Path) -> None:
    result = run(
        service(
            tmp_path,
            [
                trace(
                    "trace-good",
                    "success",
                    final_response="Approved using delivery date.",
                    expected_behavior="Use delivery date for return-window eligibility.",
                )
            ],
        ).run_evaluation()
    )

    assert result.evaluations[0].passed is True
    assert result.evaluations[0].overall_score >= 4
    assert result.generated_tests == []
    assert result.prompt_patches == []


def test_prompt_patch_requires_explicit_human_review(tmp_path: Path) -> None:
    eval_service = service(
        tmp_path,
        [
            trace(
                "trace-bad",
                "failure",
                final_response="Rejected by purchase date.",
                expected_behavior="Return eligibility must be calculated from delivery date unless policy says otherwise.",
            )
        ],
    )
    result = run(eval_service.run_evaluation())

    patch = result.prompt_patches[0]
    assert patch.status == "proposed"

    reviewed = eval_service.review_prompt_patch(patch.patch_id, approved=True)

    assert reviewed.status == "approved"


def test_reviewed_prompt_patch_status_survives_regeneration(tmp_path: Path) -> None:
    eval_service = service(
        tmp_path,
        [
            trace(
                "trace-bad",
                "failure",
                final_response="Rejected by purchase date.",
                expected_behavior="Return eligibility must be calculated from delivery date unless policy says otherwise.",
            )
        ],
    )
    result = run(eval_service.run_evaluation())
    patch_id = result.prompt_patches[0].patch_id
    eval_service.review_prompt_patch(patch_id, approved=False)

    regenerated = run(eval_service.run_evaluation())

    assert regenerated.prompt_patches[0].status == "rejected"


def test_trace_limit_uses_explicit_zero_as_empty(tmp_path: Path) -> None:
    trace_store = ConversationTraceStore(tmp_path / "traces.jsonl")
    trace_store.write_all(
        [
            trace(
                "trace-good",
                "success",
                final_response="Approved using delivery date.",
                expected_behavior="Use delivery date.",
            )
        ]
    )

    assert trace_store.load_traces(0) == []


def test_openai_llm_client_uses_responses_parse_with_structured_output() -> None:
    from project3_adaptive_eval_system.app.llm import OpenAILlmClient
    from project3_adaptive_eval_system.app.models import EvaluationResult

    class FakeResponses:
        def __init__(self) -> None:
            self.kwargs = None

        async def parse(self, **kwargs):
            self.kwargs = kwargs
            return type(
                "FakeResponse",
                (),
                {
                    "output_parsed": EvaluationResult(
                        trace_id="trace-good",
                        passed=True,
                        overall_score=5,
                        scores={
                            "intent_understanding": 5,
                            "policy_correctness": 5,
                            "tool_safety": 5,
                            "response_helpfulness": 5,
                            "escalation_correctness": 5,
                            "latency_awareness": 4,
                            "context_preservation": 5,
                        },
                        summary="ok",
                        requires_regression=False,
                    )
                },
            )()

    class FakeOpenAI:
        def __init__(self) -> None:
            self.responses = FakeResponses()

    fake_client = FakeOpenAI()
    llm_client = OpenAILlmClient(model="test-model")
    llm_client._client = fake_client

    parsed = run(
        llm_client.parse(
            task_name="evaluation_agent",
            system_prompt="system",
            user_payload={"trace": {"trace_id": "trace-good"}},
            response_model=EvaluationResult,
        )
    )

    assert parsed.trace_id == "trace-good"
    assert fake_client.responses.kwargs["model"] == "test-model"
    assert fake_client.responses.kwargs["text_format"] is EvaluationResult


def test_project1_feedback_imports_trace_and_generates_prompt_patch(tmp_path: Path) -> None:
    project1_trace_path = tmp_path / "project1_traces.jsonl"
    project1_trace_path.write_text(_project1_failed_refund_trace().model_dump_json() + "\n")
    trace_store = ConversationTraceStore(tmp_path / "project3_traces.jsonl")
    eval_service = EvaluationService(
        trace_store=trace_store,
        generated_test_store=GeneratedTestStore(tmp_path / "generated" / "regression_cases.jsonl"),
        prompt_patch_store=PromptPatchStore(tmp_path / "patches.jsonl"),
        report_path=tmp_path / "evaluation_report.md",
        llm_client=RuleBasedLlmClient(),
        project1_adapter=Project1FeedbackAdapter(
            trace_path=project1_trace_path,
            log_query_tool=FakeLokiTool(),
        ),
    )

    result = run(eval_service.run_project1_feedback(include_loki_context=True))

    assert result.import_result.imported_count == 1
    assert result.import_result.included_loki_context is True
    assert result.evaluation_result.trace_count == 1
    assert result.evaluation_result.evaluations[0].requires_regression is True
    assert result.evaluation_result.generated_tests[0].trace_id.startswith("project1-project1-session")
    assert result.evaluation_result.prompt_patches[0].target_prompt == "prompts/project1_multi_agent.md#Planner Agent"
    assert "backend validation" in result.evaluation_result.prompt_patches[0].proposed_instruction.lower()

    imported_trace = trace_store.load_traces()[0]
    assert imported_trace.project == "project1_multi_agent_return_bot"
    assert imported_trace.outcome == "failure"
    assert any(record.check_name == "loki_context" and record.passed for record in imported_trace.backend_validations)


def test_project1_trace_import_deduplicates_by_trace_id(tmp_path: Path) -> None:
    project1_trace_path = tmp_path / "project1_traces.jsonl"
    project1_trace_path.write_text(_project1_failed_refund_trace().model_dump_json() + "\n")
    trace_store = ConversationTraceStore(tmp_path / "project3_traces.jsonl")
    eval_service = EvaluationService(
        trace_store=trace_store,
        generated_test_store=GeneratedTestStore(tmp_path / "generated" / "regression_cases.jsonl"),
        prompt_patch_store=PromptPatchStore(tmp_path / "patches.jsonl"),
        report_path=tmp_path / "evaluation_report.md",
        llm_client=RuleBasedLlmClient(),
        project1_adapter=Project1FeedbackAdapter(trace_path=project1_trace_path),
    )

    first = eval_service.import_project1_traces()
    second = eval_service.import_project1_traces()

    assert first.trace_ids == second.trace_ids
    assert len(trace_store.load_traces()) == 1


class FakeLokiTool:
    def query_agent_events(self, **kwargs):
        assert kwargs["project"] == "project1"
        assert kwargs["session_id"] == "project1-session"
        return {
            "response": {
                "data": {
                    "result": [
                        {"stream": {"event": "agent_decision"}, "values": [["1", "{}"]]},
                        {"stream": {"event": "tool_execution"}, "values": [["2", "{}"]]},
                    ]
                }
            }
        }


def _project1_failed_refund_trace() -> ReturnConversationTrace:
    proposal = ToolCallProposal(
        name="issue_refund",
        args=ToolCallArgs(order_id="order-2001", item_id="item-4", amount="129.99"),
        safety="unsafe_write",
        reason="Refund requested by planner.",
    )
    return ReturnConversationTrace(
        session_id="project1-session",
        user_id="user-1",
        user_message="Please refund item-4 from order-2001.",
        response="No refund was issued because backend validation blocked the request.",
        routing=RoutingOutput(
            intent="return_request",
            extracted_fields=ReturnContext(
                order_id="order-2001",
                item_id="item-4",
                return_reason="defective",
                refund_requested=True,
            ),
            missing_fields=[],
        ),
        planner=PlannerOutput(
            status="approved",
            reason_codes=[],
            explanation="Planner approved a refund before ownership validation.",
            proposed_tool_calls=[proposal],
        ),
        tool_results=[
            ToolExecutionResult(
                proposal=proposal,
                executed=False,
                ok=False,
                error="refund blocked: user does not own order",
            )
        ],
    )
