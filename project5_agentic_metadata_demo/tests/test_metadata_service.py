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


SERVICE_HEADERS = {"X-User": "service-a", "X-Team": "platform", "X-Role": "service"}
FINANCE_VIEWER_HEADERS = {"X-User": "finley", "X-Team": "finance", "X-Role": "viewer"}
ANALYTICS_VIEWER_HEADERS = {"X-User": "ana", "X-Team": "analytics", "X-Role": "viewer"}
FINANCE_EDITOR_HEADERS = {"X-User": "fran", "X-Team": "finance", "X-Role": "editor"}
ADMIN_HEADERS = {"X-User": "admin", "X-Team": "security", "X-Role": "admin"}


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
    response = client.get("/datasets", headers=SERVICE_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 8
    assert body[0]["name"] == "revenue_transactions"


def test_search_datasets_by_owner_and_keyword(client: TestClient) -> None:
    response = client.get(
        "/datasets/search",
        params={"owner_team": "finance", "keyword": "revenue"},
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200
    names = [dataset["name"] for dataset in response.json()]
    assert names == ["revenue_transactions", "revenue_forecast"]


def test_get_schema(client: TestClient) -> None:
    response = client.get("/datasets/1/schema", headers=SERVICE_HEADERS)

    assert response.status_code == 200
    columns = response.json()
    assert [column["column_name"] for column in columns] == [
        "transaction_id",
        "account_id",
        "amount_usd",
        "booked_at",
    ]


def test_metadata_endpoints_require_identity(client: TestClient) -> None:
    response = client.get("/datasets")

    assert response.status_code == 401
    assert response.json()["detail"] == "X-User, X-Team, and X-Role headers are required"


def test_policy_filters_cross_team_sensitive_reads(client: TestClient) -> None:
    response = client.get("/datasets/search", params={"owner_team": "finance"}, headers=ANALYTICS_VIEWER_HEADERS)

    assert response.status_code == 200
    assert [dataset["name"] for dataset in response.json()] == ["revenue_forecast"]

    denied = client.get("/datasets/1/schema", headers=ANALYTICS_VIEWER_HEADERS)
    assert denied.status_code == 403
    assert "not allowed to read" in denied.json()["detail"]


def test_owner_team_can_read_own_sensitive_dataset(client: TestClient) -> None:
    response = client.get("/datasets/1/schema", headers=FINANCE_VIEWER_HEADERS)

    assert response.status_code == 200
    assert response.json()[0]["column_name"] == "transaction_id"


def test_editor_can_create_non_sensitive_dataset_for_own_team(client: TestClient) -> None:
    response = client.post(
        "/datasets",
        headers=FINANCE_EDITOR_HEADERS,
        json={
            "name": "finance_public_metrics",
            "description": "Published finance metrics for company dashboards.",
            "owner_team": "finance",
            "data_source": "metrics_store",
            "sensitivity_level": "internal",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "finance_public_metrics"
    assert body["owner_team"] == "finance"


def test_editor_cannot_create_sensitive_or_cross_team_dataset(client: TestClient) -> None:
    sensitive = client.post(
        "/datasets",
        headers=FINANCE_EDITOR_HEADERS,
        json={
            "name": "secret_finance_metrics",
            "description": "Highly sensitive finance metrics.",
            "owner_team": "finance",
            "data_source": "metrics_store",
            "sensitivity_level": "high",
        },
    )
    cross_team = client.post(
        "/datasets",
        headers=FINANCE_EDITOR_HEADERS,
        json={
            "name": "analytics_from_finance",
            "description": "Cross-team ownership attempt.",
            "owner_team": "analytics",
            "data_source": "metrics_store",
            "sensitivity_level": "internal",
        },
    )

    assert sensitive.status_code == 403
    assert cross_team.status_code == 403


def test_delete_requires_admin_and_explicit_confirmation(client: TestClient) -> None:
    viewer_delete = client.delete("/datasets/1", headers=FINANCE_VIEWER_HEADERS)
    admin_without_confirmation = client.delete("/datasets/1", headers=ADMIN_HEADERS)
    admin_with_confirmation = client.delete(
        "/datasets/1",
        headers={**ADMIN_HEADERS, "X-Confirm-Dangerous-Action": "true"},
    )

    assert viewer_delete.status_code == 403
    assert admin_without_confirmation.status_code == 403
    assert admin_with_confirmation.status_code == 200
    assert admin_with_confirmation.json() == {"deleted": True, "dataset_id": 1}
