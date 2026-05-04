from __future__ import annotations

from project4_agentic_project_copilot.app.llm import LlmClient
from project4_agentic_project_copilot.app.models import (
    FileAnswer,
    OrchestratorDecision,
    SessionContext,
    SqlPlan,
    ToolCall,
)


class CopilotOrchestrator:
    def __init__(self, llm_client: LlmClient) -> None:
        self.llm_client = llm_client

    async def decide(self, message: str, context: SessionContext) -> OrchestratorDecision:
        return await self.llm_client.parse(
            task_name="copilot_orchestrator",
            system_prompt=(
                "You route a personal productivity copilot request to one path: context, "
                "file_retrieval, sql_query, api_tool, or clarify. Use file_retrieval for "
                "uploaded file or note-content questions, sql_query for read-only structured "
                "database questions, api_tool for task or personal-note actions, context for "
                "session/workflow-state questions, and clarify when the request is ambiguous. "
                "Return only OrchestratorDecision."
            ),
            user_payload={"message": message, "context": context.model_dump(mode="json")},
            response_model=OrchestratorDecision,
        )


class SqlAgent:
    def __init__(self, llm_client: LlmClient) -> None:
        self.llm_client = llm_client

    async def plan(self, message: str, schema: str, context: SessionContext) -> SqlPlan:
        return await self.llm_client.parse(
            task_name="sql_agent",
            system_prompt=(
                "Generate one read-only SQLite SELECT query for the user's question. "
                "Use only the provided schema. Do not generate INSERT, UPDATE, DELETE, "
                "DROP, ALTER, PRAGMA, or other write/admin SQL. Return only SqlPlan."
            ),
            user_payload={
                "message": message,
                "schema": schema,
                "context": context.model_dump(mode="json"),
            },
            response_model=SqlPlan,
        )


class ToolAgent:
    def __init__(self, llm_client: LlmClient) -> None:
        self.llm_client = llm_client

    async def propose(self, message: str, decision: OrchestratorDecision, context: SessionContext) -> ToolCall:
        return await self.llm_client.parse(
            task_name="tool_agent",
            system_prompt=(
                "Propose one productivity tool call. State-changing tools require confirmation. "
                "Use create_task, update_task_status, assign_task, add_comment, search_tasks, "
                "create_note, or search_notes. Return only ToolCall."
            ),
            user_payload={
                "message": message,
                "tool_name": decision.tool_name,
                "tool_args": decision.tool_args.clean(),
                "context": context.model_dump(mode="json"),
            },
            response_model=ToolCall,
        )


class FileQaAgent:
    def __init__(self, llm_client: LlmClient) -> None:
        self.llm_client = llm_client

    async def answer(self, message: str, chunks: list[dict], context: SessionContext) -> FileAnswer:
        return await self.llm_client.parse(
            task_name="file_qa_agent",
            system_prompt=(
                "Answer using only retrieved file chunks. Cite chunk ids that support the answer. "
                "If the chunks do not answer the question, say that the uploaded files do not contain enough evidence."
            ),
            user_payload={
                "message": message,
                "chunks": chunks,
                "context": context.model_dump(mode="json"),
            },
            response_model=FileAnswer,
        )
