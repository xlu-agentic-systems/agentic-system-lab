import asyncio

from app.catalog import Catalog
from app.models import ToolCallProposal
from app.tools import BackendTools, validate_and_execute_tool


def run(coro):
    return asyncio.run(coro)


def refund_proposal(order_id: str, item_id: str, amount: str) -> ToolCallProposal:
    return ToolCallProposal(
        name="issue_refund",
        args={"order_id": order_id, "item_id": item_id, "amount": amount},
        safety="unsafe_write",
        reason="test refund proposal",
    )


def test_valid_refund_executes_after_backend_validation() -> None:
    tools = BackendTools(Catalog())

    result = run(
        validate_and_execute_tool(
            tools,
            refund_proposal("order-1001", "item-1", "129.99"),
            user_id="user-1",
        )
    )

    assert result.ok is True
    assert result.executed is True
    assert len(tools.refunds) == 1


def test_refund_for_another_users_order_is_blocked() -> None:
    tools = BackendTools(Catalog())

    result = run(
        validate_and_execute_tool(
            tools,
            refund_proposal("order-2001", "item-4", "129.99"),
            user_id="user-1",
        )
    )

    assert result.ok is False
    assert result.executed is False
    assert "user does not own order" in (result.error or "")
    assert tools.refunds == []


def test_refund_amount_mismatch_is_blocked() -> None:
    tools = BackendTools(Catalog())

    result = run(
        validate_and_execute_tool(
            tools,
            refund_proposal("order-1001", "item-1", "999.00"),
            user_id="user-1",
        )
    )

    assert result.ok is False
    assert result.executed is False
    assert "amount does not match" in (result.error or "")
    assert tools.refunds == []


def test_policy_ineligible_refund_is_blocked() -> None:
    tools = BackendTools(Catalog())

    result = run(
        validate_and_execute_tool(
            tools,
            refund_proposal("order-1002", "item-3", "89.00"),
            user_id="user-1",
        )
    )

    assert result.ok is False
    assert result.executed is False
    assert "policy check failed" in (result.error or "")
    assert tools.refunds == []
