import asyncio
from pathlib import Path

from app.models import ConversationRequest
from app.service import ConversationService
from app.session_store import JsonSessionStore


def run(coro):
    return asyncio.run(coro)


def service(tmp_path: Path) -> ConversationService:
    return ConversationService(session_store=JsonSessionStore(tmp_path / "sessions.json"))


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
    assert "eligible for return" in response.final_response
    assert "duplicate charge" in response.final_response


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
