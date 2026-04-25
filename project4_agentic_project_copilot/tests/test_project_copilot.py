import asyncio
from pathlib import Path

from openai.lib._pydantic import to_strict_json_schema

from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.embeddings import HashEmbeddingClient
from project4_agentic_project_copilot.app.llm import RuleBasedLlmClient
from project4_agentic_project_copilot.app.models import ChatRequest, FileAnswer, OrchestratorDecision, SqlPlan, ToolCall
from project4_agentic_project_copilot.app.service import ProjectCopilotService
from project4_agentic_project_copilot.app.session_store import JsonSessionStore
from project4_agentic_project_copilot.app.tools import ProjectToolService
from project4_agentic_project_copilot.app.trace_store import JsonlTraceStore


def run(coro):
    return asyncio.run(coro)


def service(tmp_path: Path) -> ProjectCopilotService:
    db = CopilotDatabase(tmp_path / "copilot.sqlite3")
    return ProjectCopilotService(
        db=db,
        llm_client=RuleBasedLlmClient(),
        embedding_client=HashEmbeddingClient(),
        session_store=JsonSessionStore(tmp_path / "sessions.json"),
        trace_store=JsonlTraceStore(tmp_path / "traces.jsonl"),
    )


def test_live_structured_output_schemas_do_not_use_open_dicts() -> None:
    for model in (OrchestratorDecision, ToolCall, SqlPlan, FileAnswer):
        schema = to_strict_json_schema(model)
        assert not _contains_open_dict(schema)


def test_greeting_returns_without_model_call(tmp_path: Path) -> None:
    copilot = ProjectCopilotService(
        db=CopilotDatabase(tmp_path / "copilot.sqlite3"),
        llm_client=FailingLlmClient(),
        embedding_client=HashEmbeddingClient(),
        session_store=JsonSessionStore(tmp_path / "sessions.json"),
        trace_store=JsonlTraceStore(tmp_path / "traces.jsonl"),
    )

    response = run(copilot.chat(ChatRequest(session_id="hello", message="hello")))

    assert response.route == "clarify"
    assert "Upload a file" in response.response


def test_file_question_without_upload_returns_clarification_without_model_call(tmp_path: Path) -> None:
    copilot = ProjectCopilotService(
        db=CopilotDatabase(tmp_path / "copilot.sqlite3"),
        llm_client=FailingLlmClient(),
        embedding_client=HashEmbeddingClient(),
        session_store=JsonSessionStore(tmp_path / "sessions.json"),
        trace_store=JsonlTraceStore(tmp_path / "traces.jsonl"),
    )

    response = run(
        copilot.chat(
            ChatRequest(
                session_id="no-file",
                message="hi, can yuo tell me what this file is doing?",
            )
        )
    )

    assert response.route == "clarify"
    assert "Upload a markdown" in response.response


def test_model_failure_returns_graceful_chat_response(tmp_path: Path) -> None:
    copilot = ProjectCopilotService(
        db=CopilotDatabase(tmp_path / "copilot.sqlite3"),
        llm_client=FailingLlmClient(),
        embedding_client=HashEmbeddingClient(),
        session_store=JsonSessionStore(tmp_path / "sessions.json"),
        trace_store=JsonlTraceStore(tmp_path / "traces.jsonl"),
    )

    response = run(copilot.chat(ChatRequest(session_id="model-error", message="How many open tasks are in the database?")))

    assert response.route == "clarify"
    assert "could not complete the model call" in response.response
    assert "Model/API call failed" in response.decision_log.reasoning


def test_upload_sets_current_document_for_session(tmp_path: Path) -> None:
    copilot = service(tmp_path)
    upload = run(
        copilot.upload_file(
            filename="current_file.md",
            content_type="text/markdown",
            content=b"# Current File\nThe current file explains the upload-session binding.",
            session_id="current-file",
        )
    )

    assert upload.context is not None
    assert upload.context.current_document_id == upload.document_id
    assert upload.context.current_document_filename == "current_file.md"

    response = run(
        copilot.chat(
            ChatRequest(
                session_id="current-file",
                message="What is this file doing?",
            )
        )
    )

    assert response.route == "file_retrieval"
    assert response.context.current_document_id == upload.document_id
    assert response.context.current_document_filename == "current_file.md"
    assert response.citations
    assert response.citations[0].document_id == upload.document_id


def test_upload_after_file_question_allows_how_about_now_followup(tmp_path: Path) -> None:
    copilot = service(tmp_path)
    before_upload = run(
        copilot.chat(
            ChatRequest(
                session_id="issue-12",
                message="can yuo say something about the file",
            )
        )
    )

    upload = run(
        copilot.upload_file(
            filename="issue_12_brief.md",
            content_type="text/markdown",
            content=b"# Issue 12 Brief\nThe uploaded file describes the copilot follow-up repair.",
            session_id="issue-12",
        )
    )

    after_upload = run(
        copilot.chat(
            ChatRequest(
                session_id="issue-12",
                message="how about now",
            )
        )
    )

    assert before_upload.route == "clarify"
    assert upload.context is not None
    assert upload.context.current_document_id == upload.document_id
    assert upload.context.current_document_filename == "issue_12_brief.md"
    assert after_upload.route == "file_retrieval"
    assert after_upload.context.current_document_id == upload.document_id
    assert after_upload.context.current_document_filename == "issue_12_brief.md"
    assert after_upload.citations
    assert after_upload.citations[0].document_id == upload.document_id


def test_concurrent_uploads_keep_session_contexts_isolated(tmp_path: Path) -> None:
    db = CopilotDatabase(tmp_path / "copilot.sqlite3")
    copilot = ProjectCopilotService(
        db=db,
        llm_client=RuleBasedLlmClient(),
        embedding_client=SlowHashEmbeddingClient(),
        session_store=JsonSessionStore(tmp_path / "sessions.json"),
        trace_store=JsonlTraceStore(tmp_path / "traces.jsonl"),
    )

    async def upload_all():
        return await asyncio.gather(
            *[
                copilot.upload_file(
                    filename=f"concurrent_{index}.md",
                    content_type="text/markdown",
                    content=f"# Concurrent {index}\nUpload {index} should not lock the database.".encode(),
                    session_id=f"session-{index}",
                )
                for index in range(6)
            ]
        )

    uploads = run(upload_all())

    assert len(uploads) == 6
    for index, upload in enumerate(uploads):
        assert upload.chunk_count == 1
        assert upload.context is not None
        assert upload.context.session_id == f"session-{index}"
        assert upload.context.current_document_id == upload.document_id
        assert upload.context.current_document_filename == f"concurrent_{index}.md"


def test_file_retrieval_does_not_cross_session_boundaries(tmp_path: Path) -> None:
    copilot = service(tmp_path)
    upload = run(
        copilot.upload_file(
            filename="private_brief.md",
            content_type="text/markdown",
            content=b"# Private Brief\nOnly session A should be able to retrieve this file.",
            session_id="session-a",
        )
    )

    response = run(
        copilot.chat(
            ChatRequest(
                session_id="session-b",
                message="What does the private brief say?",
            )
        )
    )

    assert upload.chunk_count == 1
    assert response.route == "clarify"
    assert response.citations == []
    assert response.context.current_document_id is None
    assert "file selected for this session" in response.response


def test_file_upload_retrieval_answers_with_citations(tmp_path: Path) -> None:
    copilot = service(tmp_path)
    upload = run(
        copilot.upload_file(
            filename="launch_brief.md",
            content_type="text/markdown",
            content=b"# Launch Brief\nThe rollout checklist needs QA owner and API contract review.",
            session_id="files",
        )
    )

    response = run(
        copilot.chat(
            ChatRequest(
                session_id="files",
                message="What does the launch brief say about rollout work?",
            )
        )
    )

    assert upload.chunk_count == 1
    assert response.route == "file_retrieval"
    assert response.citations
    assert response.context.current_document_id == upload.document_id
    assert response.context.current_document_filename == "launch_brief.md"
    assert response.citations[0].filename == "launch_brief.md"


def test_sql_question_generates_valid_read_only_sql(tmp_path: Path) -> None:
    response = run(
        service(tmp_path).chat(
            ChatRequest(
                session_id="sql",
                message="How many open tasks are in the database?",
            )
        )
    )

    assert response.route == "sql_query"
    assert response.generated_sql == "SELECT COUNT(*) AS open_task_count FROM tasks WHERE status = 'open'"
    assert response.sql_result is not None
    assert response.sql_result.rows == [{"open_task_count": 2}]
    assert "open_task_count" in response.response


def test_service_refuses_destructive_generated_sql(tmp_path: Path) -> None:
    class BadSqlLlm(RuleBasedLlmClient):
        async def parse(self, *, task_name, system_prompt, user_payload, response_model):
            if response_model is SqlPlan:
                return SqlPlan(sql="DELETE FROM tasks", explanation="bad")
            return await super().parse(
                task_name=task_name,
                system_prompt=system_prompt,
                user_payload=user_payload,
                response_model=response_model,
            )

    db = CopilotDatabase(tmp_path / "copilot.sqlite3")
    copilot = ProjectCopilotService(
        db=db,
        llm_client=BadSqlLlm(),
        embedding_client=HashEmbeddingClient(),
        session_store=JsonSessionStore(tmp_path / "sessions.json"),
        trace_store=JsonlTraceStore(tmp_path / "traces.jsonl"),
    )

    response = run(
        copilot.chat(
            ChatRequest(
                session_id="bad-sql",
                message="How many open tasks are in the database?",
            )
        )
    )

    assert response.route == "sql_query"
    assert response.sql_result is None
    assert response.generated_sql == "DELETE FROM tasks"
    assert "refused" in response.response


def test_create_task_requires_confirmation_then_executes(tmp_path: Path) -> None:
    copilot = service(tmp_path)
    proposed = run(
        copilot.chat(
            ChatRequest(
                session_id="tool",
                message="Create task follow up with QA in project 1",
            )
        )
    )

    assert proposed.route == "api_tool"
    assert proposed.pending_action is not None
    assert proposed.tool_result is None

    confirmed = run(
        copilot.chat(
            ChatRequest(
                session_id="tool",
                message="Confirm action",
                confirm_action_id=proposed.pending_action.action_id,
            )
        )
    )

    assert confirmed.tool_result is not None
    assert confirmed.tool_result.ok is True
    assert confirmed.context.current_task_id is not None
    rows = copilot.db.execute_read("SELECT title FROM tasks WHERE task_id = ?", (confirmed.context.current_task_id,))
    assert rows[0]["title"] == "follow up with QA in project 1"


def test_api_tools_reject_invalid_foreign_keys(tmp_path: Path) -> None:
    tools = ProjectToolService(CopilotDatabase(tmp_path / "copilot.sqlite3"))

    create_result = tools.execute(
        tool_call=ToolCall(
            name="create_task",
            args={"project_id": 999, "title": "bad"},
            requires_confirmation=True,
            reason="test",
        )
    )
    comment_result = tools.execute(
        tool_call=ToolCall(
            name="add_comment",
            args={"task_id": 999, "user_id": 999, "body": "bad"},
            requires_confirmation=True,
            reason="test",
        )
    )

    assert create_result.ok is False
    assert "does not exist" in (create_result.error or "")
    assert comment_result.ok is False
    assert "does not exist" in (comment_result.error or "")


def test_trace_log_records_orchestration_decision(tmp_path: Path) -> None:
    copilot = service(tmp_path)
    run(copilot.chat(ChatRequest(session_id="trace", message="How many open tasks are in the database?")))

    trace_path = tmp_path / "traces.jsonl"
    raw = trace_path.read_text()

    assert '"route":"sql_query"' in raw
    assert '"has_sql":true' in raw


class FailingLlmClient:
    async def parse(self, *, task_name, system_prompt, user_payload, response_model):
        raise RuntimeError("model unavailable")


class SlowHashEmbeddingClient(HashEmbeddingClient):
    async def embed(self, text: str) -> list[float]:
        await asyncio.sleep(0.02)
        return await super().embed(text)


def _contains_open_dict(value) -> bool:
    if isinstance(value, dict):
        if value.get("type") == "object" and value.get("additionalProperties") is True:
            return True
        return any(_contains_open_dict(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_open_dict(item) for item in value)
    return False
