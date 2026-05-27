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
    assert "message-citations" in index.text
    assert "message-meta" in index.text
    assert 'addMessage("assistant", data.response, responseCitations, data.response_timing)' in index.text
    debug = client.get("/debug")
    assert debug.status_code == 200
    assert debug.headers["cache-control"] == "no-store, max-age=0"

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
    assert body["trace_id"]
    assert body["response_timing"]["elapsed_ms"] >= 0
    assert body["response_timing"]["note"].startswith("Processed in ")
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

    traces = client.get("/debug/traces")
    assert traces.status_code == 200
    trace_items = traces.json()["traces"]
    assert trace_items
    assert trace_items[0]["trace_id"] == body["trace_id"]
    assert trace_items[0]["span_count"] >= 4

    trace_detail = client.get(f"/debug/traces/{body['trace_id']}")
    assert trace_detail.status_code == 200
    detail_body = trace_detail.json()
    assert detail_body["trace_id"] == body["trace_id"]
    event_types = [span["event_type"] for span in detail_body["spans"]]
    assert event_types[:2] == ["user_message", "route_decision"]
    assert "retrieval" in event_types
    assert "final_response" in event_types

    deleted = client.delete(f"/documents/{upload_body['document_id']}", params={"session_id": "http"})
    assert deleted.status_code == 200
    deleted_body = deleted.json()
    assert deleted_body["deleted"] is True
    assert deleted_body["context"]["current_document_id"] is None
    assert deleted_body["context"]["selected_documents"] == []
    assert [document["document_id"] for document in client.get("/documents").json()["documents"]] == [
        second_body["document_id"]
    ]
