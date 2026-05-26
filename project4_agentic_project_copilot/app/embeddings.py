from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Sequence
from typing import Protocol


class EmbeddingClient(Protocol):
    async def embed(self, text: str) -> list[float]:
        ...


class HashEmbeddingClient:
    """Deterministic local embedding client for tests and offline demos."""

    def __init__(self, dimensions: int = 128) -> None:
        self.dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[a-z0-9_]+", text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]


class OpenAIEmbeddingClient:
    def __init__(self, *, model: str | None = None, timeout_seconds: float | None = None) -> None:
        self.model = model or os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        self.timeout_seconds = timeout_seconds or float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
        self._client = None

    async def embed(self, text: str) -> list[float]:
        client = self._get_client()
        response = await client.embeddings.create(model=self.model, input=text)
        return list(response.data[0].embedding)

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        response = await client.embeddings.create(model=self.model, input=list(texts))
        return [list(item.embedding) for item in response.data]

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "The openai package is required for embedding calls. Install dependencies with "
                    '`pip install -e ".[dev]"`.'
                ) from exc
            self._client = AsyncOpenAI(timeout=self.timeout_seconds)
        return self._client


async def embed_many(client: EmbeddingClient, texts: Sequence[str]) -> list[list[float]]:
    batch_embed = getattr(client, "embed_many", None)
    if batch_embed is not None:
        return await batch_embed(texts)
    return [await client.embed(text) for text in texts]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)
