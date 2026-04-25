import asyncio
import json
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
from project3_adaptive_eval_system.app.project1_feedback import Project1FeedbackAdapter
from project3_adaptive_eval_system.app.service import EvaluationService
from project3_adaptive_eval_system.app.store import (
    ConversationTraceStore,
    GeneratedTestStore,
    PromptPatchStore,
)


def run(coro):
    return asyncio.run(coro)


def test_project1_feedback_keeps_success_trace_passed_with_grafana_context(tmp_path: Path) -> None:
    trace_path = _write_project1_trace(tmp_path, _project1_refund_trace(refund_executed=True))
    loki_tool = StaticLokiTool(_loki_response(session_id="demo-session-2", refund_status="executed"))
    eval_service = _service(tmp_path, trace_path, loki_tool=loki_tool)

    result = run(eval_service.run_project1_feedback(include_loki_context=True))

    assert result.evaluation_result.evaluations[0].passed is True
    assert result.evaluation_result.generated_tests == []
    assert result.evaluation_result.prompt_patches == []
    assert loki_tool.calls[0]["session_id"] == "demo-session-2"

    imported_trace = eval_service.trace_store.load_traces()[0]
    loki_summary = _loki_summary(imported_trace)
    assert "events=agent_decision=3,tool_execution=4,user_message=1" in loki_summary
    assert "agent_statuses=planner_agent:approved=1" in loki_summary
    assert "tool_statuses=" in loki_summary
    assert "issue_refund:executed=1" in loki_summary


def test_project1_feedback_uses_grafana_logs_in_prompt_patch_rationale(tmp_path: Path) -> None:
    trace_path = _write_project1_trace(tmp_path, _project1_refund_trace(refund_executed=False))
    loki_tool = StaticLokiTool(_loki_response(session_id="demo-session-2", refund_status="blocked"))
    eval_service = _service(tmp_path, trace_path, loki_tool=loki_tool)
    production_prompt_path = Path("prompts/project1_multi_agent.md")
    prompt_before = production_prompt_path.read_text()

    result = run(eval_service.run_project1_feedback(include_loki_context=True))

    assert production_prompt_path.read_text() == prompt_before
    assert result.evaluation_result.evaluations[0].requires_regression is True

    patch = result.evaluation_result.prompt_patches[0]
    assert patch.target_prompt == "prompts/project1_multi_agent.md#Planner Agent"
    assert "backend validation" in patch.proposed_instruction.lower()
    assert "Loki/Grafana context" in patch.rationale
    assert "issue_refund:blocked=1" in patch.rationale

    generated_test = result.evaluation_result.generated_tests[0]
    assert any("Loki/Grafana logs" in assertion for assertion in generated_test.assertions)


def test_project3_system_prompts_are_grafana_log_aware(tmp_path: Path) -> None:
    trace_path = _write_project1_trace(tmp_path, _project1_refund_trace(refund_executed=False))
    llm_client = CapturingLlmClient()
    eval_service = _service(
        tmp_path,
        trace_path,
        loki_tool=StaticLokiTool(_loki_response(session_id="demo-session-2", refund_status="blocked")),
        llm_client=llm_client,
    )

    run(eval_service.run_project1_feedback(include_loki_context=True))

    assert "Grafana/Loki observability evidence" in llm_client.prompts["evaluation_agent"]
    assert "observable handoffs" in llm_client.prompts["test_case_generator"]
    assert "Grafana/Loki context" in llm_client.prompts["prompt_improvement_agent"]


class CapturingLlmClient:
    def __init__(self) -> None:
        self.delegate = RuleBasedLlmClient()
        self.prompts: dict[str, str] = {}

    async def parse(self, *, task_name, system_prompt, user_payload, response_model):
        self.prompts[task_name] = system_prompt
        return await self.delegate.parse(
            task_name=task_name,
            system_prompt=system_prompt,
            user_payload=user_payload,
            response_model=response_model,
        )


class StaticLokiTool:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    def query_agent_events(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _service(
    tmp_path: Path,
    trace_path: Path,
    *,
    loki_tool: StaticLokiTool,
    llm_client=None,
) -> EvaluationService:
    return EvaluationService(
        trace_store=ConversationTraceStore(tmp_path / "project3_traces.jsonl"),
        generated_test_store=GeneratedTestStore(tmp_path / "generated" / "regression_cases.jsonl"),
        prompt_patch_store=PromptPatchStore(tmp_path / "patches.jsonl"),
        report_path=tmp_path / "evaluation_report.md",
        llm_client=llm_client or RuleBasedLlmClient(),
        project1_adapter=Project1FeedbackAdapter(trace_path=trace_path, log_query_tool=loki_tool),
    )


def _write_project1_trace(tmp_path: Path, trace: ReturnConversationTrace) -> Path:
    trace_path = tmp_path / "project1_traces.jsonl"
    trace_path.write_text(trace.model_dump_json() + "\n")
    return trace_path


def _project1_refund_trace(*, refund_executed: bool) -> ReturnConversationTrace:
    proposals = [
        ToolCallProposal(
            name="get_order",
            args=ToolCallArgs(order_id="order-1001"),
            safety="read_only",
            reason="Load order facts.",
        ),
        ToolCallProposal(
            name="get_return_policy",
            args=ToolCallArgs(category="shoes"),
            safety="read_only",
            reason="Load return policy.",
        ),
        ToolCallProposal(
            name="check_refund_eligibility",
            args=ToolCallArgs(order_id="order-1001", item_id="item-1"),
            safety="read_only",
            reason="Verify refund eligibility.",
        ),
        ToolCallProposal(
            name="issue_refund",
            args=ToolCallArgs(order_id="order-1001", item_id="item-1", amount="89.99"),
            safety="unsafe_write",
            reason="Planner requested a refund.",
        ),
    ]
    tool_results = [
        ToolExecutionResult(proposal=proposals[0], executed=True, ok=True, result={"order_id": "order-1001"}),
        ToolExecutionResult(proposal=proposals[1], executed=True, ok=True, result={"window_days": 30}),
        ToolExecutionResult(
            proposal=proposals[2],
            executed=True,
            ok=True,
            result={"eligible": True, "amount": "89.99"},
        ),
        ToolExecutionResult(
            proposal=proposals[3],
            executed=refund_executed,
            ok=refund_executed,
            result={"refund_id": "refund-1"} if refund_executed else None,
            error=None if refund_executed else "refund blocked: user does not own order",
        ),
    ]
    return ReturnConversationTrace(
        session_id="demo-session-2",
        user_id="user-1",
        user_message="I want to return item-1 from order-1001 because it is defective.",
        response=(
            "Your refund was issued."
            if refund_executed
            else "No refund was issued because backend validation blocked the request."
        ),
        routing=RoutingOutput(
            intent="return_request",
            extracted_fields=ReturnContext(
                order_id="order-1001",
                item_id="item-1",
                return_reason="defective",
                refund_requested=True,
            ),
            missing_fields=[],
        ),
        planner=PlannerOutput(
            status="approved",
            reason_codes=[],
            explanation="Planner approved after checking order, policy, and eligibility.",
            proposed_tool_calls=proposals,
        ),
        tool_results=tool_results,
    )


def _loki_response(*, session_id: str, refund_status: str) -> dict:
    entries = [
        _loki_entry(event="user_message", session_id=session_id),
        _loki_entry(event="agent_decision", session_id=session_id, agent="routing_agent", status="return_request"),
        _loki_entry(event="agent_decision", session_id=session_id, agent="planner_agent", status="approved"),
        _loki_entry(event="tool_execution", session_id=session_id, tool_name="get_order", status="executed"),
        _loki_entry(event="tool_execution", session_id=session_id, tool_name="get_return_policy", status="executed"),
        _loki_entry(
            event="tool_execution",
            session_id=session_id,
            tool_name="check_refund_eligibility",
            status="executed",
        ),
        _loki_entry(event="tool_execution", session_id=session_id, tool_name="issue_refund", status=refund_status),
        _loki_entry(event="agent_decision", session_id=session_id, agent="qa_agent", status="approved"),
    ]
    return {"response": {"data": {"result": entries}}}


def _loki_entry(
    *,
    event: str,
    session_id: str,
    agent: str | None = None,
    tool_name: str | None = None,
    status: str | None = None,
) -> dict:
    labels = {
        "project": "project1",
        "event": event,
        "agentic_event_event": event,
        "session_id": session_id,
        "agentic_event_session_id": session_id,
    }
    payload = {
        "project": "project1",
        "message": f"project1 {event}",
        "agentic_event": {
            "event": event,
            "session_id": session_id,
            "user_id": "user-1",
        },
        "event": event,
        "session_id": session_id,
        "user_id": "user-1",
    }
    if agent is not None:
        labels["agent"] = agent
        labels["agentic_event_agent"] = agent
        payload["agentic_event"]["agent"] = agent
        payload["agent"] = agent
    if tool_name is not None:
        labels["tool_name"] = tool_name
        labels["agentic_event_tool_name"] = tool_name
        payload["agentic_event"]["tool_name"] = tool_name
        payload["tool_name"] = tool_name
    if status is not None:
        labels["status"] = status
        labels["agentic_event_status"] = status
        payload["agentic_event"]["status"] = status
        payload["status"] = status
    return {"stream": labels, "values": [["1777154677393106000", json.dumps(payload)]]}


def _loki_summary(trace) -> str:
    for validation in trace.backend_validations:
        if validation.check_name == "loki_context":
            return validation.summary
    raise AssertionError("missing loki_context validation")
