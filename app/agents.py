from __future__ import annotations

import logging
import re
from decimal import Decimal

from app.catalog import Catalog, catalog
from app.models import (
    PlannerOutput,
    ReturnContext,
    RoutingOutput,
    SessionState,
    ToolCallProposal,
)
from app.policy import check_return_policy


logger = logging.getLogger(__name__)
ORDER_RE = re.compile(r"\border-\d+\b", re.IGNORECASE)
ITEM_RE = re.compile(r"\bitem-\d+\b", re.IGNORECASE)


class RoutingAgent:
    async def run(self, message: str, state: SessionState) -> RoutingOutput:
        text = message.lower()
        extracted = state.context.model_copy()
        order_match = ORDER_RE.search(message)
        item_match = ITEM_RE.search(message)

        if order_match:
            extracted.order_id = order_match.group(0).lower()
        if item_match:
            extracted.item_id = item_match.group(0).lower()
        reason = _extract_reason(text)
        if reason:
            extracted.return_reason = reason
        if any(word in text for word in ("refund", "return", "money back", "reimburse")):
            extracted.refund_requested = True

        intent = _classify_intent(text)
        missing_fields = _missing_fields(intent, extracted)
        clarification = _clarification_question(missing_fields)
        output = RoutingOutput(
            intent=intent,
            extracted_fields=extracted,
            missing_fields=missing_fields,
            clarification_question=clarification,
        )
        logger.info("routing decision: %s", output.model_dump(mode="json"))
        return output


class PlannerAgent:
    def __init__(self, domain_catalog: Catalog = catalog) -> None:
        self.catalog = domain_catalog

    async def run(self, routing: RoutingOutput, user_id: str) -> PlannerOutput:
        if routing.missing_fields:
            return PlannerOutput(
                status="needs_clarification",
                reason_codes=["missing_required_fields"],
                explanation=routing.clarification_question or "More information is needed.",
                proposed_tool_calls=[],
            )

        context = routing.extracted_fields
        if routing.intent == "return_policy_question":
            return self._plan_policy_answer(context)
        if routing.intent != "return_request":
            return PlannerOutput(
                status="needs_clarification",
                reason_codes=["unsupported_intent"],
                explanation="I can help with return and refund requests. Please share the order and item.",
                proposed_tool_calls=[],
            )

        assert context.order_id is not None
        assert context.item_id is not None
        order = self.catalog.get_order(context.order_id)
        if order is None:
            return PlannerOutput(
                status="rejected",
                reason_codes=["order_not_found"],
                explanation="The order was not found.",
                proposed_tool_calls=[
                    ToolCallProposal(
                        name="create_support_ticket",
                        args={"reason": f"Return request for unknown order {context.order_id}"},
                        safety="safe_write",
                        reason="A support agent should inspect the missing order reference.",
                    )
                ],
            )
        if order.user_id != user_id:
            return PlannerOutput(
                status="rejected",
                reason_codes=["order_not_owned_by_user"],
                explanation="The requested order does not belong to the authenticated user.",
                proposed_tool_calls=[],
            )

        item = next((candidate for candidate in order.items if candidate.item_id == context.item_id), None)
        if item is None:
            return PlannerOutput(
                status="rejected",
                reason_codes=["item_not_found"],
                explanation="The item was not found on that order.",
                proposed_tool_calls=[],
            )

        product = self.catalog.get_product(item.product_id)
        category = product.category if product else "unknown"
        eligibility = check_return_policy(self.catalog, context.order_id, context.item_id)
        read_calls = [
            ToolCallProposal(
                name="get_order",
                args={"order_id": context.order_id},
                safety="read_only",
                reason="Load order facts before making a return decision.",
            ),
            ToolCallProposal(
                name="get_return_policy",
                args={"category": category},
                safety="read_only",
                reason="Load the policy that applies to the item category.",
            ),
            ToolCallProposal(
                name="check_refund_eligibility",
                args={"order_id": context.order_id, "item_id": context.item_id},
                safety="read_only",
                reason="Ask the backend policy checker for the authoritative eligibility result.",
            ),
        ]
        if eligibility.eligible:
            amount = _decimal_to_str(eligibility.amount or Decimal("0"))
            return PlannerOutput(
                status="approved",
                reason_codes=["eligible_for_refund"],
                explanation="The order, item, user ownership, and return policy all allow a refund.",
                proposed_tool_calls=[
                    *read_calls,
                    ToolCallProposal(
                        name="issue_refund",
                        args={
                            "order_id": context.order_id,
                            "item_id": context.item_id,
                            "amount": amount,
                        },
                        safety="unsafe_write",
                        reason="Refund can be issued only if backend validation confirms the proposal.",
                    ),
                ],
            )

        return PlannerOutput(
            status="rejected",
            reason_codes=eligibility.reason_codes,
            explanation="The refund does not satisfy the return policy.",
            proposed_tool_calls=read_calls,
        )

    def _plan_policy_answer(self, context: ReturnContext) -> PlannerOutput:
        category = "electronics"
        if context.order_id and context.item_id:
            item = self.catalog.get_order_item(context.order_id, context.item_id)
            product = self.catalog.get_product(item.product_id) if item else None
            category = product.category if product else category
        return PlannerOutput(
            status="needs_clarification",
            reason_codes=["policy_information_only"],
            explanation="Policy questions are answered without executing a refund.",
            proposed_tool_calls=[
                ToolCallProposal(
                    name="get_return_policy",
                    args={"category": category},
                    safety="read_only",
                    reason="Retrieve policy text for the requested category.",
                )
            ],
        )


class QAAgent:
    async def run(self, routing: RoutingOutput, planner: PlannerOutput, tool_results) -> str:
        if routing.clarification_question:
            return routing.clarification_question

        if planner.status == "approved":
            refund_result = next(
                (
                    result
                    for result in tool_results
                    if result.proposal.name == "issue_refund" and result.ok
                ),
                None,
            )
            if refund_result and refund_result.result:
                amount = refund_result.result["amount"]
                refund_id = refund_result.result["refund_id"]
                return (
                    f"Your return is approved and refund {refund_id} has been issued "
                    f"for ${amount}. You will receive a confirmation with the next steps."
                )
            return (
                "Your return appears eligible, but the refund was not issued because backend "
                "validation did not approve the tool call. I have not changed your order."
            )

        if planner.status == "rejected":
            reason = _human_reason(planner.reason_codes)
            return f"I cannot approve this refund because {reason}."

        if planner.proposed_tool_calls and planner.proposed_tool_calls[0].name == "get_return_policy":
            policy_result = next((result for result in tool_results if result.ok), None)
            if policy_result and policy_result.result:
                return policy_result.result.get("notes", "I found the return policy.")

        return planner.explanation


def _classify_intent(text: str):
    if "policy" in text:
        return "return_policy_question"
    if any(word in text for word in ("return", "refund", "money back", "exchange")):
        return "return_request"
    if "status" in text:
        return "refund_status"
    return "unknown"


def _extract_reason(text: str) -> str | None:
    reasons = {
        "damaged": ("damaged", "broken", "defective", "not working"),
        "wrong_item": ("wrong item", "incorrect item"),
        "fit": ("too small", "too large", "doesn't fit", "does not fit"),
        "changed_mind": ("changed my mind", "do not want", "don't want"),
    }
    for label, phrases in reasons.items():
        if any(phrase in text for phrase in phrases):
            return label
    return None


def _missing_fields(intent: str, context: ReturnContext) -> list[str]:
    if intent != "return_request":
        return []
    missing = []
    if not context.order_id:
        missing.append("order_id")
    if not context.item_id:
        missing.append("item_id")
    if not context.return_reason:
        missing.append("return_reason")
    return missing


def _clarification_question(missing_fields: list[str]) -> str | None:
    if not missing_fields:
        return None
    labels = {
        "order_id": "order ID",
        "item_id": "item ID",
        "return_reason": "reason for the return",
    }
    readable = ", ".join(labels[field] for field in missing_fields)
    return f"Please provide the {readable} so I can check the return."


def _decimal_to_str(value: Decimal) -> str:
    return f"{value:.2f}"


def _human_reason(reason_codes: list[str]) -> str:
    phrases = {
        "order_not_found": "I could not find that order",
        "order_not_owned_by_user": "that order does not belong to your account",
        "item_not_found": "that item is not on the order",
        "item_not_refundable": "the item is marked non-refundable",
        "policy_disallows_refund": "the policy does not allow refunds for this category",
        "return_window_expired": "the return window has expired",
        "order_not_delivered": "the order has not been delivered",
        "missing_delivery_date": "the delivery date is missing",
    }
    return "; ".join(phrases.get(code, code.replace("_", " ")) for code in reason_codes)
