from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.embeddings import EmbeddingClient, OpenAIEmbeddingClient, cosine_similarity
from project4_agentic_project_copilot.app.models import RetrievedChunk, UploadResponse


SUPPORTED_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text", ".rst"}


class DocumentStore:
    def __init__(
        self,
        db: CopilotDatabase | None = None,
        embedding_client: EmbeddingClient | None = None,
        *,
        chunk_size: int = 900,
        chunk_overlap: int = 120,
    ) -> None:
        self.db = db or CopilotDatabase()
        self.embedding_client = embedding_client or OpenAIEmbeddingClient()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    async def ingest_bytes(self, *, filename: str, content_type: str, content: bytes) -> UploadResponse:
        text = extract_text(filename, content)
        document_id = str(uuid.uuid4())
        chunks = chunk_text(text, chunk_size=self.chunk_size, overlap=self.chunk_overlap)
        embeddings = []
        for chunk in chunks:
            embeddings.append(await self.embedding_client.embed(chunk))
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO documents(document_id, filename, content_type) VALUES (?, ?, ?)",
                (document_id, filename, content_type or "application/octet-stream"),
            )
            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_id = f"{document_id}:{index}"
                conn.execute(
                    """
                    INSERT INTO document_chunks(chunk_id, document_id, chunk_index, text, embedding_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (chunk_id, document_id, index, chunk, json.dumps(embedding)),
                )
            conn.commit()
        return UploadResponse(document_id=document_id, filename=filename, chunk_count=len(chunks))

    async def search(self, query: str, *, top_k: int = 4, document_id: str | None = None) -> list[RetrievedChunk]:
        query_embedding = await self.embedding_client.embed(query)
        with self.db.connect() as conn:
            if document_id:
                rows = conn.execute(
                    """
                    SELECT c.chunk_id, c.document_id, c.chunk_index, c.text, c.embedding_json, d.filename
                    FROM document_chunks c
                    JOIN documents d ON d.document_id = c.document_id
                    WHERE c.document_id = ?
                    """,
                    (document_id,),
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
        return sorted(scored, key=lambda item: item.score, reverse=True)[:top_k]

    def has_documents(self, document_id: str | None = None) -> bool:
        with self.db.connect() as conn:
            if document_id:
                row = conn.execute("SELECT 1 FROM document_chunks WHERE document_id = ? LIMIT 1", (document_id,)).fetchone()
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


def chunk_text(text: str, *, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    normalized = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not normalized:
        raise ValueError("Uploaded document did not contain extractable text.")
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


def short_quote(text: str, max_chars: int = 180) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 3].rstrip() + "..."
