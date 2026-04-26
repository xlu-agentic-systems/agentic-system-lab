from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from project5_agentic_metadata_demo.app import main
from project5_agentic_metadata_demo.app.database import Base, get_db
from project5_agentic_metadata_demo.app.models import Dataset
from project5_agentic_metadata_demo.app.seed import seed_database


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with TestingSessionLocal() as db:
        seed_database(db)

    def override_get_db() -> Iterator[Session]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = override_get_db
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()


def test_database_seeding_creates_sample_datasets(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata.db'}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    with TestingSessionLocal() as db:
        count = seed_database(db)
        datasets = db.scalars(select(Dataset).order_by(Dataset.id)).all()

    assert count == 8
    assert len(datasets) == 8
    assert {dataset.owner_team for dataset in datasets} >= {"finance", "analytics", "growth"}


def test_list_datasets(client: TestClient) -> None:
    response = client.get("/datasets")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 8
    assert body[0]["name"] == "revenue_transactions"


def test_search_datasets_by_owner_and_keyword(client: TestClient) -> None:
    response = client.get("/datasets/search", params={"owner_team": "finance", "keyword": "revenue"})

    assert response.status_code == 200
    names = [dataset["name"] for dataset in response.json()]
    assert names == ["revenue_transactions", "revenue_forecast"]


def test_get_schema(client: TestClient) -> None:
    response = client.get("/datasets/1/schema")

    assert response.status_code == 200
    columns = response.json()
    assert [column["column_name"] for column in columns] == [
        "transaction_id",
        "account_id",
        "amount_usd",
        "booked_at",
    ]

