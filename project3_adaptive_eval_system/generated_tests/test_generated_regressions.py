"""Generated regression cases from Project 3 evaluation traces."""

import asyncio
import json
from pathlib import Path

from project1_multi_agent_return_bot.app.catalog import Catalog
from project1_multi_agent_return_bot.app.models import ToolCallArgs, ToolCallProposal
from project1_multi_agent_return_bot.app.policy import check_return_policy
from project1_multi_agent_return_bot.app.tools import BackendTools, validate_and_execute_tool


def _cases():
    path = Path(__file__).with_name('regression_cases.jsonl')
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _run(coro):
    return asyncio.run(coro)


def test_generated_regression_cases_have_expected_behavior():
    cases = _cases()
    if not cases:
        return
    for case in cases:
        assert case['expected_behavior']
        assert case['assertions']
        assertion_text = ' '.join(case['assertions']).lower()
        assert any(term in assertion_text for term in ('policy', 'validation', 'ownership', 'delivery date'))


def test_delivery_date_regression_replays_policy_behavior():
    cases = {case['trace_id']: case for case in _cases()}
    if 'trace-bad-purchase-date' not in cases:
        return
    catalog = Catalog()
    order = catalog.get_order('order-1002')
    assert order is not None
    order.delivered_at = catalog.today
    result = check_return_policy(catalog, 'order-1002', 'item-3')
    assert result.eligible is True
    assert 'return_window_expired' not in result.reason_codes


def test_unsafe_refund_regression_replays_ownership_gate():
    cases = {case['trace_id']: case for case in _cases()}
    if 'trace-bad-unsafe-refund' not in cases:
        return
    tools = BackendTools(Catalog())
    proposal = ToolCallProposal(
        name='issue_refund',
        args=ToolCallArgs(order_id='order-2001', item_id='item-4', amount='129.99'),
        safety='unsafe_write',
        reason='generated regression ownership check',
    )
    result = _run(validate_and_execute_tool(tools, proposal, user_id='user-1'))
    assert result.executed is False
    assert result.ok is False
    assert 'user does not own order' in (result.error or '')
    assert tools.refunds == []
