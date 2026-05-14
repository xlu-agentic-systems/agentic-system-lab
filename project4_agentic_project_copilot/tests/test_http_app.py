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
    index = client.get("/")
    assert index.status_code == 200
    assert index.headers["cache-control"] == "no-store, max-age=0"

    upload = client.post(
        "/upload",
        data={"session_id": "http"},
        files={"file": ("brief.md", b"The launch brief mentions API contract review.", "text/markdown")},
    )
    assert upload.status_code == 200
    upload_body = upload.json()
    assert upload_body["chunk_count"] == 1
    assert upload_body["context"]["current_document_id"] == upload_body["document_id"]
    assert upload_body["context"]["current_document_filename"] == "brief.md"
    assert upload_body["context"]["selected_documents"] == [
        {"document_id": upload_body["document_id"], "filename": "brief.md"}
    ]
    assert upload_body["context"]["retrieval_scope"] == "current"
    assert upload_body["reindexed_chunk_count"] == 1

    second_upload = client.post(
        "/upload",
        data={"session_id": "other"},
        files={"file": ("roadmap.md", b"The roadmap mentions migration rehearsal.", "text/markdown")},
    )
    assert second_upload.status_code == 200
    second_body = second_upload.json()

    listed = client.get("/documents")
    assert listed.status_code == 200
    listed_body = listed.json()
    assert {document["filename"] for document in listed_body["documents"]} == {"brief.md", "roadmap.md"}

    selected = client.post(
        f"/documents/{upload_body['document_id']}/select",
        data={"session_id": "http-selected"},
    )
    assert selected.status_code == 200
    assert selected.json()["context"]["current_document_filename"] == "brief.md"

    attached = client.post(
        f"/documents/{second_body['document_id']}/attach",
        data={"session_id": "http"},
    )
    assert attached.status_code == 200
    assert {document["document_id"] for document in attached.json()["context"]["selected_documents"]} == {
        upload_body["document_id"],
        second_body["document_id"],
    }

    scoped = client.post(
        "/documents/scope",
        data={"session_id": "http", "retrieval_scope": "selected"},
    )
    assert scoped.status_code == 200
    assert scoped.json()["context"]["retrieval_scope"] == "selected"

    chat = client.post(
        "/chat",
        json={"session_id": "http", "message": "What do the selected documents say?"},
    )
    assert chat.status_code == 200
    body = chat.json()
    assert body["route"] == "file_retrieval"
    assert body["citations"]
    assert body["decision_log"]["retrieval_scope"] == "selected"
    assert {document["document_id"] for document in body["decision_log"]["searched_documents"]} == {
        upload_body["document_id"],
        second_body["document_id"],
    }

    detached = client.post(
        f"/documents/{second_body['document_id']}/detach",
        data={"session_id": "http"},
    )
    assert detached.status_code == 200
    assert detached.json()["detached"] is True
    assert [document["document_id"] for document in detached.json()["context"]["selected_documents"]] == [
        upload_body["document_id"]
    ]

    deleted = client.delete(f"/documents/{upload_body['document_id']}", params={"session_id": "http"})
    assert deleted.status_code == 200
    deleted_body = deleted.json()
    assert deleted_body["deleted"] is True
    assert deleted_body["context"]["current_document_id"] is None
    assert deleted_body["context"]["selected_documents"] == []
    assert [document["document_id"] for document in client.get("/documents").json()["documents"]] == [
        second_body["document_id"]
    ]
