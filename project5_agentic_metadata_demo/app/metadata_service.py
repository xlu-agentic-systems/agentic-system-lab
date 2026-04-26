from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from project5_agentic_metadata_demo.app.auth import (
    Principal,
    filter_readable_datasets,
    get_current_principal,
    require_dataset_create,
    require_dataset_delete,
    require_dataset_read,
    require_dataset_update,
)
from project5_agentic_metadata_demo.app.database import get_db
from project5_agentic_metadata_demo.app.models import Dataset, DatasetSchema, Lineage, Owner
from project5_agentic_metadata_demo.app.schemas import (
    DatasetCreate,
    DatasetLineageRead,
    DatasetRead,
    DatasetSchemaRead,
    DatasetUpdate,
    DeleteDatasetResponse,
    LineageEdgeRead,
)


router = APIRouter(tags=["metadata"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/datasets", response_model=list[DatasetRead])
def list_datasets(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> list[Dataset]:
    datasets = db.scalars(select(Dataset).order_by(Dataset.id)).all()
    return filter_readable_datasets(principal, datasets)


@router.get("/datasets/search", response_model=list[DatasetRead])
def search_datasets(
    owner_team: str | None = Query(default=None),
    sensitivity_level: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> list[Dataset]:
    stmt: Select[tuple[Dataset]] = select(Dataset)
    if owner_team:
        stmt = stmt.where(Dataset.owner_team == owner_team.lower())
    if sensitivity_level:
        stmt = stmt.where(Dataset.sensitivity_level == sensitivity_level.lower())
    if keyword:
        pattern = f"%{keyword.lower()}%"
        stmt = stmt.where(
            or_(
                Dataset.name.ilike(pattern),
                Dataset.description.ilike(pattern),
                Dataset.data_source.ilike(pattern),
            )
        )
    datasets = db.scalars(stmt.order_by(Dataset.id)).all()
    return filter_readable_datasets(principal, datasets)


@router.post("/datasets", response_model=DatasetRead, status_code=status.HTTP_201_CREATED)
def create_dataset(
    request: DatasetCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> Dataset:
    owner_team = request.owner_team.lower()
    sensitivity_level = request.sensitivity_level.lower()
    require_dataset_create(principal, owner_team=owner_team, sensitivity_level=sensitivity_level)
    if db.scalar(select(Owner).where(Owner.team_name == owner_team)) is None:
        raise HTTPException(status_code=400, detail=f"Owner team {owner_team} does not exist")
    if db.scalar(select(Dataset).where(Dataset.name == request.name)) is not None:
        raise HTTPException(status_code=409, detail=f"Dataset {request.name} already exists")
    now = _utc_now()
    dataset = Dataset(
        name=request.name,
        description=request.description,
        owner_team=owner_team,
        created_at=now,
        updated_at=now,
        data_source=request.data_source,
        sensitivity_level=sensitivity_level,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return dataset


@router.get("/datasets/{dataset_id}", response_model=DatasetRead)
def get_dataset(
    dataset_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
    require_dataset_read(principal, dataset)
    return dataset


@router.patch("/datasets/{dataset_id}", response_model=DatasetRead)
def update_dataset(
    dataset_id: int,
    request: DatasetUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")

    update_data = request.model_dump(exclude_unset=True)
    requested_owner_team = _lower_or_none(update_data.get("owner_team"))
    requested_sensitivity_level = _lower_or_none(update_data.get("sensitivity_level"))
    require_dataset_update(principal, dataset, requested_owner_team, requested_sensitivity_level)

    if requested_owner_team and db.scalar(select(Owner).where(Owner.team_name == requested_owner_team)) is None:
        raise HTTPException(status_code=400, detail=f"Owner team {requested_owner_team} does not exist")

    for field, value in update_data.items():
        if field in {"owner_team", "sensitivity_level"} and isinstance(value, str):
            value = value.lower()
        setattr(dataset, field, value)
    dataset.updated_at = _utc_now()
    db.commit()
    db.refresh(dataset)
    return dataset


@router.delete("/datasets/{dataset_id}", response_model=DeleteDatasetResponse)
def delete_dataset(
    dataset_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
    x_confirm_dangerous_action: str | None = Header(default=None, alias="X-Confirm-Dangerous-Action"),
) -> DeleteDatasetResponse:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
    require_dataset_delete(principal, confirmed=(x_confirm_dangerous_action or "").lower() == "true")
    db.query(Lineage).filter(
        or_(Lineage.upstream_dataset_id == dataset_id, Lineage.downstream_dataset_id == dataset_id)
    ).delete(synchronize_session=False)
    db.delete(dataset)
    db.commit()
    return DeleteDatasetResponse(deleted=True, dataset_id=dataset_id)


@router.get("/datasets/{dataset_id}/schema", response_model=list[DatasetSchemaRead])
def get_dataset_schema(
    dataset_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> list[DatasetSchema]:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
    require_dataset_read(principal, dataset)
    return list(
        db.scalars(
            select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id).order_by(DatasetSchema.id)
        ).all()
    )


@router.get("/datasets/{dataset_id}/lineage", response_model=DatasetLineageRead)
def get_dataset_lineage(
    dataset_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> DatasetLineageRead:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
    require_dataset_read(principal, dataset)

    upstream_edges = db.scalars(select(Lineage).where(Lineage.downstream_dataset_id == dataset_id).order_by(Lineage.id)).all()
    downstream_edges = db.scalars(select(Lineage).where(Lineage.upstream_dataset_id == dataset_id).order_by(Lineage.id)).all()
    return DatasetLineageRead(
        dataset=DatasetRead.model_validate(dataset),
        upstream=[
            LineageEdgeRead(
                relationship_type=edge.relationship_type,
                dataset=DatasetRead.model_validate(edge.upstream_dataset),
            )
            for edge in upstream_edges
            if _can_include_related(principal, edge.upstream_dataset)
        ],
        downstream=[
            LineageEdgeRead(
                relationship_type=edge.relationship_type,
                dataset=DatasetRead.model_validate(edge.downstream_dataset),
            )
            for edge in downstream_edges
            if _can_include_related(principal, edge.downstream_dataset)
        ],
    )


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _lower_or_none(value: object) -> str | None:
    return value.lower() if isinstance(value, str) else None


def _can_include_related(principal: Principal, dataset: Dataset) -> bool:
    try:
        require_dataset_read(principal, dataset)
    except HTTPException:
        return False
    return True
