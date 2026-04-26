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

