from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OwnerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    team_name: str
    contact_email: str
    slack_channel: str


class DatasetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    owner_team: str
    created_at: str
    updated_at: str
    data_source: str
    sensitivity_level: str


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    owner_team: str = Field(min_length=1)
    data_source: str = Field(min_length=1)
    sensitivity_level: str = Field(min_length=1)


class DatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    owner_team: str | None = Field(default=None, min_length=1)
    data_source: str | None = Field(default=None, min_length=1)
    sensitivity_level: str | None = Field(default=None, min_length=1)


class DeleteDatasetResponse(BaseModel):
    deleted: bool
    dataset_id: int


class DatasetSchemaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset_id: int
    column_name: str
    column_type: str
    is_nullable: bool
    description: str


class LineageEdgeRead(BaseModel):
    relationship_type: str
    dataset: DatasetRead


class DatasetLineageRead(BaseModel):
    dataset: DatasetRead
    upstream: list[LineageEdgeRead]
    downstream: list[LineageEdgeRead]


class AgentQueryRequest(BaseModel):
    question: str = Field(min_length=1)


class ToolCallRecord(BaseModel):
    tool: str
    arguments: dict[str, Any]


class AgentQueryResponse(BaseModel):
    answer: str
    tool_calls: list[ToolCallRecord]
    raw_results: dict[str, Any]
