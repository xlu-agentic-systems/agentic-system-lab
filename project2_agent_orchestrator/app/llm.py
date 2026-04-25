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
                    "The openai package is required for LLM calls. Install dependencies with "
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
        from project2_agent_orchestrator.app.models import AgentResult, OrchestratorDecision

        self.calls.append(task_name)
        message = str(user_payload.get("message", ""))
        text = message.lower()

        if response_model is OrchestratorDecision:
            return response_model.model_validate(_rule_based_orchestrator_decision(message, text))
        if response_model is AgentResult:
            return response_model.model_validate(_rule_based_agent_result(task_name, user_payload))

        raise NotImplementedError(f"RuleBasedLlmClient does not support {response_model.__name__}")


def _rule_based_orchestrator_decision(message: str, text: str) -> dict:
    selected = []
    refund_status = "refund status" in text or "refund timeline" in text
    return_terms = ("return", "exchange", "send back", "money back")
    refund_for_item = "refund" in text and not refund_status and (
        re.search(r"\bitem-\d+\b", message, re.IGNORECASE) or "want a refund" in text
    )
    if any(term in text for term in return_terms) or refund_for_item:
        selected.append("return_agent")
    if any(term in text for term in ("shipping", "tracking", "package", "delivery", "delayed", "lost")):
        selected.append("shipping_agent")
    if any(
        term in text
        for term in ("charged", "charge", "payment", "card", "refund timeline", "refund status", "paid")
    ):
        selected.append("payment_agent")
    if any(term in text for term in ("account", "address", "login", "password", "email", "profile")):
        selected.append("account_agent")
    if not selected:
        selected.append("escalation_agent")

    mode = "single_agent"
    if len(selected) > 1:
        mode = "sequential" if _requires_sequential(text, selected) else "parallel"

    order_match = re.search(r"\border-\d+\b", message, re.IGNORECASE)
    item_match = re.search(r"\bitem-\d+\b", message, re.IGNORECASE)
    return {
        "selected_agents": selected,
        "execution_mode": mode,
        "extracted_context": {
            "order_id": order_match.group(0).lower() if order_match else None,
            "item_id": item_match.group(0).lower() if item_match else None,
            "return_reason": _extract_reason(text),
            "refund_requested": "refund" in text or "money back" in text,
            "product_hint": _extract_product_hint(text),
        },
        "reasoning": "Rule-based test decision.",
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
                "proposed_actions": [
                    {
                        "name": "start_return_authorization",
                        "args": {
                            "order_id": resolved["order"]["order_id"],
                            "item_id": resolved["item"]["item_id"],
                            "amount": eligibility["amount"],
                        },
                        "safety": "safe_write",
                        "requires_approval": True,
                        "reason": "Create a return authorization only after customer confirmation.",
                    }
                ],
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
            "summary": (
                f"Order {status['order_id']} is {status['status']} with {status['carrier']} "
                f"tracking {status['tracking_number']}."
            ),
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
                "summary": (
                    f"I found a likely duplicate charge for order {extra_payment['order_id']} "
                    f"in the amount of ${extra_payment['amount']}."
                ),
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
        if "error" in profile:
            return {
                "agent": "account_agent",
                "confidence": 0.35,
                "summary": "I could not load the account profile.",
                "details": profile,
                "needs_escalation": True,
                "reason_codes": [profile["error"]],
            }
        sensitive = any(
            term in str(payload.get("message", "")).lower()
            for term in ("address", "password", "login", "email", "payment method")
        )
        return {
            "agent": "account_agent",
            "confidence": 0.82,
            "summary": (
                f"The account on file is {profile['name']} with email {profile['email']}. "
                "Sensitive profile changes require verification."
            ),
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
                "prior_reason_codes": payload.get("prior_reason_codes", []),
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


def _requires_sequential(text: str, selected_agents: list[str]) -> bool:
    sensitive = any(term in text for term in ("change", "update", "password", "login", "address"))
    shipping_dependency = "shipping_agent" in selected_agents and (
        "return_agent" in selected_agents or "payment_agent" in selected_agents
    )
    return sensitive or shipping_dependency


def _extract_product_hint(text: str) -> str | None:
    for hint in ("shoes", "shoe", "sneakers", "headphones", "mug"):
        if hint in text:
            return hint
    return None


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
