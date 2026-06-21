from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable

from project4_agentic_project_copilot.app.agents import ToolAgent
from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.models import (
    ChatRequest,
    ChatResponse,
    DecisionLog,
    OrchestratorDecision,
    PendingAction,
    SessionContext,
    ToolCall,
    ToolResult,
)
from project4_agentic_project_copilot.app.response_utils import build_chat_response
from project4_agentic_project_copilot.app.tools import ProjectToolService, WRITE_TOOLS


EventLogger = Callable[..., None]


class WorkflowActionService:
    def __init__(
        self,
        *,
        db: CopilotDatabase,
        tool_agent: ToolAgent,
        tools: ProjectToolService,
        event_logger: EventLogger,
        logger: logging.Logger,
    ) -> None:
        self.db = db
        self.tool_agent = tool_agent
        self.tools = tools
        self.event_logger = event_logger
        self.logger = logger

    async def handle_tool(
        self,
        request: ChatRequest,
        context: SessionContext,
        decision: OrchestratorDecision,
    ) -> ChatResponse:
        tool_call = await self.tool_agent.propose(request.message, decision, context)
        if tool_call.name in WRITE_TOOLS or tool_call.requires_confirmation:
            action_id = str(uuid.uuid4())
            pending = PendingAction(
                action_id=action_id,
                tool_call=tool_call,
                preview=self.tools.preview(tool_call),
            )
            context.pending_actions[action_id] = tool_call
            workflow_id = self._create_workflow_state(
                context=context,
                action_id=action_id,
                tool_call=tool_call,
                preview=pending.preview,
            )
            self.event_logger(
                self.logger,
                event="tool_execution",
                message="project4 project API tool requires confirmation",
                agent="tool_agent",
                session_id=request.session_id,
                tool_name=tool_call.name,
                status="requires_confirmation",
                attributes={"requires_confirmation": tool_call.requires_confirmation, "workflow_id": workflow_id},
            )
            return build_chat_response(
                request,
                context,
                response=f"I can do that, but need confirmation first. Proposed action: {pending.preview}",
                route="api_tool",
                tool_call=tool_call,
                pending_action=pending,
                log=DecisionLog(
                    route="api_tool",
                    reasoning=decision.reasoning,
                    selected_data_source="project_api",
                    tool_name=tool_call.name,
                    workflow_id=workflow_id,
                ),
            )

        result = self.tools.execute(tool_call)
        self.event_logger(
            self.logger,
            event="tool_execution",
            message="project4 project API tool execution result",
            agent="tool_agent",
            session_id=request.session_id,
            tool_name=tool_call.name,
            status="executed" if result.ok else "failed",
            attributes={"ok": result.ok, "error": result.error, "requires_confirmation": tool_call.requires_confirmation},
        )
        self._update_context_from_tool(context, result)
        return build_chat_response(
            request,
            context,
            response=_explain_tool_result(result),
            route="api_tool",
            tool_call=tool_call,
            tool_result=result,
            log=DecisionLog(
                route="api_tool",
                reasoning=decision.reasoning,
                selected_data_source="project_api",
                tool_name=tool_call.name,
            ),
        )

    async def confirm_action(self, request: ChatRequest, context: SessionContext) -> ChatResponse:
        action_id = request.confirm_action_id or ""
        tool_call = context.pending_actions.pop(action_id, None)
        if tool_call is None:
            result = ToolResult(name="search_tasks", ok=False, error="No pending action found for that confirmation id.")
            self.event_logger(
                self.logger,
                event="tool_execution",
                message="project4 missing pending action confirmation",
                agent="tool_agent",
                session_id=request.session_id,
                tool_name=result.name,
                status="blocked",
                attributes={"ok": result.ok, "error": result.error},
            )
            return build_chat_response(
                request,
                context,
                response=result.error or "No pending action found.",
                route="api_tool",
                tool_result=result,
                log=DecisionLog(
                    route="api_tool",
                    reasoning="User attempted to confirm a missing pending action.",
                    selected_data_source="project_api",
                ),
            )

        result = self.tools.execute(tool_call)
        workflow_id = self._finish_workflow_state(context, action_id, result)
        self.event_logger(
            self.logger,
            event="tool_execution",
            message="project4 confirmed project API tool execution result",
            agent="tool_agent",
            session_id=request.session_id,
            tool_name=tool_call.name,
            status="executed" if result.ok else "failed",
            attributes={
                "ok": result.ok,
                "error": result.error,
                "requires_confirmation": tool_call.requires_confirmation,
                "workflow_id": workflow_id,
            },
        )
        self._update_context_from_tool(context, result)
        return build_chat_response(
            request,
            context,
            response=_explain_tool_result(result),
            route="api_tool",
            tool_call=tool_call,
            tool_result=result,
            log=DecisionLog(
                route="api_tool",
                reasoning="User confirmed a pending state-changing API action.",
                selected_data_source="project_api",
                tool_name=tool_call.name,
                workflow_id=workflow_id,
            ),
        )

    def _create_workflow_state(
        self,
        *,
        context: SessionContext,
        action_id: str,
        tool_call: ToolCall,
        preview: str,
    ) -> str:
        workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
        workflow_type = _workflow_type(tool_call)
        args = tool_call.args.clean()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO productivity_workflows(
                  workflow_id,
                  session_id,
                  workflow_type,
                  status,
                  pending_action_id,
                  source_document_id,
                  source_note_id,
                  summary
                )
                VALUES (?, ?, ?, 'awaiting_review', ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    context.session_id,
                    workflow_type,
                    action_id,
                    args.get("source_document_id") or context.current_document_id,
                    args.get("note_id") or context.current_note_id,
                    preview,
                ),
            )
            conn.execute(
                """
                INSERT INTO workflow_steps(workflow_id, step_index, name, status, output_json)
                VALUES (?, 1, 'proposed_tool_action', 'awaiting_review', ?)
                """,
                (
                    workflow_id,
                    json.dumps(
                        {
                            "tool_name": tool_call.name,
                            "args": args,
                            "preview": preview,
                        },
                        sort_keys=True,
                    ),
                ),
            )
            conn.commit()
        context.current_workflow_id = workflow_id
        return workflow_id

    def _finish_workflow_state(self, context: SessionContext, action_id: str, result: ToolResult) -> str | None:
        rows = self.db.execute_read(
            """
            SELECT workflow_id
            FROM productivity_workflows
            WHERE session_id = ? AND pending_action_id = ? AND status = 'awaiting_review'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (context.session_id, action_id),
        )
        if not rows:
            return None
        workflow_id = rows[0]["workflow_id"]
        status = "completed" if result.ok else "failed"
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE productivity_workflows
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE workflow_id = ?
                """,
                (status, workflow_id),
            )
            conn.execute(
                """
                INSERT INTO workflow_steps(workflow_id, step_index, name, status, output_json)
                VALUES (?, 2, 'confirmed_tool_execution', ?, ?)
                """,
                (
                    workflow_id,
                    status,
                    result.model_dump_json(),
                ),
            )
            conn.commit()
        context.current_workflow_id = workflow_id
        return workflow_id

    def _update_context_from_tool(self, context: SessionContext, result: ToolResult) -> None:
        if not result.ok or not isinstance(result.result, dict):
            return
        if "project_id" in result.result:
            context.current_project_id = int(result.result["project_id"])
        if "task_id" in result.result:
            context.current_task_id = int(result.result["task_id"])
        if "note_id" in result.result:
            context.current_note_id = int(result.result["note_id"])


def _workflow_type(tool_call: ToolCall) -> str:
    if tool_call.name == "create_note":
        return "capture_personal_note"
    if tool_call.name == "create_task" and (
        tool_call.args.source_document_id or tool_call.args.note_id or tool_call.args.source_filename
    ):
        return "document_or_note_to_task"
    return f"{tool_call.name}_workflow"


def _explain_tool_result(result: ToolResult) -> str:
    if not result.ok:
        return f"The tool call failed: {result.error}"
    return f"Tool {result.name} executed successfully: {result.result}"
