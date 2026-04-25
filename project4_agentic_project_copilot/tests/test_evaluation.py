import asyncio

from project4_agentic_project_copilot.app.evaluation import run_evaluation


def test_evaluation_harness_passes_sample_cases() -> None:
    result = asyncio.run(run_evaluation())

    assert result.total == 6
    assert result.passed == 6
