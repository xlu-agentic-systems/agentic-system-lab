from __future__ import annotations

import asyncio
from collections import Counter
from typing import Protocol, TypeVar

from pydantic import BaseModel


T = TypeVar("T", bound=BaseModel)


class ParseLlmClient(Protocol):
    async def parse(
        self,
        *,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_model: type[T],
    ) -> T:
        ...


class DelayedLlmClient:
    """Adds deterministic async delay and call counting to a local rule client."""

    def __init__(self, inner: ParseLlmClient, *, delay_seconds: float = 0.0) -> None:
        self.inner = inner
        self.delay_seconds = delay_seconds
        self.task_counts: Counter[str] = Counter()

    @property
    def total_calls(self) -> int:
        return sum(self.task_counts.values())

    async def parse(
        self,
        *,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_model: type[T],
    ) -> T:
        self.task_counts[task_name] += 1
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        return await self.inner.parse(
            task_name=task_name,
            system_prompt=system_prompt,
            user_payload=user_payload,
            response_model=response_model,
        )

