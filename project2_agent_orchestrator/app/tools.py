from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from uuid import uuid4

from project2_agent_orchestrator.app.catalog import Catalog, catalog
from project2_agent_orchestrator.app.models import (
    BackendActionResult,
    ProposedAction,
    RefundRecord,
    SupportTicket,
    ToolCallProposal,
    ToolExecutionResult,
)
from project2_agent_orchestrator.app.policy import check_return_policy


logger = logging.getLogger(__name__)


class ToolValidationError(ValueError):
    pass


class BackendTools:
    def __init__(self, domain_catalog: Catalog = catalog) -> None:
        self.catalog = domain_catalog
        self.refunds: list[RefundRecord] = []
        self.support_tickets: list[SupportTicket] = []

    async def get_order(self, order_id: str) -> dict:
        order = self.catalog.get_order(order_id)
        return order.model_dump(mode="json") if order else {"error": "order_not_found"}

    async def get_return_policy(self, category: str) -> dict:
        policy = self.catalog.get_policy(category)
        return policy.model_dump(mode="json") if policy else {"error": "policy_not_found"}

    async def check_refund_eligibility(self, order_id: str, item_id: str) -> dict:
        result = check_return_policy(self.catalog, order_id, item_id)
        return result.model_dump(mode="json")

    async def issue_refund(self, order_id: str, item_id: str, amount: Decimal) -> dict:
        refund = RefundRecord(
            refund_id=f"refund-{uuid4().hex[:8]}",
            order_id=order_id,
            item_id=item_id,
            amount=amount,
            status="issued",
        )
        self.refunds.append(refund)
        return refund.model_dump(mode="json")

    async def create_support_ticket(self, user_id: str, reason: str) -> dict:
        ticket = SupportTicket(
            ticket_id=f"ticket-{uuid4().hex[:8]}",
            user_id=user_id,
            reason=reason,
        )
        self.support_tickets.append(ticket)
        return ticket.model_dump(mode="json")

    async def get_account_profile(self, user_id: str) -> dict:
        account = self.catalog.get_account(user_id)
        if not account:
            return {"error": "account_not_found"}
        return account.model_dump(mode="json")

    async def resolve_order_item(
        self,
        *,
        user_id: str,
        order_id: str | None = None,
        item_id: str | None = None,
        product_hint: str | None = None,
    ) -> dict:
        order, item = self.catalog.resolve_order_item(
            user_id=user_id,
            order_id=order_id,
            item_id=item_id,
            product_hint=product_hint,
        )
        if not order:
            return {"error": "order_not_found"}
        if not item:
            return {"order": order.model_dump(mode="json"), "error": "item_not_found"}
        product = self.catalog.get_product(item.product_id)
        return {
            "order": order.model_dump(mode="json"),
            "item": item.model_dump(mode="json"),
            "product": product.model_dump(mode="json") if product else None,
        }

    async def check_return_eligibility(self, order_id: str, item_id: str) -> dict:
        result = check_return_policy(self.catalog, order_id, item_id)
        return result.model_dump(mode="json")

    async def get_shipping_status(self, user_id: str, order_id: str | None = None) -> dict:
        order = self._resolve_order(user_id, order_id)
        if not order:
            return {"error": "order_not_found"}
        shipment = self.catalog.get_shipment(order.order_id)
        if not shipment:
            return {"order_id": order.order_id, "error": "shipment_not_found"}
        return shipment.model_dump(mode="json")

    async def find_duplicate_charges(self, user_id: str, order_id: str | None = None) -> dict:
        payments = self.catalog.get_user_payments(user_id, order_id)
        groups: dict[str, list[dict]] = defaultdict(list)
        for payment in payments:
            if payment.duplicate_group and payment.status == "captured":
                groups[payment.duplicate_group].append(payment.model_dump(mode="json"))
        duplicates = [items for items in groups.values() if len(items) > 1]
        return {"duplicates": duplicates, "payment_count": len(payments)}

    async def get_refund_timeline(self) -> dict:
        return {
            "timeline": "Refunds usually post to the original payment method in 5-7 business days after approval."
        }

    def _resolve_order(self, user_id: str, order_id: str | None):
        if order_id:
            order = self.catalog.get_order(order_id)
            return order if order and order.user_id == user_id else None
        orders = self.catalog.get_user_orders(user_id)
        return orders[-1] if orders else None

async def validate_and_execute_tool(
    tools: BackendTools,
    proposal: ToolCallProposal,
    *,
    user_id: str,
) -> ToolExecutionResult:
    logger.info("tool proposal: %s args=%s", proposal.name, proposal.args)
    try:
        _validate_proposal_shape(proposal)
        if proposal.name == "get_order":
            result = await tools.get_order(str(proposal.args["order_id"]))
        elif proposal.name == "get_return_policy":
            result = await tools.get_return_policy(str(proposal.args["category"]))
        elif proposal.name == "check_refund_eligibility":
            result = await tools.check_refund_eligibility(
                str(proposal.args["order_id"]),
                str(proposal.args["item_id"]),
            )
        elif proposal.name == "create_support_ticket":
            result = await tools.create_support_ticket(user_id, str(proposal.args["reason"]))
        elif proposal.name == "issue_refund":
            _validate_refund_call(tools, proposal, user_id=user_id)
            result = await tools.issue_refund(
                str(proposal.args["order_id"]),
                str(proposal.args["item_id"]),
                Decimal(str(proposal.args["amount"])),
            )
        else:
            raise ToolValidationError(f"unknown tool: {proposal.name}")
    except ToolValidationError as exc:
        logger.warning("blocked tool execution: %s", exc)
        return ToolExecutionResult(
            proposal=proposal,
            executed=False,
            ok=False,
            error=str(exc),
        )

    logger.info("tool executed: %s", proposal.name)
    return ToolExecutionResult(proposal=proposal, executed=True, ok=True, result=result)


def _validate_proposal_shape(proposal: ToolCallProposal) -> None:
    required_args = {
        "get_order": {"order_id"},
        "get_return_policy": {"category"},
        "check_refund_eligibility": {"order_id", "item_id"},
        "issue_refund": {"order_id", "item_id", "amount"},
        "create_support_ticket": {"reason"},
    }
    missing = required_args[proposal.name] - proposal.args.keys()
    if missing:
        raise ToolValidationError(f"missing tool args: {sorted(missing)}")


def _validate_refund_call(
    tools: BackendTools,
    proposal: ToolCallProposal,
    *,
    user_id: str,
) -> None:
    order_id = str(proposal.args["order_id"])
    item_id = str(proposal.args["item_id"])
    proposed_amount = Decimal(str(proposal.args["amount"]))

    order = tools.catalog.get_order(order_id)
    if order is None:
        raise ToolValidationError("refund blocked: order does not exist")
    if order.user_id != user_id:
        raise ToolValidationError("refund blocked: user does not own order")

    item = next((candidate for candidate in order.items if candidate.item_id == item_id), None)
    if item is None:
        raise ToolValidationError("refund blocked: item does not belong to order")
    if proposed_amount != item.refund_amount:
        raise ToolValidationError("refund blocked: amount does not match order item")

    eligibility = check_return_policy(tools.catalog, order_id, item_id)
    if not eligibility.eligible:
        raise ToolValidationError(
            "refund blocked: policy check failed "
            f"({', '.join(eligibility.reason_codes)})"
        )


async def validate_and_execute_action(
    tools: BackendTools,
    action: ProposedAction,
    *,
    user_id: str,
) -> BackendActionResult:
    logger.info("backend action proposal: %s args=%s", action.name, action.args)
    try:
        if action.name != "create_support_ticket":
            raise ToolValidationError("only support tickets can be executed automatically")
        if action.safety != "safe_write":
            raise ToolValidationError("unsafe action requires explicit human approval")
        reason = str(action.args.get("reason", "")).strip()
        if not reason:
            raise ToolValidationError("missing support ticket reason")
        result = await tools.create_support_ticket(user_id, reason)
    except ToolValidationError as exc:
        logger.warning("blocked backend action: %s", exc)
        return BackendActionResult(action=action, status="blocked", ok=False, error=str(exc))

    logger.info("backend action executed: %s", action.name)
    return BackendActionResult(action=action, status="executed", ok=True, result=result)
