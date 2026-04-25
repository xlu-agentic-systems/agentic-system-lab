from __future__ import annotations

import json
import os
import re
from typing import Protocol, TypeVar

from pydantic import BaseModel


T = TypeVar("T", bound=BaseModel)


class LlmClient(Protocol):
    async def parse(
        self,
        *,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_model: type[T],
    ) -> T:
        ...


class OpenAILlmClient:
    def __init__(self, *, model: str | None = None) -> None:
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5.5")
        self._client = None

    async def parse(
        self,
        *,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_model: type[T],
    ) -> T:
        client = self._get_client()
        response = await client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Return structured JSON for task "
                        f"{task_name}.\n\n{json.dumps(user_payload, default=str)}"
                    ),
                },
            ],
            text_format=response_model,
        )
        parsed = _extract_parsed_response(response)
        if isinstance(parsed, response_model):
            return parsed
        return response_model.model_validate(parsed)

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "The openai package is required for LLM calls. Install project dependencies with "
                    '`pip install -e ".[dev]"`.'
                ) from exc
            self._client = AsyncOpenAI()
        return self._client


def _extract_parsed_response(response):
    direct = getattr(response, "output_parsed", None)
    if direct is not None:
        return direct

    for output in getattr(response, "output", []):
        if getattr(output, "type", None) != "message":
            continue
        for item in getattr(output, "content", []):
            refusal = getattr(item, "refusal", None)
            if refusal:
                raise RuntimeError(f"OpenAI refused the structured output request: {refusal}")
            parsed = getattr(item, "parsed", None)
            if parsed is not None:
                return parsed

    raise RuntimeError("OpenAI response did not include parsed structured output.")


class RuleBasedLlmClient:
    """Deterministic LLM substitute for unit tests and offline demos."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def parse(
        self,
        *,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_model: type[T],
    ) -> T:
        from app.models import (
            AgentResult,
            OrchestratorDecision,
            PlannerOutput,
            ProposedAction,
            ReturnContext,
            RoutingOutput,
        )

        message = str(user_payload.get("message", ""))
        text = message.lower()
        self.calls.append(task_name)

        if response_model is OrchestratorDecision:
            selected = []
            if any(term in text for term in ("return", "exchange", "refund the item", "send back")):
                selected.append("return_agent")
            if any(term in text for term in ("shipping", "tracking", "package", "delivery", "delayed", "lost")):
                selected.append("shipping_agent")
            if any(term in text for term in ("charged", "charge", "payment", "card", "refund timeline", "refund status", "paid")):
                selected.append("payment_agent")
            if any(term in text for term in ("account", "address", "login", "password", "email", "profile")):
                selected.append("account_agent")
            if not selected:
                selected.append("escalation_agent")
            mode = "single_agent"
            if len(selected) > 1:
                mode = "sequential" if "package" in text or "delayed" in text else "parallel"
            return response_model.model_validate(
                {
                    "selected_agents": selected,
                    "execution_mode": mode,
                    "extracted_context": {
                        "product_hint": "shoes" if "shoe" in text else None,
                        "refund_requested": "refund" in text,
                    },
                    "reasoning": "Rule-based test decision.",
                }
            )

        if response_model is RoutingOutput:
            order_match = re.search(r"\border-\d+\b", message, re.IGNORECASE)
            item_match = re.search(r"\bitem-\d+\b", message, re.IGNORECASE)
            return response_model.model_validate(
                {
                    "intent": _rule_based_intent(text),
                    "extracted_fields": {
                        "order_id": order_match.group(0).lower() if order_match else None,
                        "item_id": item_match.group(0).lower() if item_match else None,
                        "return_reason": _rule_based_return_reason(text),
                        "refund_requested": any(term in text for term in ("refund", "return", "money back")),
                        "product_hint": _rule_based_product_hint(text),
                    },
                    "missing_fields": [],
                    "clarification_question": None,
                }
            )

        if response_model is PlannerOutput:
            if "backend_plan" in user_payload:
                return response_model.model_validate(user_payload["backend_plan"])
            return response_model.model_validate(_rule_based_return_plan(user_payload))

        if response_model is AgentResult:
            return response_model.model_validate(_rule_based_agent_result(task_name, user_payload))

        if response_model.__name__ in {"_ReturnQaResponse", "QAOutput"}:
            return response_model.model_validate({"response": _rule_based_return_response(user_payload)})

        raise NotImplementedError(f"RuleBasedLlmClient does not support {response_model.__name__}")


def _rule_based_intent(text: str) -> str:
    if "policy" in text:
        return "return_policy_question"
    if any(term in text for term in ("return", "refund", "money back", "exchange")):
        return "return_request"
    if "status" in text:
        return "refund_status"
    return "unknown"


def _rule_based_return_reason(text: str) -> str | None:
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


def _rule_based_return_plan(payload: dict) -> dict:
    routing = payload["routing"]
    facts = payload["backend_facts"]
    context = routing["extracted_fields"]
    if facts.get("error") == "missing_required_fields":
        return {
            "status": "needs_clarification",
            "reason_codes": ["missing_required_fields"],
            "explanation": routing.get("clarification_question") or "More information is needed.",
            "proposed_tool_calls": [],
        }
    if facts.get("order") is None:
        return {
            "status": "rejected",
            "reason_codes": facts.get("reason_codes", ["order_not_found"]),
            "explanation": "The order was not found.",
            "proposed_tool_calls": [],
        }
    if not facts.get("user_owns_order"):
        return {
            "status": "rejected",
            "reason_codes": ["order_not_owned_by_user"],
            "explanation": "The requested order does not belong to the authenticated user.",
            "proposed_tool_calls": [],
        }
    eligibility = facts.get("eligibility") or {}
    if eligibility.get("eligible"):
        return {
            "status": "approved",
            "reason_codes": ["eligible_for_refund"],
            "explanation": "The order, item, user ownership, and return policy all allow a refund.",
            "proposed_tool_calls": [],
        }
    return {
        "status": "rejected",
        "reason_codes": eligibility.get("reason_codes", ["not_eligible"]),
        "explanation": "The refund does not satisfy the return policy.",
        "proposed_tool_calls": [],
    }


def _rule_based_agent_result(task_name: str, payload: dict) -> dict:
    if task_name == "return_agent":
        resolved = payload["resolved"]
        if "error" in resolved:
            return {
                "agent": "return_agent",
                "confidence": 0.35,
                "summary": "I could not identify the exact item to evaluate for return eligibility.",
                "details": resolved,
                "needs_escalation": True,
                "reason_codes": [resolved["error"]],
            }
        eligibility = payload["eligibility"]
        product = resolved["product"]
        if eligibility["eligible"]:
            return {
                "agent": "return_agent",
                "confidence": 0.92,
                "summary": f"{product['name']} is eligible for return. The refundable amount is ${eligibility['amount']}.",
                "details": {"resolved": resolved, "eligibility": eligibility},
                "reason_codes": ["return_eligible"],
            }
        return {
            "agent": "return_agent",
            "confidence": 0.88,
            "summary": f"{product['name']} is not currently eligible for return: {', '.join(eligibility['reason_codes'])}.",
            "details": {"resolved": resolved, "eligibility": eligibility},
            "needs_escalation": "order_not_delivered" in eligibility["reason_codes"],
            "reason_codes": eligibility["reason_codes"],
        }

    if task_name == "shipping_agent":
        status = payload["shipping_status"]
        if "error" in status:
            return {
                "agent": "shipping_agent",
                "confidence": 0.4,
                "summary": "I could not find a shipment for this request.",
                "details": status,
                "needs_escalation": True,
                "reason_codes": [status["error"]],
            }
        return {
            "agent": "shipping_agent",
            "confidence": 0.9,
            "summary": f"Order {status['order_id']} is {status['status']} with {status['carrier']}. Latest update: {status['last_update']}",
            "details": status,
            "needs_escalation": status["status"] in {"delayed", "lost"},
            "reason_codes": [f"shipment_{status['status']}"],
        }

    if task_name == "payment_agent":
        timeline = payload.get("refund_timeline")
        duplicates = payload["duplicate_charge_result"]
        if timeline:
            return {
                "agent": "payment_agent",
                "confidence": 0.86,
                "summary": timeline["timeline"],
                "details": timeline,
                "reason_codes": ["refund_timeline"],
            }
        if duplicates["duplicates"]:
            extra_payment = duplicates["duplicates"][0][1]
            return {
                "agent": "payment_agent",
                "confidence": 0.93,
                "summary": f"I found a likely duplicate charge for order {extra_payment['order_id']} in the amount of ${extra_payment['amount']}.",
                "details": duplicates,
                "proposed_actions": [
                    {
                        "name": "refund_duplicate_charge",
                        "args": {"payment_id": extra_payment["payment_id"], "amount": extra_payment["amount"]},
                        "safety": "unsafe_write",
                        "requires_approval": True,
                        "reason": "Refunding a payment changes customer funds and needs approval.",
                    }
                ],
                "reason_codes": ["duplicate_charge_found"],
            }
        return {
            "agent": "payment_agent",
            "confidence": 0.72,
            "summary": "I did not find a duplicate captured charge in the available payment records.",
            "details": duplicates,
            "reason_codes": ["duplicate_charge_not_found"],
        }

    if task_name == "account_agent":
        profile = payload["profile"]
        sensitive = any(term in str(payload.get("message", "")).lower() for term in ("address", "password", "login", "email", "payment method"))
        return {
            "agent": "account_agent",
            "confidence": 0.82,
            "summary": f"The account on file is {profile.get('name')} with email {profile.get('email')}. Sensitive profile changes require verification.",
            "details": {"profile": profile, "sensitive_change_requested": sensitive},
            "needs_escalation": sensitive,
            "reason_codes": ["sensitive_account_change"] if sensitive else ["account_loaded"],
        }

    if task_name == "escalation_agent":
        return {
            "agent": "escalation_agent",
            "confidence": 0.9,
            "summary": "A human support ticket should be created for this request.",
            "details": {
                "low_confidence_agents": payload.get("low_confidence_agents", []),
                "escalating_agents": payload.get("escalating_agents", []),
            },
            "proposed_actions": [
                {
                    "name": "create_support_ticket",
                    "args": {"reason": payload["ticket_reason"]},
                    "safety": "safe_write",
                    "requires_approval": False,
                    "reason": "The orchestrator determined this request needs human review.",
                }
            ],
            "needs_escalation": True,
            "reason_codes": ["human_ticket_recommended"],
        }

    raise NotImplementedError(task_name)


def _rule_based_return_response(payload: dict) -> str:
    routing = payload["routing"]
    planner = payload["planner"]
    tool_results = payload["tool_results"]
    if routing.get("clarification_question"):
        return routing["clarification_question"]
    if planner["status"] == "approved":
        refund_result = next(
            (
                result
                for result in tool_results
                if result["proposal"]["name"] == "issue_refund" and result["ok"]
            ),
            None,
        )
        if refund_result and refund_result.get("result"):
            return (
                f"Your return is approved and refund {refund_result['result']['refund_id']} "
                f"has been issued for ${refund_result['result']['amount']}."
            )
        return "Your return appears eligible, but the refund was not issued because backend validation did not approve the tool call."
    if planner["status"] == "rejected":
        return f"I cannot approve this refund because {', '.join(planner['reason_codes'])}."
    policy_result = next(
        (
            result
            for result in tool_results
            if result["proposal"]["name"] == "get_return_policy" and result["ok"]
        ),
        None,
    )
    if policy_result and policy_result.get("result"):
        return policy_result["result"].get("notes", planner["explanation"])
    return planner["explanation"]


def _rule_based_product_hint(text: str) -> str | None:
    for hint in ("shoes", "shoe", "sneakers", "headphones", "mug", "apparel", "electronics", "clearance"):
        if hint in text:
            return hint
    return None
