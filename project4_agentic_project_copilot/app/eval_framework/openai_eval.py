from __future__ import annotations

import os
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from project4_agentic_project_copilot.app.embeddings import EmbeddingClient, OpenAIEmbeddingClient
from project4_agentic_project_copilot.app.eval_framework.goal_harness import run_goal_harness
from project4_agentic_project_copilot.app.eval_framework.models import (
    EvaluationRun,
    EvaluationRunMetadata,
    GoalHarnessRun,
)
from project4_agentic_project_copilot.app.eval_framework.runner import WorkflowEvalRunner, run_evaluation
from project4_agentic_project_copilot.app.eval_framework.service_factory import create_evaluation_service
from project4_agentic_project_copilot.app.llm import LlmClient, OpenAILlmClient
from project4_agentic_project_copilot.app.service import ProjectCopilotService


T = TypeVar("T", bound=BaseModel)

OPENAI_SMOKE_CASES_PATH = Path(__file__).resolve().parents[2] / "evals" / "openai_smoke_cases.jsonl"


class CountingLlmClient:
    def __init__(self, inner: LlmClient, call_counts: Counter[str]) -> None:
        self.inner = inner
        self.call_counts = call_counts

    async def parse(
        self,
        *,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_model: type[T],
    ) -> T:
        self.call_counts[task_name] += 1
        return await self.inner.parse(
            task_name=task_name,
            system_prompt=system_prompt,
            user_payload=user_payload,
            response_model=response_model,
        )


class CountingEmbeddingClient:
    def __init__(self, inner: EmbeddingClient, call_counts: Counter[str]) -> None:
        self.inner = inner
        self.call_counts = call_counts

    async def embed(self, text: str) -> list[float]:
        self.call_counts["embedding.embed"] += 1
        return await self.inner.embed(text)

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        self.call_counts["embedding.embed_many"] += 1
        batch_embed = getattr(self.inner, "embed_many", None)
        if batch_embed is not None:
            return await batch_embed(texts)
        return [await self.inner.embed(text) for text in texts]


class OpenAIEvaluationServiceFactory:
    def __init__(self) -> None:
        self.llm_client = OpenAILlmClient()
        self.embedding_client = OpenAIEmbeddingClient()
        self.call_counts: Counter[str] = Counter()

    def __call__(self) -> ProjectCopilotService:
        return create_evaluation_service(
            temp_prefix="project4-openai-eval-",
            llm_client=CountingLlmClient(self.llm_client, self.call_counts),
            embedding_client=CountingEmbeddingClient(self.embedding_client, self.call_counts),
        )

    def metadata(self) -> EvaluationRunMetadata:
        return EvaluationRunMetadata(
            live=True,
            llm_provider="openai",
            llm_model=self.llm_client.model,
            embedding_provider="openai",
            embedding_model=self.embedding_client.model,
            call_count_by_task=dict(sorted(self.call_counts.items())),
        )


def has_openai_api_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def require_openai_api_key() -> None:
    if not has_openai_api_key():
        raise RuntimeError("OPENAI_API_KEY is required to run real OpenAI Project 4 evals.")


async def run_openai_evaluation(cases_path: Path | str = OPENAI_SMOKE_CASES_PATH) -> EvaluationRun:
    require_openai_api_key()
    service_factory = OpenAIEvaluationServiceFactory()
    runner = WorkflowEvalRunner(
        mode="openai_workflow",
        service_factory=service_factory,
        metadata=service_factory.metadata(),
    )
    run = await run_evaluation(cases_path, runner=runner)
    return run.model_copy(update={"metadata": service_factory.metadata()})


async def run_openai_goal_harness(cases_path: Path | str = OPENAI_SMOKE_CASES_PATH) -> GoalHarnessRun:
    require_openai_api_key()
    service_factory = OpenAIEvaluationServiceFactory()
    runner = WorkflowEvalRunner(
        mode="openai_workflow",
        service_factory=service_factory,
        metadata=service_factory.metadata(),
    )
    run = await run_goal_harness(
        service_factory=service_factory,
        regression_runner=runner,
        regression_cases_path=cases_path,
        metadata=service_factory.metadata(),
    )
    return run.model_copy(update={"metadata": service_factory.metadata()})
