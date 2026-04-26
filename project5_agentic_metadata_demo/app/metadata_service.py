from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from project5_agentic_metadata_demo.app.database import get_db
from project5_agentic_metadata_demo.app.models import Dataset, DatasetSchema, Lineage
from project5_agentic_metadata_demo.app.schemas import DatasetLineageRead, DatasetRead, DatasetSchemaRead, LineageEdgeRead


router = APIRouter(tags=["metadata"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/datasets", response_model=list[DatasetRead])
def list_datasets(db: Session = Depends(get_db)) -> list[Dataset]:
    return list(db.scalars(select(Dataset).order_by(Dataset.id)).all())


@router.get("/datasets/search", response_model=list[DatasetRead])
def search_datasets(
    owner_team: str | None = Query(default=None),
    sensitivity_level: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    db: Session = Depends(get_db),
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
    return list(db.scalars(stmt.order_by(Dataset.id)).all())


@router.get("/datasets/{dataset_id}", response_model=DatasetRead)
def get_dataset(dataset_id: int, db: Session = Depends(get_db)) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
    return dataset


@router.get("/datasets/{dataset_id}/schema", response_model=list[DatasetSchemaRead])
def get_dataset_schema(dataset_id: int, db: Session = Depends(get_db)) -> list[DatasetSchema]:
    if db.get(Dataset, dataset_id) is None:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")
    return list(
        db.scalars(
            select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id).order_by(DatasetSchema.id)
        ).all()
    )


@router.get("/datasets/{dataset_id}/lineage", response_model=DatasetLineageRead)
def get_dataset_lineage(dataset_id: int, db: Session = Depends(get_db)) -> DatasetLineageRead:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset_id} not found")

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
        ],
        downstream=[
            LineageEdgeRead(
                relationship_type=edge.relationship_type,
                dataset=DatasetRead.model_validate(edge.downstream_dataset),
            )
            for edge in downstream_edges
        ],
    )

