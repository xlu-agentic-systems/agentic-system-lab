from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.embeddings import (
    EmbeddingClient,
    OpenAIEmbeddingClient,
    cosine_similarity,
    embed_many,
)
from project4_agentic_project_copilot.app.models import DocumentSummary, RetrievedChunk, UploadResponse


SUPPORTED_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text", ".rst"}
ChunkingStrategy = Literal["fixed", "paragraph", "sentence"]
DEFAULT_CHUNKING_STRATEGY: ChunkingStrategy = "fixed"
DEFAULT_EMBEDDING_BATCH_SIZE = 64


@dataclass(frozen=True)
class FreshnessResult:
    processed_event_count: int
    reindexed_chunk_count: int
    skipped_event_count: int = 0


class DocumentStore:
    def __init__(
        self,
        db: CopilotDatabase | None = None,
        embedding_client: EmbeddingClient | None = None,
        *,
        chunk_size: int = 900,
        chunk_overlap: int = 120,
        chunking_strategy: ChunkingStrategy = DEFAULT_CHUNKING_STRATEGY,
        embedding_batch_size: int | None = None,
    ) -> None:
        self.db = db or CopilotDatabase()
        self.embedding_client = embedding_client or OpenAIEmbeddingClient()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.chunking_strategy = chunking_strategy
        self.embedding_batch_size = embedding_batch_size or int(
            os.getenv("OPENAI_EMBEDDING_BATCH_SIZE", str(DEFAULT_EMBEDDING_BATCH_SIZE))
        )

    async def ingest_bytes(self, *, filename: str, content_type: str, content: bytes) -> UploadResponse:
        text = extract_text(filename, content)
        document_id = str(uuid.uuid4())
        chunks = chunk_text(
            text,
            chunk_size=self.chunk_size,
            overlap=self.chunk_overlap,
            strategy=self.chunking_strategy,
        )
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO documents(document_id, filename, content_type) VALUES (?, ?, ?)",
                (document_id, filename, content_type or "application/octet-stream"),
            )
            for index, chunk in enumerate(chunks):
                chunk_id = f"{document_id}:{index}"
                conn.execute(
                    """
                    INSERT INTO document_chunks(chunk_id, document_id, chunk_index, text, embedding_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (chunk_id, document_id, index, chunk, "[]"),
                )
            conn.commit()
        freshness = await self.process_pending_index_events()
        return UploadResponse(
            document_id=document_id,
            filename=filename,
            chunk_count=len(chunks),
            reindexed_chunk_count=freshness.reindexed_chunk_count,
        )

    def list_documents(self) -> list[DocumentSummary]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT d.document_id, d.filename, d.content_type, d.created_at, COUNT(c.chunk_id) AS chunk_count
                FROM documents d
                LEFT JOIN document_chunks c ON c.document_id = d.document_id
                GROUP BY d.document_id, d.filename, d.content_type, d.created_at
                ORDER BY d.created_at DESC, d.filename
                """
            ).fetchall()
        return [
            DocumentSummary(
                document_id=row["document_id"],
                filename=row["filename"],
                content_type=row["content_type"],
                chunk_count=int(row["chunk_count"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_document(self, document_id: str) -> DocumentSummary | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT d.document_id, d.filename, d.content_type, d.created_at, COUNT(c.chunk_id) AS chunk_count
                FROM documents d
                LEFT JOIN document_chunks c ON c.document_id = d.document_id
                WHERE d.document_id = ?
                GROUP BY d.document_id, d.filename, d.content_type, d.created_at
                """,
                (document_id,),
            ).fetchone()
        if row is None:
            return None
        return DocumentSummary(
            document_id=row["document_id"],
            filename=row["filename"],
            content_type=row["content_type"],
            chunk_count=int(row["chunk_count"]),
            created_at=row["created_at"],
        )

    async def delete_document(self, document_id: str) -> tuple[bool, int]:
        with self.db.connect() as conn:
            existing = conn.execute("SELECT 1 FROM documents WHERE document_id = ?", (document_id,)).fetchone()
            if existing is None:
                return False, 0
            conn.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
            conn.commit()
        freshness = await self.process_pending_index_events()
        return True, freshness.reindexed_chunk_count

    async def reindex_embeddings(self) -> int:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT chunk_id, text
                FROM document_chunks
                ORDER BY document_id, chunk_index
                """
            ).fetchall()
        updates = []
        for batch in _batched(rows, self.embedding_batch_size):
            embeddings = await embed_many(self.embedding_client, [row["text"] for row in batch])
            if len(embeddings) != len(batch):
                raise RuntimeError("Embedding batch response length did not match input length.")
            updates.extend(
                (json.dumps(embedding), row["chunk_id"])
                for row, embedding in zip(batch, embeddings, strict=True)
            )
        if updates:
            with self.db.connect() as conn:
                conn.executemany("UPDATE document_chunks SET embedding_json = ? WHERE chunk_id = ?", updates)
                conn.commit()
        return len(updates)

    async def process_pending_index_events(self, *, limit: int | None = None) -> FreshnessResult:
        query = """
            SELECT event_id, event_type, chunk_id
            FROM document_index_events
            WHERE status = 'pending'
            ORDER BY event_id
        """
        params: tuple[int, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        with self.db.connect() as conn:
            events = conn.execute(query, params).fetchall()
        skipped_count = 0
        refresh_items = []
        for event in events:
            if event["event_type"] in {"chunk_inserted", "chunk_text_updated"} and event["chunk_id"]:
                row = self._get_chunk_for_refresh(event["chunk_id"])
                if row is None:
                    skipped_count += 1
                    self._mark_index_event_processed(event["event_id"])
                else:
                    refresh_items.append(
                        {
                            "event_id": event["event_id"],
                            "chunk_id": event["chunk_id"],
                            "text": row["text"],
                        }
                    )
            else:
                skipped_count += 1
                self._mark_index_event_processed(event["event_id"])

        reindexed_count = 0
        for batch in _batched(refresh_items, self.embedding_batch_size):
            try:
                embeddings = await embed_many(self.embedding_client, [item["text"] for item in batch])
                if len(embeddings) != len(batch):
                    raise RuntimeError("Embedding batch response length did not match input length.")
                with self.db.connect() as conn:
                    conn.executemany(
                        "UPDATE document_chunks SET embedding_json = ? WHERE chunk_id = ?",
                        [
                            (json.dumps(embedding), item["chunk_id"])
                            for item, embedding in zip(batch, embeddings, strict=True)
                        ],
                    )
                    conn.executemany(
                        """
                        UPDATE document_index_events
                        SET status = 'processed', processed_at = CURRENT_TIMESTAMP, error = NULL
                        WHERE event_id = ?
                        """,
                        [(item["event_id"],) for item in batch],
                    )
                    conn.commit()
                reindexed_count += len(batch)
            except Exception as exc:
                for item in batch:
                    self._mark_index_event_failed(item["event_id"], str(exc))
                raise
        return FreshnessResult(
            processed_event_count=len(events),
            reindexed_chunk_count=reindexed_count,
            skipped_event_count=skipped_count,
        )

    def pending_index_event_count(self) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM document_index_events WHERE status = 'pending'"
            ).fetchone()
        return int(row["count"])

    def _get_chunk_for_refresh(self, chunk_id: str):
        with self.db.connect() as conn:
            return conn.execute(
                "SELECT chunk_id, text FROM document_chunks WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()

    def _mark_index_event_processed(self, event_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE document_index_events
                SET status = 'processed', processed_at = CURRENT_TIMESTAMP, error = NULL
                WHERE event_id = ?
                """,
                (event_id,),
            )
            conn.commit()

    def _mark_index_event_failed(self, event_id: int, error: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE document_index_events
                SET status = 'failed', processed_at = CURRENT_TIMESTAMP, error = ?
                WHERE event_id = ?
                """,
                (error, event_id),
            )
            conn.commit()

    async def search(
        self,
        query: str,
        *,
        top_k: int = 4,
        document_id: str | None = None,
        document_ids: list[str] | None = None,
        diversify: bool = True,
    ) -> list[RetrievedChunk]:
        target_document_ids = _target_document_ids(document_id=document_id, document_ids=document_ids)
        if target_document_ids == []:
            return []
        query_embedding = await self.embedding_client.embed(query)
        with self.db.connect() as conn:
            if target_document_ids is not None:
                placeholders = ", ".join("?" for _ in target_document_ids)
                rows = conn.execute(
                    f"""
                    SELECT c.chunk_id, c.document_id, c.chunk_index, c.text, c.embedding_json, d.filename
                    FROM document_chunks c
                    JOIN documents d ON d.document_id = c.document_id
                    WHERE c.document_id IN ({placeholders})
                    """,
                    tuple(target_document_ids),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT c.chunk_id, c.document_id, c.chunk_index, c.text, c.embedding_json, d.filename
                    FROM document_chunks c
                    JOIN documents d ON d.document_id = c.document_id
                    """
                ).fetchall()
        scored = []
        for row in rows:
            score = cosine_similarity(query_embedding, json.loads(row["embedding_json"]))
            scored.append(
                RetrievedChunk(
                    document_id=row["document_id"],
                    filename=row["filename"],
                    chunk_id=row["chunk_id"],
                    chunk_index=row["chunk_index"],
                    score=score,
                    quote=short_quote(row["text"]),
                    text=row["text"],
                )
            )
        ranked = sorted(scored, key=lambda item: item.score, reverse=True)
        if diversify and not document_id:
            return _diversified_top(ranked, top_k=top_k)
        return ranked[:top_k]

    def has_documents(self, document_id: str | None = None, document_ids: list[str] | None = None) -> bool:
        target_document_ids = _target_document_ids(document_id=document_id, document_ids=document_ids)
        if target_document_ids == []:
            return False
        with self.db.connect() as conn:
            if target_document_ids is not None:
                placeholders = ", ".join("?" for _ in target_document_ids)
                row = conn.execute(
                    f"SELECT 1 FROM document_chunks WHERE document_id IN ({placeholders}) LIMIT 1",
                    tuple(target_document_ids),
                ).fetchone()
            else:
                row = conn.execute("SELECT 1 FROM document_chunks LIMIT 1").fetchone()
        return row is not None


def extract_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF upload requires the pypdf package.") from exc
        import io

        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if suffix in SUPPORTED_TEXT_SUFFIXES or not suffix:
        return content.decode("utf-8", errors="replace")
    raise ValueError(f"Unsupported file type for {filename}. Use markdown, text, or PDF files.")


def chunk_text(
    text: str,
    *,
    chunk_size: int = 900,
    overlap: int = 120,
    strategy: ChunkingStrategy = DEFAULT_CHUNKING_STRATEGY,
) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        raise ValueError("Uploaded document did not contain extractable text.")
    if strategy == "fixed":
        return _chunk_fixed(normalized, chunk_size=chunk_size, overlap=overlap)
    if strategy == "paragraph":
        return _chunk_units(_split_paragraphs(normalized), chunk_size=chunk_size, overlap=overlap)
    if strategy == "sentence":
        return _chunk_units(_split_sentences(normalized), chunk_size=chunk_size, overlap=overlap)
    raise ValueError(f"Unsupported chunking strategy: {strategy}")


def normalize_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.strip())


def _chunk_fixed(normalized: str, *, chunk_size: int, overlap: int) -> list[str]:
    chunks = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _batched(items, batch_size: int):
    size = max(1, batch_size)
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _split_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    unique = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _target_document_ids(*, document_id: str | None, document_ids: list[str] | None) -> list[str] | None:
    if document_id:
        return _dedupe([document_id])
    if document_ids is not None:
        return _dedupe(document_ids)
    return None


def _diversified_top(chunks: list[RetrievedChunk], *, top_k: int) -> list[RetrievedChunk]:
    by_document: dict[str, list[RetrievedChunk]] = {}
    for chunk in chunks:
        by_document.setdefault(chunk.document_id, []).append(chunk)
    document_order = sorted(
        by_document,
        key=lambda document_id: by_document[document_id][0].score,
        reverse=True,
    )
    diversified: list[RetrievedChunk] = []
    while len(diversified) < top_k and document_order:
        next_order = []
        for document_id in document_order:
            document_chunks = by_document[document_id]
            if document_chunks and len(diversified) < top_k:
                diversified.append(document_chunks.pop(0))
            if document_chunks:
                next_order.append(document_id)
        document_order = next_order
    return diversified


def _split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text)
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]


def _chunk_units(units: list[str], *, chunk_size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > chunk_size:
            if current:
                chunks.append(current)
                current = _overlap_tail(current, overlap)
            for chunk in _chunk_fixed(unit, chunk_size=chunk_size, overlap=overlap):
                if current and len(current) + 1 + len(chunk) <= chunk_size:
                    current = f"{current} {chunk}".strip()
                else:
                    if current:
                        chunks.append(current)
                    current = chunk
            continue
        candidate = f"{current}\n\n{unit}".strip() if current else unit
        if len(candidate) <= chunk_size:
            current = candidate
            continue
        if current:
            chunks.append(current)
        tail = _overlap_tail(current, overlap)
        candidate = f"{tail}\n\n{unit}".strip() if tail else unit
        current = candidate if len(candidate) <= chunk_size else unit
    if current:
        chunks.append(current)
    return chunks


def _overlap_tail(text: str, overlap: int) -> str:
    if overlap <= 0 or not text:
        return ""
    return text[-overlap:].strip()


def short_quote(text: str, max_chars: int = 180) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 3].rstrip() + "..."
