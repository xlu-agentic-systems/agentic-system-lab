from __future__ import annotations

from typing import Any, Literal

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


class DatasetFilters(BaseModel):
    owner_team: str | None = None
    sensitivity_level: str | None = None
    keyword: str | None = None


class AgentTaskRequest(BaseModel):
    task: Literal["find_datasets", "get_dataset", "create_dataset", "update_dataset", "delete_dataset"]
    filters: DatasetFilters | None = None
    include: list[Literal["schema", "lineage"]] = Field(default_factory=list)
    dataset_id: int | None = None
    dataset: DatasetCreate | None = None
    updates: DatasetUpdate | None = None
    confirm_dangerous_action: bool = False
    reason: str | None = None


class AgentTaskError(BaseModel):
    tool: str
    status_code: int
    message: str


class AgentTaskResponse(BaseModel):
    status: Literal["completed", "blocked", "failed"]
    answer: str
    datasets: list[dict[str, Any]] = Field(default_factory=list)
    schemas_by_dataset_id: dict[str, Any] = Field(default_factory=dict)
    lineage_by_dataset_id: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    raw_results: dict[str, Any] = Field(default_factory=dict)
    errors: list[AgentTaskError] = Field(default_factory=list)
