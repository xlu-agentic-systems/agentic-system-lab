from __future__ import annotations

import logging

from app.catalog import Catalog, catalog
from app.llm import LlmClient
from app.models import (
    PlannerOutput,
    ReturnContext,
    RoutingOutput,
    SessionState,
    ToolCallProposal,
    ToolExecutionResult,
)
from app.policy import check_return_policy
from app.return_models import QAOutput


logger = logging.getLogger(__name__)


class RoutingAgent:
    def __init__(self, llm_client: LlmClient) -> None:
        self.llm_client = llm_client

    async def run(self, message: str, state: SessionState) -> RoutingOutput:
        output = await self.llm_client.parse(
            task_name="return_routing_agent",
            system_prompt=(
                "You are the Routing Agent for an e-commerce return chatbot. Classify intent and "
                "extract useful fields from the customer message and session context. Valid intents "
                "are return_request, return_policy_question, refund_status, and unknown. For a "
                "return_request, mark order_id, item_id, and return_reason as missing when absent. "
                "Ask one concise clarification question if required fields are missing. Return only "
                "the structured RoutingOutput."
            ),
            user_payload={
                "message": message,
                "current_session_context": state.context.model_dump(),
                "recent_history": [item.model_dump(mode="json") for item in state.history[-6:]],
            },
            response_model=RoutingOutput,
        )
        output = _normalize_routing_output(output, state.context)
        logger.info("return routing decision: %s", output.model_dump(mode="json"))
        return output


class PlannerAgent:
    def __init__(self, domain_catalog: Catalog = catalog, llm_client: LlmClient | None = None) -> None:
        if llm_client is None:
            raise ValueError("PlannerAgent requires an LLM client")
        self.catalog = domain_catalog
        self.llm_client = llm_client

    async def run(self, routing: RoutingOutput, user_id: str) -> PlannerOutput:
        if routing.missing_fields:
            return PlannerOutput(
                status="needs_clarification",
                reason_codes=["missing_required_fields"],
                explanation=routing.clarification_question or "More information is needed.",
                proposed_tool_calls=[],
            )
        if routing.intent == "return_policy_question":
            category = _policy_category_for_context(self.catalog, routing.extracted_fields)
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
        if routing.intent != "return_request":
            return PlannerOutput(
                status="escalated",
                reason_codes=["unsupported_return_bot_intent"],
                explanation="I can help with return requests and return policy questions.",
                proposed_tool_calls=[],
            )

        facts = _load_planner_facts(self.catalog, routing.extracted_fields, user_id)
        output = await self.llm_client.parse(
            task_name="return_planner_agent",
            system_prompt=(
                "You are the Planner Agent for an e-commerce return chatbot. Use only the provided "
                "backend facts and policy facts. Decide whether the request is approved, rejected, "
                "needs clarification, or should be escalated. You may propose backend tool calls, "
                "including issue_refund, but you must not claim unsafe writes have executed. "
                "Use issue_refund only when backend facts show eligibility and include the exact "
                "refund amount from eligibility.amount. Return only the structured PlannerOutput."
            ),
            user_payload={
                "user_id": user_id,
                "routing": routing.model_dump(mode="json"),
                "backend_facts": facts,
            },
            response_model=PlannerOutput,
        )
        output = _normalize_planner_output(output, routing, facts)
        logger.info("return planner decision: %s", output.model_dump(mode="json"))
        return output


class QAAgent:
    def __init__(self, llm_client: LlmClient) -> None:
        self.llm_client = llm_client

    async def run(
        self,
        routing: RoutingOutput,
        planner: PlannerOutput,
        tool_results: list[ToolExecutionResult],
    ) -> str:
        output = await self.llm_client.parse(
            task_name="return_qa_agent",
            system_prompt=(
                "You are the Q&A Agent for an e-commerce return chatbot. Write the final customer "
                "message from the routing output, planner decision, and backend tool results. Be "
                "polite and concise. Do not invent policy details. If an unsafe tool proposal was "
                "blocked or not executed, clearly say no refund was issued."
            ),
            user_payload={
                "routing": routing.model_dump(mode="json"),
                "planner": planner.model_dump(mode="json"),
                "tool_results": [result.model_dump(mode="json") for result in tool_results],
            },
            response_model=QAOutput,
        )
        return output.response


def _normalize_routing_output(output: RoutingOutput, previous_context: ReturnContext) -> RoutingOutput:
    context = previous_context.model_copy(update=output.extracted_fields.model_dump(exclude_none=True))
    missing_fields: list[str] = []
    if output.intent == "return_request":
        if not context.order_id:
            missing_fields.append("order_id")
        if not context.item_id:
            missing_fields.append("item_id")
        if not context.return_reason:
            missing_fields.append("return_reason")
    clarification = output.clarification_question
    if missing_fields and not clarification:
        labels = {
            "order_id": "order ID",
            "item_id": "item ID",
            "return_reason": "reason for the return",
        }
        clarification = f"Please provide the {', '.join(labels[field] for field in missing_fields)} so I can check the return."
    return RoutingOutput(
        intent=output.intent,
        extracted_fields=context,
        missing_fields=missing_fields,
        clarification_question=clarification if missing_fields else None,
    )


def _load_planner_facts(catalog: Catalog, context: ReturnContext, user_id: str) -> dict:
    if not context.order_id or not context.item_id:
        return {"error": "missing_required_fields"}
    order = catalog.get_order(context.order_id)
    if order is None:
        return {"order": None, "reason_codes": ["order_not_found"]}

    item = next((candidate for candidate in order.items if candidate.item_id == context.item_id), None)
    product = catalog.get_product(item.product_id) if item else None
    policy = catalog.get_policy(product.category) if product else None
    eligibility = check_return_policy(catalog, context.order_id, context.item_id)
    return {
        "order": order.model_dump(mode="json"),
        "authenticated_user_id": user_id,
        "user_owns_order": order.user_id == user_id,
        "item": item.model_dump(mode="json") if item else None,
        "product": product.model_dump(mode="json") if product else None,
        "policy": policy.model_dump(mode="json") if policy else None,
        "eligibility": eligibility.model_dump(mode="json"),
    }


def _normalize_planner_output(output: PlannerOutput, routing: RoutingOutput, facts: dict) -> PlannerOutput:
    context = routing.extracted_fields
    if facts.get("order") and not facts.get("user_owns_order"):
        return PlannerOutput(
            status="rejected",
            reason_codes=["order_not_owned_by_user"],
            explanation="The requested order does not belong to the authenticated user.",
            proposed_tool_calls=[],
        )

    proposals = list(output.proposed_tool_calls)

    if context.order_id and not any(call.name == "get_order" for call in proposals):
        proposals.insert(
            0,
            ToolCallProposal(
                name="get_order",
                args={"order_id": context.order_id},
                safety="read_only",
                reason="Load order facts before making a return decision.",
            ),
        )

    product = facts.get("product") or {}
    category = product.get("category")
    if category and not any(call.name == "get_return_policy" for call in proposals):
        proposals.append(
            ToolCallProposal(
                name="get_return_policy",
                args={"category": category},
                safety="read_only",
                reason="Load the policy that applies to the item category.",
            )
        )

    if context.order_id and context.item_id and not any(call.name == "check_refund_eligibility" for call in proposals):
        proposals.append(
            ToolCallProposal(
                name="check_refund_eligibility",
                args={"order_id": context.order_id, "item_id": context.item_id},
                safety="read_only",
                reason="Ask the backend policy checker for the authoritative eligibility result.",
            )
        )

    eligibility = facts.get("eligibility") or {}
    eligible = (
        routing.intent == "return_request"
        and bool(context.return_reason)
        and bool(eligibility.get("eligible"))
        and bool(facts.get("user_owns_order"))
    )
    if not eligible:
        proposals = [call for call in proposals if call.name != "issue_refund"]
    elif not any(call.name == "issue_refund" for call in proposals):
        proposals.append(
            ToolCallProposal(
                name="issue_refund",
                args={
                    "order_id": context.order_id,
                    "item_id": context.item_id,
                    "amount": eligibility["amount"],
                },
                safety="unsafe_write",
                reason="Refund may execute only after backend validation independently confirms it.",
            )
        )

    return output.model_copy(update={"proposed_tool_calls": proposals})


def _policy_category_for_context(catalog: Catalog, context: ReturnContext) -> str:
    if context.order_id and context.item_id:
        item = catalog.get_order_item(context.order_id, context.item_id)
        product = catalog.get_product(item.product_id) if item else None
        if product:
            return product.category
    if context.product_hint:
        hint = context.product_hint.lower()
        for product in catalog.products.values():
            terms = [product.name.lower(), product.category.lower(), *product.aliases]
            if any(hint in term or term in hint for term in terms):
                return product.category
    return "electronics"
