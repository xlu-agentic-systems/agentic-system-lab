import asyncio

from project4_agentic_project_copilot.app.chunking_benchmark import run_chunking_benchmark


def test_chunking_benchmark_compares_supported_strategies() -> None:
    run = asyncio.run(run_chunking_benchmark())

    rows = {row.strategy: row for row in run.rows}

    assert set(rows) == {"fixed", "paragraph", "sentence"}
    assert run.cases == 5
    assert all(row.chunk_count > 0 for row in rows.values())
    assert all(row.recall_at_2 >= 0.8 for row in rows.values())
    assert max(row.expansion_ratio for row in rows.values()) < 1.5
