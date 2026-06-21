import asyncio
import os

import pytest

from project4_agentic_project_copilot.app.evaluation import (
    OPENAI_SMOKE_CASES_PATH,
    run_openai_evaluation,
    run_openai_goal_harness,
)


pytestmark = pytest.mark.live_openai


def _live_openai_requested() -> bool:
    return os.getenv("RUN_OPENAI_EVALS") == "1"


@pytest.mark.skipif(not _live_openai_requested(), reason="set RUN_OPENAI_EVALS=1")
def test_openai_smoke_evaluation_uses_real_provider() -> None:
    result = asyncio.run(run_openai_evaluation(OPENAI_SMOKE_CASES_PATH))

    assert result.metadata.live is True
    assert result.metadata.llm_provider == "openai"
    assert result.metadata.embedding_provider == "openai"
    assert result.metadata.llm_model
    assert result.metadata.embedding_model
    assert result.metadata.call_count_by_task
    assert result.total == 4
    assert result.passed == result.total, [item.model_dump() for item in result.results if not item.passed]


@pytest.mark.skipif(not _live_openai_requested(), reason="set RUN_OPENAI_EVALS=1")
def test_openai_goal_harness_uses_real_provider() -> None:
    result = asyncio.run(run_openai_goal_harness(OPENAI_SMOKE_CASES_PATH))

    assert result.metadata.live is True
    assert result.metadata.llm_provider == "openai"
    assert result.metadata.embedding_provider == "openai"
    assert result.metadata.call_count_by_task
    assert result.passed == result.total, [check.model_dump() for check in result.checks if not check.passed]
