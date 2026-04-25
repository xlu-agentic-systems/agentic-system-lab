from fastapi.testclient import TestClient

from project4_agentic_project_copilot.app import main
from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.embeddings import HashEmbeddingClient
from project4_agentic_project_copilot.app.llm import RuleBasedLlmClient
from project4_agentic_project_copilot.app.service import ProjectCopilotService
from project4_agentic_project_copilot.app.session_store import JsonSessionStore
from project4_agentic_project_copilot.app.trace_store import JsonlTraceStore


def test_http_app_serves_ui_and_chat_with_local_test_service(tmp_path, monkeypatch) -> None:
    test_service = ProjectCopilotService(
        db=CopilotDatabase(tmp_path / "copilot.sqlite3"),
        llm_client=RuleBasedLlmClient(),
        embedding_client=HashEmbeddingClient(),
        session_store=JsonSessionStore(tmp_path / "sessions.json"),
        trace_store=JsonlTraceStore(tmp_path / "traces.jsonl"),
    )
    monkeypatch.setattr(main, "service", test_service)
    client = TestClient(main.app)

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200

    upload = client.post(
        "/upload",
        data={"session_id": "http"},
        files={"file": ("brief.md", b"The launch brief mentions API contract review.", "text/markdown")},
    )
    assert upload.status_code == 200
    assert upload.json()["chunk_count"] == 1

    chat = client.post(
        "/chat",
        json={"session_id": "http", "message": "What does the brief say about API contract review?"},
    )
    assert chat.status_code == 200
    body = chat.json()
    assert body["route"] == "file_retrieval"
    assert body["citations"]
