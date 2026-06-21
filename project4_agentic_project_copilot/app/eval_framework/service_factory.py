from __future__ import annotations

import tempfile
from pathlib import Path

from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.embeddings import HashEmbeddingClient
from project4_agentic_project_copilot.app.embeddings import EmbeddingClient
from project4_agentic_project_copilot.app.llm import LlmClient, RuleBasedLlmClient
from project4_agentic_project_copilot.app.service import ProjectCopilotService
from project4_agentic_project_copilot.app.session_store import JsonSessionStore
from project4_agentic_project_copilot.app.trace_store import JsonlTraceStore


def create_evaluation_service(
    *,
    temp_prefix: str = "project4-eval-",
    llm_client: LlmClient | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> ProjectCopilotService:
    root = Path(tempfile.mkdtemp(prefix=temp_prefix))
    return create_evaluation_service_at(root, llm_client=llm_client, embedding_client=embedding_client)


def create_evaluation_service_at(
    root: Path,
    *,
    llm_client: LlmClient | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> ProjectCopilotService:
    root.mkdir(parents=True, exist_ok=True)
    return ProjectCopilotService(
        db=CopilotDatabase(root / "copilot.sqlite3"),
        llm_client=llm_client or RuleBasedLlmClient(),
        embedding_client=embedding_client or HashEmbeddingClient(),
        session_store=JsonSessionStore(root / "sessions.json"),
        trace_store=JsonlTraceStore(root / "traces.jsonl"),
    )
