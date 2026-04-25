import asyncio
from pathlib import Path

from project1_multi_agent_return_bot.app.llm import RuleBasedLlmClient
from project1_multi_agent_return_bot.app.return_models import ReturnConversationRequest
from project1_multi_agent_return_bot.app.return_service import ReturnConversationService
from project1_multi_agent_return_bot.app.session_store import JsonSessionStore
from project1_multi_agent_return_bot.app.trace_store import JsonlTraceStore


def run(coro):
    return asyncio.run(coro)


def service(tmp_path: Path) -> ReturnConversationService:
    return ReturnConversationService(
        session_store=JsonSessionStore(tmp_path / "sessions.json"),
        trace_store=JsonlTraceStore(tmp_path / "conversation_traces.jsonl"),
        llm_client=RuleBasedLlmClient(),
    )


def test_return_chatbot_asks_for_missing_fields(tmp_path: Path) -> None:
    response = run(
        service(tmp_path).handle_message(
            ReturnConversationRequest(
                session_id="clarify",
                user_id="user-1",
                message="I need a refund",
            )
        )
    )

    assert response.planner.status == "needs_clarification"
    assert response.routing.missing_fields == ["order_id", "item_id", "return_reason"]
    assert "Please provide" in response.response


def test_return_chatbot_issues_valid_refund(tmp_path: Path) -> None:
    llm_client = RuleBasedLlmClient()
    response = run(
        ReturnConversationService(
            session_store=JsonSessionStore(tmp_path / "sessions.json"),
            trace_store=JsonlTraceStore(tmp_path / "conversation_traces.jsonl"),
            llm_client=llm_client,
        ).handle_message(
            ReturnConversationRequest(
                session_id="approved",
                user_id="user-1",
                message="I want to return item-1 from order-1001 because it is damaged and get a refund",
            )
        )
    )

    assert response.planner.status == "approved"
    assert response.tool_results[-1].proposal.name == "issue_refund"
    assert response.tool_results[-1].ok is True
    assert "approved" in response.response
    assert llm_client.calls == [
        "return_routing_agent",
        "return_planner_agent",
        "return_qa_agent",
    ]


def test_return_chatbot_rejects_cross_user_order(tmp_path: Path) -> None:
    response = run(
        service(tmp_path).handle_message(
            ReturnConversationRequest(
                session_id="cross-user",
                user_id="user-1",
                message="Return item-4 from order-2001 because it is damaged and refund me",
            )
        )
    )

    assert response.planner.status == "rejected"
    assert response.planner.reason_codes == ["order_not_owned_by_user"]
    assert response.tool_results == []


def test_return_policy_question_does_not_issue_refund(tmp_path: Path) -> None:
    response = run(
        service(tmp_path).handle_message(
            ReturnConversationRequest(
                session_id="policy",
                user_id="user-1",
                message="What is the return policy for shoes?",
            )
        )
    )

    assert response.routing.intent == "return_policy_question"
    assert response.planner.reason_codes == ["policy_information_only"]
    assert [result.proposal.name for result in response.tool_results] == ["get_return_policy"]
    assert all(result.proposal.name != "issue_refund" for result in response.tool_results)
    assert "Apparel is refundable" in response.response


def test_return_chatbot_writes_durable_trace_separate_from_session(tmp_path: Path) -> None:
    trace_store = JsonlTraceStore(tmp_path / "conversation_traces.jsonl")
    response = run(
        ReturnConversationService(
            session_store=JsonSessionStore(tmp_path / "sessions.json"),
            trace_store=trace_store,
            llm_client=RuleBasedLlmClient(),
        ).handle_message(
            ReturnConversationRequest(
                session_id="trace",
                user_id="user-1",
                message="I want to return item-1 from order-1001 because it is damaged and get a refund",
            )
        )
    )

    traces = trace_store.load_all()

    assert len(traces) == 1
    assert traces[0].session_id == "trace"
    assert traces[0].routing.intent == "return_request"
    assert traces[0].planner.status == response.planner.status
    assert traces[0].tool_results[-1].proposal.name == "issue_refund"
