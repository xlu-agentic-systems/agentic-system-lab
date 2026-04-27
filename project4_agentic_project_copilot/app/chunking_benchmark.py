from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from pydantic import BaseModel

from project4_agentic_project_copilot.app.document_store import ChunkingStrategy, chunk_text, normalize_text
from project4_agentic_project_copilot.app.embeddings import HashEmbeddingClient, cosine_similarity


BENCHMARK_CORPUS = """
# Apollo Launch Plan

The Apollo Launch project needs a rollout checklist with clear launch gates,
release owners, QA signoff, and a rollback plan before public launch.

## API Contract Review

Backend and frontend engineers must review pagination fields, authentication
headers, idempotency behavior, and error response formats before integration.
The API contract review is blocked until pagination fields are confirmed.

## Design System Follow-up

The design system team should audit button variants, disabled states, focus
states, and empty states. The audit should be separate from launch readiness.

## Customer Migration Notes

Customer migration work requires owner mapping, support coverage, import dry
runs, and a post-launch monitoring window for failed records.

## Incident Readiness

Incident readiness requires an on-call rotation, escalation paths, dashboard
links, and a rollback decision owner.
"""

BENCHMARK_STRATEGIES: tuple[ChunkingStrategy, ...] = ("fixed", "paragraph", "sentence")


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    query: str
    expected_terms: tuple[str, ...]


BENCHMARK_CASES = (
    BenchmarkCase(
        case_id="rollout",
        query="What launch rollout work needs QA signoff and rollback planning?",
        expected_terms=("rollout checklist", "qa signoff", "rollback plan"),
    ),
    BenchmarkCase(
        case_id="api_contract",
        query="What is blocking API contract review?",
        expected_terms=("api contract review", "pagination fields", "blocked"),
    ),
    BenchmarkCase(
        case_id="design_system",
        query="What should the design system audit cover?",
        expected_terms=("button variants", "disabled states", "empty states"),
    ),
    BenchmarkCase(
        case_id="migration",
        query="What does customer migration require?",
        expected_terms=("owner mapping", "import dry runs", "monitoring window"),
    ),
    BenchmarkCase(
        case_id="incident",
        query="What is needed for incident readiness?",
        expected_terms=("on-call rotation", "escalation paths", "rollback decision owner"),
    ),
)


class ChunkingBenchmarkRow(BaseModel):
    strategy: ChunkingStrategy
    chunk_count: int
    average_chunk_chars: float
    expansion_ratio: float
    recall_at_2: float
    mean_matched_terms_at_2: float
    mean_top_score: float


class ChunkingBenchmarkRun(BaseModel):
    corpus_chars: int
    cases: int
    chunk_size: int
    overlap: int
    rows: list[ChunkingBenchmarkRow]


async def run_chunking_benchmark(
    *,
    chunk_size: int = 220,
    overlap: int = 40,
    top_k: int = 2,
) -> ChunkingBenchmarkRun:
    embedding_client = HashEmbeddingClient()
    normalized = normalize_text(BENCHMARK_CORPUS)
    rows: list[ChunkingBenchmarkRow] = []
    for strategy in BENCHMARK_STRATEGIES:
        chunks = chunk_text(normalized, chunk_size=chunk_size, overlap=overlap, strategy=strategy)
        chunk_embeddings = [await embedding_client.embed(chunk) for chunk in chunks]
        case_hits = 0
        matched_terms_total = 0
        top_scores = []
        for case in BENCHMARK_CASES:
            query_embedding = await embedding_client.embed(case.query)
            scored = sorted(
                zip(chunks, chunk_embeddings, strict=True),
                key=lambda item: cosine_similarity(query_embedding, item[1]),
                reverse=True,
            )
            top = scored[:top_k]
            top_text = "\n".join(chunk for chunk, _embedding in top).lower()
            matched_terms = sum(1 for term in case.expected_terms if term in top_text)
            if matched_terms:
                case_hits += 1
            matched_terms_total += matched_terms
            top_scores.append(cosine_similarity(query_embedding, top[0][1]) if top else 0.0)
        rows.append(
            ChunkingBenchmarkRow(
                strategy=strategy,
                chunk_count=len(chunks),
                average_chunk_chars=round(sum(len(chunk) for chunk in chunks) / len(chunks), 2),
                expansion_ratio=round(sum(len(chunk) for chunk in chunks) / len(normalized), 3),
                recall_at_2=round(case_hits / len(BENCHMARK_CASES), 3),
                mean_matched_terms_at_2=round(matched_terms_total / len(BENCHMARK_CASES), 3),
                mean_top_score=round(sum(top_scores) / len(top_scores), 3),
            )
        )
    return ChunkingBenchmarkRun(
        corpus_chars=len(normalized),
        cases=len(BENCHMARK_CASES),
        chunk_size=chunk_size,
        overlap=overlap,
        rows=rows,
    )


def main() -> None:
    run = asyncio.run(run_chunking_benchmark())
    print(json.dumps(run.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
