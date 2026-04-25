import asyncio
from pathlib import Path

from app.llm import RuleBasedLlmClient
from app.models import ConversationRequest
from app.service import ConversationService
from app.session_store import JsonSessionStore


def run(coro):
    return asyncio.run(coro)


def service(tmp_path: Path) -> ConversationService:
    return ConversationService(
        session_store=JsonSessionStore(tmp_path / "sessions.json"),
        llm_client=RuleBasedLlmClient(),
    )


def test_parallel_payment_and_return_routing(tmp_path: Path) -> None:
    response = run(
        service(tmp_path).handle_message(
            ConversationRequest(
                session_id="parallel",
                user_id="user-1",
                message="I was charged twice and I also want to return the shoes.",
            )
        )
    )

    assert response.selected_agents == ["return_agent", "payment_agent"]
    assert response.execution_plan.mode == "parallel"
    assert {result.agent for result in response.agent_results} == {"return_agent", "payment_agent"}
    return_result = next(result for result in response.agent_results if result.agent == "return_agent")
    assert return_result.proposed_actions[0].name == "start_return_authorization"
    assert return_result.proposed_actions[0].requires_approval is True
    assert "eligible for return" in response.final_response
    assert "duplicate charge" in response.final_response


def test_orchestrator_uses_llm_boundary(tmp_path: Path) -> None:
    llm_client = RuleBasedLlmClient()
    response = run(
        ConversationService(
            session_store=JsonSessionStore(tmp_path / "sessions.json"),
            llm_client=llm_client,
        ).handle_message(
            ConversationRequest(
                session_id="llm-boundary",
                user_id="user-1",
                message="I want a refund for item-1 from order-1001",
            )
        )
    )

    assert llm_client.calls == ["orchestrator_routing", "return_agent"]
    assert response.selected_agents == ["return_agent"]
    assert response.agent_results[0].reason_codes == ["return_eligible"]


def test_independent_return_and_no_duplicate_charge_does_not_escalate(tmp_path: Path) -> None:
    response = run(
        service(tmp_path).handle_message(
            ConversationRequest(
                session_id="parallel-no-conflict",
                user_id="user-1",
                message="I was charged once and I want to return item-1 from order-1001.",
            )
        )
    )

    assert response.selected_agents == ["return_agent", "payment_agent"]
    assert response.execution_plan.mode == "parallel"
    assert {result.agent for result in response.agent_results} == {"return_agent", "payment_agent"}
    assert "support ticket" not in response.final_response


def test_single_shipping_agent_routing(tmp_path: Path) -> None:
    response = run(
        service(tmp_path).handle_message(
            ConversationRequest(
                session_id="single",
                user_id="user-1",
                message="Where is package order-1004?",
            )
        )
    )

    assert response.selected_agents == ["shipping_agent"]
    assert response.execution_plan.mode == "single_agent"
    assert response.agent_results[0].agent == "shipping_agent"
    assert "delayed" in response.final_response


def test_sequential_shipping_payment_flow_escalates(tmp_path: Path) -> None:
    response = run(
        service(tmp_path).handle_message(
            ConversationRequest(
                session_id="sequential",
                user_id="user-1",
                message="My package order-1004 is delayed and I want a refund status update.",
            )
        )
    )

    assert response.selected_agents == ["shipping_agent", "payment_agent"]
    assert response.execution_plan.mode == "sequential"
    assert [step.agents for step in response.execution_plan.steps] == [
        ["shipping_agent"],
        ["payment_agent"],
    ]
    assert response.agent_results[-1].agent == "escalation_agent"
    assert response.agent_results[-1].backend_actions[0].ok is True
    assert "support ticket" in response.final_response
