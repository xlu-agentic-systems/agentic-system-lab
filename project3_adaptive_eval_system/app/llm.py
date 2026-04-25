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
    text = " ".join(
        [
            trace.get("user_message", ""),
            trace.get("final_response", ""),
            trace.get("expected_behavior", ""),
            trace.get("policy_basis") or "",
            " ".join(item.get("output", "") for item in trace.get("agent_outputs", [])),
            " ".join(item.get("summary", "") for item in trace.get("tool_calls", [])),
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
    if "refund" in text and "unsafe" in text:
        issues.append(
            {
                "category": "unsafe_tool_proposal",
                "severity": "high",
                "description": "The trace includes an unsafe refund proposal.",
                "evidence": "Tool proposal was marked unsafe or failed validation.",
                "recommendation": "Require backend validation before refund execution.",
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
    return {
        "test_id": f"regression-{trace['trace_id']}",
        "trace_id": trace["trace_id"],
        "test_name": f"test_{trace['trace_id'].replace('-', '_')}",
        "user_message": trace["user_message"],
        "expected_behavior": trace["expected_behavior"],
        "assertions": [
            "Agent follows the expected behavior from the source trace.",
            "Unsafe tool calls are not executed without backend validation.",
            "Final response does not hallucinate policy details.",
        ],
        "source_issue": issue["description"],
    }


def _generate_patch(trace: dict, evaluation: dict) -> dict:
    issue = (evaluation.get("detected_issues") or [{"recommendation": "Improve prompt clarity."}])[0]
    instruction = issue.get("recommendation", "Improve prompt clarity.")
    if issue.get("category") == "incorrect_policy_interpretation":
        instruction = "Return eligibility must be calculated from delivery date unless the policy explicitly says otherwise."
    return {
        "patch_id": f"patch-{trace['trace_id']}",
        "trace_id": trace["trace_id"],
        "target_prompt": "Planner Agent",
        "proposed_instruction": instruction,
        "rationale": f"Generated from evaluation issue: {issue.get('description', 'failure')}",
        "status": "proposed",
    }
