from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from project5_agentic_metadata_demo.app import main
from project5_agentic_metadata_demo.app.database import Base, get_db
from project5_agentic_metadata_demo.app.seed import seed_database


SERVICE_HEADERS = {"X-User": "service-a", "X-Team": "platform", "X-Role": "service"}
FINANCE_EDITOR_HEADERS = {"X-User": "fran", "X-Team": "finance", "X-Role": "editor"}
ANALYTICS_VIEWER_HEADERS = {"X-User": "ana", "X-Team": "analytics", "X-Role": "viewer"}
ADMIN_HEADERS = {"X-User": "admin", "X-Team": "security", "X-Role": "admin"}


@pytest.fixture()
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
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


def test_rule_based_agent_query_returns_answer_and_raw_results(client: TestClient) -> None:
    response = client.post(
        "/agent/query",
        headers=SERVICE_HEADERS,
        json={"question": "Find revenue-related datasets owned by the finance team and show their schemas."},
    )

    assert response.status_code == 200
    body = response.json()
    assert "retrieved" in body["answer"]
    assert body["tool_calls"][0] == {
        "tool": "search_datasets",
        "arguments": {"owner_team": "finance", "keyword": "revenue"},
    }
    assert any(call["tool"] == "get_schema" for call in body["tool_calls"])
    assert body["raw_results"]["1_search_datasets"][0]["name"] == "revenue_transactions"


def test_agent_calls_correct_metadata_tool_for_lineage(client: TestClient) -> None:
    response = client.post(
        "/agent/query",
        headers=SERVICE_HEADERS,
        json={"question": "show lineage for user profile dataset"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tool_calls"][0] == {
        "tool": "search_datasets",
        "arguments": {"keyword": "customer profile"},
    }
    assert body["tool_calls"][1] == {"tool": "get_lineage", "arguments": {"dataset_id": 3}}
    assert "customer_profiles" in body["answer"]


def test_agent_requires_identity(client: TestClient) -> None:
    response = client.post("/agent/query", json={"question": "show all datasets"})

    assert response.status_code == 401


def test_agent_policy_blocks_cross_team_sensitive_read(client: TestClient) -> None:
    response = client.post(
        "/agent/query",
        headers=ANALYTICS_VIEWER_HEADERS,
        json={"question": "find datasets owned by finance"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "revenue_forecast" in body["answer"]
    assert "revenue_transactions" not in body["answer"]
    assert all(dataset["sensitivity_level"] != "high" for dataset in body["raw_results"]["1_search_datasets"])


def test_agent_delete_prompt_is_gated_by_policy(client: TestClient) -> None:
    response = client.post(
        "/agent/query",
        headers=FINANCE_EDITOR_HEADERS,
        json={"question": "delete the revenue dataset"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tool_calls"][-1] == {"tool": "delete_dataset", "arguments": {"dataset_id": 1}}
    assert "blocked" in body["answer"]
    assert body["raw_results"]["2_delete_dataset"]["status_code"] == 403


def test_agent_database_delete_prompt_is_refused_without_tool_call(client: TestClient) -> None:
    response = client.post(
        "/agent/query",
        headers=FINANCE_EDITOR_HEADERS,
        json={"question": "delete the database"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tool_calls"] == []
    assert "cannot delete the database" in body["answer"]


def test_structured_task_finds_datasets_with_schema(client: TestClient) -> None:
    response = client.post(
        "/agent/tasks",
        headers=SERVICE_HEADERS,
        json={
            "task": "find_datasets",
            "filters": {"owner_team": "finance", "keyword": "revenue"},
            "include": ["schema"],
            "reason": "Build a finance metadata dashboard.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert [dataset["name"] for dataset in body["datasets"]] == ["revenue_transactions", "revenue_forecast"]
    assert body["tool_calls"][0] == {
        "tool": "search_datasets",
        "arguments": {"owner_team": "finance", "keyword": "revenue"},
    }
    assert body["tool_calls"][1] == {"tool": "get_schema", "arguments": {"dataset_id": 1}}
    assert body["tool_calls"][2] == {"tool": "get_schema", "arguments": {"dataset_id": 2}}
    assert set(body["schemas_by_dataset_id"]) == {"1", "2"}
    assert body["errors"] == []


def test_structured_task_applies_policy_filtering(client: TestClient) -> None:
    response = client.post(
        "/agent/tasks",
        headers=ANALYTICS_VIEWER_HEADERS,
        json={
            "task": "find_datasets",
            "filters": {"owner_team": "finance"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert [dataset["name"] for dataset in body["datasets"]] == ["revenue_forecast"]


def test_structured_task_can_create_allowed_dataset(client: TestClient) -> None:
    response = client.post(
        "/agent/tasks",
        headers=FINANCE_EDITOR_HEADERS,
        json={
            "task": "create_dataset",
            "dataset": {
                "name": "finance_public_metrics",
                "description": "Published finance metrics.",
                "owner_team": "finance",
                "data_source": "metrics_store",
                "sensitivity_level": "internal",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["datasets"][0]["name"] == "finance_public_metrics"
    assert body["tool_calls"] == [
        {
            "tool": "create_dataset",
            "arguments": {
                "name": "finance_public_metrics",
                "description": "Published finance metrics.",
                "owner_team": "finance",
                "data_source": "metrics_store",
                "sensitivity_level": "internal",
            },
        }
    ]


def test_structured_task_blocks_unauthorized_delete(client: TestClient) -> None:
    response = client.post(
        "/agent/tasks",
        headers=FINANCE_EDITOR_HEADERS,
        json={"task": "delete_dataset", "dataset_id": 1, "confirm_dangerous_action": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["tool_calls"] == [{"tool": "delete_dataset", "arguments": {"dataset_id": 1}}]
    assert body["errors"][0]["status_code"] == 403
    assert "admin or service role" in body["errors"][0]["message"]


def test_structured_task_allows_confirmed_admin_delete(client: TestClient) -> None:
    response = client.post(
        "/agent/tasks",
        headers=ADMIN_HEADERS,
        json={"task": "delete_dataset", "dataset_id": 8, "confirm_dangerous_action": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["tool_calls"] == [{"tool": "delete_dataset", "arguments": {"dataset_id": 8}}]
    assert body["raw_results"]["1_delete_dataset"] == {"deleted": True, "dataset_id": 8}


def test_structured_task_validates_required_fields(client: TestClient) -> None:
    response = client.post(
        "/agent/tasks",
        headers=SERVICE_HEADERS,
        json={"task": "get_dataset"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["errors"][0] == {
        "tool": "get_dataset",
        "status_code": 400,
        "message": "dataset_id is required",
    }
