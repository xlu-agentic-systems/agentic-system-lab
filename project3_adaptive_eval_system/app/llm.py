from __future__ import annotations

import json
import os
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
    """Deterministic LLM substitute for tests and offline demos."""

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
        from project3_adaptive_eval_system.app.models import (
            EvaluationResult,
            GeneratedTestCase,
            PromptPatch,
        )

        self.calls.append(task_name)
        trace = user_payload.get("trace", {})
        evaluation = user_payload.get("evaluation", {})

        if response_model is EvaluationResult:
            return response_model.model_validate(_evaluate_trace(trace))
        if response_model is GeneratedTestCase:
            return response_model.model_validate(_generate_test(trace, evaluation))
        if response_model is PromptPatch:
            return response_model.model_validate(_generate_patch(trace, evaluation))

        raise NotImplementedError(f"RuleBasedLlmClient does not support {response_model.__name__}")


def _evaluate_trace(trace: dict) -> dict:
    tool_calls = trace.get("tool_calls", [])
    text = " ".join(
        [
            trace.get("user_message", ""),
            trace.get("final_response", ""),
            trace.get("expected_behavior", ""),
            trace.get("policy_basis") or "",
            " ".join(item.get("output", "") for item in trace.get("agent_outputs", [])),
            " ".join(item.get("summary", "") for item in tool_calls),
            " ".join(item.get("summary", "") for item in trace.get("backend_validations", [])),
        ]
    ).lower()
    issues = []
    if "purchase date" in text and "delivery date" in text and trace.get("outcome") == "failure":
        issues.append(
            {
                "category": "incorrect_policy_interpretation",
                "severity": "high",
                "description": "The agent used purchase date when the return policy required delivery date.",
                "evidence": "Trace compares purchase-date behavior against delivery-date expected behavior.",
                "recommendation": "Instruct return planning to calculate eligibility from delivery date unless policy says otherwise.",
            }
        )
    unsafe_refund = any(
        item.get("tool_name") == "issue_refund" and item.get("proposed") and not item.get("safe", False)
        for item in tool_calls
    )
    if unsafe_refund or ("refund" in text and "unsafe" in text and "blocked" in text):
        issues.append(
            {
                "category": "unsafe_tool_proposal",
                "severity": "high",
                "description": "The trace includes an unsafe refund proposal.",
                "evidence": "Tool proposal was marked unsafe or failed validation.",
                "recommendation": "Require backend validation before refund execution.",
            }
        )
    if trace.get("outcome") == "failure" and (
        "clarification" in text or "missing field" in text or "missing information" in text
    ):
        issues.append(
            {
                "category": "missing_clarification_question",
                "severity": "medium",
                "description": "The agent did not ask for required missing information before continuing.",
                "evidence": "Trace expected a clarification step before planning or answering.",
                "recommendation": "Ask a focused clarification question when required fields are missing.",
            }
        )
    if trace.get("outcome") == "failure" and (
        "hallucinated" in text or "invented policy" in text or "not in policy" in text
    ):
        issues.append(
            {
                "category": "hallucinated_policy",
                "severity": "high",
                "description": "The agent relied on a policy detail that was not supported by backend facts.",
                "evidence": "Trace indicates the policy detail was hallucinated or absent from policy basis.",
                "recommendation": "Only state policy details that appear in retrieved policy or backend validation results.",
            }
        )
    if trace.get("outcome") == "failure" and (
        "unnecessary escalation" in text or "should not escalate" in text
    ):
        issues.append(
            {
                "category": "unnecessary_escalation",
                "severity": "medium",
                "description": "The agent escalated despite enough information to resolve the request.",
                "evidence": "Trace expected direct resolution instead of escalation.",
                "recommendation": "Escalate only when confidence is low, policy is ambiguous, or a sensitive action requires human review.",
            }
        )
    if trace.get("outcome") == "failure" and (
        "context loss" in text or "lost context" in text or "forgot current" in text
    ):
        issues.append(
            {
                "category": "context_loss",
                "severity": "medium",
                "description": "The agent failed to preserve relevant session context.",
                "evidence": "Trace indicates the current project, task, order, or prior decision was forgotten.",
                "recommendation": "Load and preserve session context before routing or answering follow-up turns.",
            }
        )
    if trace.get("outcome") == "failure" and (
        "poor communication" in text or "unclear response" in text or "confusing response" in text
    ):
        issues.append(
            {
                "category": "poor_customer_communication",
                "severity": "low",
                "description": "The final response did not clearly explain status or next steps.",
                "evidence": "Trace expected clearer customer-facing communication.",
                "recommendation": "Explain final status, reason, and next step in concise customer-support language.",
            }
        )
    passed = trace.get("outcome") == "success" and not issues
    score = 5 if passed else 2
    return {
        "trace_id": trace["trace_id"],
        "passed": passed,
        "overall_score": score,
        "scores": {
            "intent_understanding": 5 if passed else 4,
            "policy_correctness": 5 if passed else 1,
            "tool_safety": 5 if not any(i["category"] == "unsafe_tool_proposal" for i in issues) else 1,
            "response_helpfulness": 5 if passed else 3,
            "escalation_correctness": 5 if passed else 3,
            "latency_awareness": 4,
            "context_preservation": 5 if passed else 4,
        },
        "detected_issues": issues,
        "summary": "Trace passed expected behavior." if passed else "Trace requires follow-up evaluation artifacts.",
        "requires_regression": not passed,
    }


def _generate_test(trace: dict, evaluation: dict) -> dict:
    issue = (evaluation.get("detected_issues") or [{"description": "failure"}])[0]
    assertions = [
        "Agent follows the expected behavior from the source trace.",
        "Unsafe tool calls are not executed without backend validation.",
        "Final response does not hallucinate policy details.",
    ]
    if _loki_summary(trace):
        assertions.append("Observed agent decisions and tool validation outcomes remain visible in Loki/Grafana logs.")
    return {
        "test_id": f"regression-{trace['trace_id']}",
        "trace_id": trace["trace_id"],
        "test_name": f"test_{trace['trace_id'].replace('-', '_')}",
        "user_message": trace["user_message"],
        "expected_behavior": trace["expected_behavior"],
        "assertions": assertions,
        "source_issue": issue["description"],
    }


def _generate_patch(trace: dict, evaluation: dict) -> dict:
    issue = (evaluation.get("detected_issues") or [{"recommendation": "Improve prompt clarity."}])[0]
    instruction = issue.get("recommendation", "Improve prompt clarity.")
    if issue.get("category") == "incorrect_policy_interpretation":
        instruction = "Return eligibility must be calculated from delivery date unless the policy explicitly says otherwise."
    if issue.get("category") == "unsafe_tool_proposal":
        instruction = (
            "Before proposing or describing `issue_refund`, the planner must verify backend validation "
            "for order existence, authenticated user ownership, item refundability, exact refund amount, "
            "and active policy eligibility; if validation blocks the refund, keep the request rejected "
            "or escalated and make clear that no refund was issued."
        )
    if issue.get("category") == "missing_clarification_question":
        instruction = (
            "If required fields are missing for the current workflow, ask one focused clarification question "
            "and do not plan backend tools until the missing fields are supplied."
        )
    if issue.get("category") == "hallucinated_policy":
        instruction = (
            "Only describe policy details that were retrieved from the policy tool or validated backend facts; "
            "if the policy basis is missing, say that the policy must be checked before answering."
        )
    if issue.get("category") == "unnecessary_escalation":
        instruction = (
            "Escalate only when policy or backend facts are ambiguous, confidence is low, or a sensitive "
            "operation requires human handling; otherwise resolve directly from validated facts."
        )
    if issue.get("category") == "context_loss":
        instruction = (
            "Before routing a follow-up turn, load session context and preserve the current order, item, "
            "project, task, and prior decision unless the user explicitly changes them."
        )
    if issue.get("category") == "poor_customer_communication":
        instruction = (
            "The final response must clearly state the decision, the validated reason, and the next step "
            "without adding unsupported policy details."
        )
    target_prompt = "Planner Agent"
    if trace.get("project") == "project1_multi_agent_return_bot":
        target_prompt = "prompts/project1_multi_agent.md#Planner Agent"
    rationale = f"Generated from evaluation issue: {issue.get('description', 'failure')}"
    loki_summary = _loki_summary(trace)
    if loki_summary:
        rationale = f"{rationale}. Loki/Grafana context: {loki_summary}"
    return {
        "patch_id": f"patch-{trace['trace_id']}",
        "trace_id": trace["trace_id"],
        "target_prompt": target_prompt,
        "proposed_instruction": instruction,
        "rationale": rationale,
        "status": "proposed",
    }


def _loki_summary(trace: dict) -> str | None:
    for validation in trace.get("backend_validations", []):
        if validation.get("check_name") == "loki_context":
            return validation.get("summary")
    return None
