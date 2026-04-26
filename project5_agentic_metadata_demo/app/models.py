from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from project5_agentic_metadata_demo.app.database import Base


class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    contact_email: Mapped[str] = mapped_column(String(200))
    slack_channel: Mapped[str] = mapped_column(String(80))

    datasets: Mapped[list["Dataset"]] = relationship(back_populates="owner")


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    owner_team: Mapped[str] = mapped_column(String(80), ForeignKey("owners.team_name"), index=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32), index=True)
    data_source: Mapped[str] = mapped_column(String(120))
    sensitivity_level: Mapped[str] = mapped_column(String(40), index=True)

    owner: Mapped[Owner] = relationship(back_populates="datasets")
    columns: Mapped[list["DatasetSchema"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        order_by="DatasetSchema.id",
    )


class DatasetSchema(Base):
    __tablename__ = "schemas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(Integer, ForeignKey("datasets.id"), index=True)
    column_name: Mapped[str] = mapped_column(String(120))
    column_type: Mapped[str] = mapped_column(String(80))
    is_nullable: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text)

    dataset: Mapped[Dataset] = relationship(back_populates="columns")


class Lineage(Base):
    __tablename__ = "lineage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upstream_dataset_id: Mapped[int] = mapped_column(Integer, ForeignKey("datasets.id"), index=True)
    downstream_dataset_id: Mapped[int] = mapped_column(Integer, ForeignKey("datasets.id"), index=True)
    relationship_type: Mapped[str] = mapped_column(String(80))

    upstream_dataset: Mapped[Dataset] = relationship(foreign_keys=[upstream_dataset_id])
    downstream_dataset: Mapped[Dataset] = relationship(foreign_keys=[downstream_dataset_id])

