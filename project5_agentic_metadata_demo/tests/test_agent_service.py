from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from project5_agentic_metadata_demo.app import main
from project5_agentic_metadata_demo.app.database import Base, get_db
from project5_agentic_metadata_demo.app.seed import seed_database


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

