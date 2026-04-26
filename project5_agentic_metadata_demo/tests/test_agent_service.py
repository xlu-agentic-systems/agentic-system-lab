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
