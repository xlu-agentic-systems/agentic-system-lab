import asyncio
from pathlib import Path

from app.llm import RuleBasedLlmClient
from app.return_models import ReturnConversationRequest
from app.return_service import ReturnConversationService
from app.session_store import JsonSessionStore


def run(coro):
    return asyncio.run(coro)


def service(tmp_path: Path) -> ReturnConversationService:
    return ReturnConversationService(
        session_store=JsonSessionStore(tmp_path / "sessions.json"),
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
    response = run(
        service(tmp_path).handle_message(
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
