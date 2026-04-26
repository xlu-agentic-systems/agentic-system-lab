from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI


class MetadataToolError(RuntimeError):
    pass


class MetadataTools:
    """Tool facade over the metadata microservice REST API."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://metadata-service") as client:
            response = await client.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        if response.status_code >= 400:
            raise MetadataToolError(response.text)
        return response.json()

    async def list_datasets(self) -> Any:
        return await self._get("/datasets")

    async def search_datasets(
        self,
        owner_team: str | None = None,
        sensitivity_level: str | None = None,
        keyword: str | None = None,
    ) -> Any:
        return await self._get(
            "/datasets/search",
            {"owner_team": owner_team, "sensitivity_level": sensitivity_level, "keyword": keyword},
        )

    async def get_dataset(self, dataset_id: int) -> Any:
        return await self._get(f"/datasets/{dataset_id}")

    async def get_schema(self, dataset_id: int) -> Any:
        return await self._get(f"/datasets/{dataset_id}/schema")

    async def get_lineage(self, dataset_id: int) -> Any:
        return await self._get(f"/datasets/{dataset_id}/lineage")

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        if tool == "list_datasets":
            return await self.list_datasets()
        if tool == "search_datasets":
            return await self.search_datasets(**arguments)
        if tool == "get_dataset":
            return await self.get_dataset(**arguments)
        if tool == "get_schema":
            return await self.get_schema(**arguments)
        if tool == "get_lineage":
            return await self.get_lineage(**arguments)
        raise MetadataToolError(f"Unknown metadata tool: {tool}")

