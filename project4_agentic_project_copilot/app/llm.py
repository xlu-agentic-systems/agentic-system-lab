from __future__ import annotations

import json
import os
import re
from typing import Protocol, TypeVar

from pydantic import BaseModel

from project4_agentic_project_copilot.app.models import FileAnswer, OrchestratorDecision, SqlPlan, ToolCall


T = TypeVar("T", bound=BaseModel)


class LlmClient(Protocol):
    async def parse(
        self,
        *,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_model: type[T],
    ) -> T:
        ...


class OpenAILlmClient:
    def __init__(self, *, model: str | None = None, timeout_seconds: float | None = None) -> None:
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5.5")
        self.timeout_seconds = timeout_seconds or float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
        self._client = None

    async def parse(
        self,
        *,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_model: type[T],
    ) -> T:
        client = self._get_client()
        response = await client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Task: {task_name}\n\n{json.dumps(user_payload, default=str)}",
                },
            ],
            text_format=response_model,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is not None:
            return parsed if isinstance(parsed, response_model) else response_model.model_validate(parsed)
        for output in getattr(response, "output", []):
            for item in getattr(output, "content", []):
                candidate = getattr(item, "parsed", None)
                if candidate is not None:
                    return candidate if isinstance(candidate, response_model) else response_model.model_validate(candidate)
        raise RuntimeError("OpenAI response did not include parsed structured output.")

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "The openai package is required for LLM calls. Install dependencies with "
                    '`pip install -e ".[dev]"`.'
                ) from exc
            self._client = AsyncOpenAI(timeout=self.timeout_seconds)
        return self._client


class RuleBasedLlmClient:
    """Deterministic project-copilot LLM substitute for tests and local demos."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def parse(
        self,
        *,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_model: type[T],
    ) -> T:
        self.calls.append(task_name)
        if response_model is OrchestratorDecision:
            return response_model.model_validate(_decision(user_payload))
        if response_model is SqlPlan:
            return response_model.model_validate(_sql_plan(user_payload))
        if response_model is ToolCall:
            return response_model.model_validate(_tool_call(user_payload))
        if response_model is FileAnswer:
            return response_model.model_validate(_file_answer(user_payload))
        raise NotImplementedError(f"RuleBasedLlmClient does not support {response_model.__name__}")


def _decision(payload: dict) -> dict:
    message = str(payload.get("message", ""))
    text = message.lower()
    context = payload.get("context") or {}
    if re.fullmatch(r"\s*(yes|confirm|approve|do it)\s*", text):
        return {"route": "context", "reasoning": "Confirmation is handled by the service layer."}
    if any(term in text for term in ("delete from", "drop table", "insert into", "update tasks")):
        return {"route": "sql_query", "reasoning": "The user is asking for database SQL, so the SQL safety layer must inspect it."}
    if any(term in text for term in ("search notes", "find notes")):
        return {
            "route": "api_tool",
            "reasoning": "The user wants to search personal notes through the read-only productivity API.",
            "tool_name": "search_notes",
        }
    if any(term in text for term in ("save this as a note", "create note", "capture note", "personal note")):
        return {
            "route": "api_tool",
            "reasoning": "The user wants to capture a personal note, which is a reviewable write.",
            "tool_name": "create_note",
        }
    if ("follow-up task" in text or "follow up task" in text or "turn this note" in text or "turn this document" in text) and any(
        term in text for term in ("note", "document", "file", "follow")
    ):
        return {
            "route": "api_tool",
            "reasoning": "The user wants to convert personal context into a task, which is a reviewable workflow.",
            "tool_name": "create_task",
        }
    if any(term in text for term in ("search tasks", "find tasks")):
        return {
            "route": "api_tool",
            "reasoning": "The user wants to search tasks through the read-only project API.",
            "tool_name": "search_tasks",
        }
    if any(term in text for term in ("upload", "file", "document", "doc", "design", "requirements", "brief")):
        return {"route": "file_retrieval", "reasoning": "The user is asking about uploaded project files.", "search_query": message}
    if any(term in text for term in ("how many", "list", "show", "which", "count", "status", "assigned", "open tasks", "blocked")):
        return {"route": "sql_query", "reasoning": "The user is asking for structured task database facts."}
    if any(term in text for term in ("create task", "add task", "new task")):
        return {
            "route": "api_tool",
            "reasoning": "The user wants to create a task, which is a state-changing API action.",
            "tool_name": "create_task",
        }
    if "assign" in text:
        return {
            "route": "api_tool",
            "reasoning": "The user wants to assign a task, which changes state.",
            "tool_name": "assign_task",
        }
    if any(term in text for term in ("mark", "update status", "move task", "set task")):
        return {
            "route": "api_tool",
            "reasoning": "The user wants to update task status, which changes state.",
            "tool_name": "update_task_status",
        }
    if "comment" in text or ("note" in text and "task" in text):
        return {
            "route": "api_tool",
            "reasoning": "The user wants to add a comment, which changes state.",
            "tool_name": "add_comment",
        }
    if any(term in text for term in ("current task", "current project", "what are we discussing")):
        return {"route": "context", "reasoning": "The user is asking about session context."}
    if context.get("current_project_id") or context.get("current_task_id"):
        return {"route": "context", "reasoning": "The existing session context is sufficient."}
    return {
        "route": "clarify",
        "reasoning": "The request does not clearly map to files, data, or tools.",
        "clarification_question": "Should I search uploaded files, query tasks, or perform a task action?",
    }


def _sql_plan(payload: dict) -> dict:
    message = str(payload.get("message", "")).lower()
    if "delete from" in message:
        sql = "DELETE FROM tasks"
    elif "drop table" in message:
        sql = "DROP TABLE tasks"
    elif "how many" in message and "open" in message:
        sql = "SELECT COUNT(*) AS open_task_count FROM tasks WHERE status = 'open'"
    elif "blocked" in message:
        sql = "SELECT task_id, title, status FROM tasks WHERE status = 'blocked' ORDER BY task_id"
    elif "alice" in message:
        sql = (
            "SELECT t.task_id, t.title, t.status FROM tasks t "
            "JOIN users u ON u.user_id = t.assignee_id WHERE u.name LIKE '%Alice%' ORDER BY t.task_id"
        )
    elif "project" in message and "task" in message:
        sql = (
            "SELECT p.name AS project, t.task_id, t.title, t.status FROM tasks t "
            "JOIN projects p ON p.project_id = t.project_id ORDER BY p.project_id, t.task_id"
        )
    else:
        sql = "SELECT task_id, title, status, assignee_id FROM tasks ORDER BY task_id"
    return {"sql": sql, "explanation": "Read-only SQL generated from the task database schema."}


def _tool_call(payload: dict) -> dict:
    message = str(payload.get("message", ""))
    text = message.lower()
    context = payload.get("context") or {}
    tool_name = payload.get("tool_name") or "search_tasks"
    if tool_name == "create_task":
        project_id = _number_after(text, "project") or context.get("current_project_id") or 1
        title = (
            _title_after(message, "create task")
            or _title_after(message, "add task")
            or _followup_task_title(message, context)
            or "New task"
        )
        return {
            "name": "create_task",
            "args": {
                "project_id": project_id,
                "title": title,
                "description": _task_description(message, context),
                "note_id": context.get("current_note_id"),
                "source_document_id": context.get("current_document_id"),
                "source_filename": context.get("current_document_filename"),
            },
            "requires_confirmation": True,
            "reason": "Creating a task changes the project database.",
        }
    if tool_name == "update_task_status":
        task_id = _number_after(text, "task") or context.get("current_task_id")
        status = next((candidate for candidate in ("open", "in_progress", "blocked", "done") if candidate in text), "done")
        return {
            "name": "update_task_status",
            "args": {"task_id": task_id, "status": status},
            "requires_confirmation": True,
            "reason": "Updating status changes task state.",
        }
    if tool_name == "assign_task":
        task_id = _number_after(text, "task") or context.get("current_task_id")
        user_id = _number_after(text, "user") or 1
        return {
            "name": "assign_task",
            "args": {"task_id": task_id, "user_id": user_id},
            "requires_confirmation": True,
            "reason": "Assigning a task changes task ownership.",
        }
    if tool_name == "add_comment":
        task_id = _number_after(text, "task") or context.get("current_task_id")
        body = _title_after(message, "comment") or _title_after(message, "note") or message
        return {
            "name": "add_comment",
            "args": {"task_id": task_id, "user_id": 1, "body": body},
            "requires_confirmation": True,
            "reason": "Adding a comment changes task records.",
        }
    if tool_name == "create_note":
        note = _note_parts(message, context)
        return {
            "name": "create_note",
            "args": {
                "title": note["title"],
                "body": note["body"],
                "source_document_id": context.get("current_document_id"),
                "source_filename": context.get("current_document_filename"),
            },
            "requires_confirmation": True,
            "reason": "Creating a personal note writes durable productivity memory.",
        }
    if tool_name == "search_notes":
        return {
            "name": "search_notes",
            "args": {"query": _search_query(message)},
            "requires_confirmation": False,
            "reason": "Searching personal notes is read-only.",
        }
    return {
        "name": "search_tasks",
        "args": {"query": _search_query(message)},
        "requires_confirmation": False,
        "reason": "Searching tasks is read-only.",
    }


def _file_answer(payload: dict) -> dict:
    chunks = payload.get("chunks") or []
    if not chunks:
        return {"answer": "I could not find relevant uploaded file content.", "cited_chunk_ids": []}
    first = chunks[0]
    return {
        "answer": f"Based on {first['filename']}, {first['text'][:280].strip()}",
        "cited_chunk_ids": [chunk["chunk_id"] for chunk in chunks[:2]],
    }


def _number_after(text: str, label: str) -> int | None:
    match = re.search(rf"{label}\D+(\d+)", text)
    return int(match.group(1)) if match else None


def _title_after(message: str, marker: str) -> str | None:
    index = message.lower().find(marker)
    if index < 0:
        return None
    title = message[index + len(marker) :].strip(" :-'\"")
    return title or None


def _note_parts(message: str, context: dict) -> dict[str, str]:
    body = (
        _title_after(message, "save this as a note")
        or _title_after(message, "create note")
        or _title_after(message, "capture note")
        or message
    )
    if body.lower() in {"from this document", "from this file"} and context.get("current_document_filename"):
        body = f"Note captured from {context['current_document_filename']}."
    cleaned = body.strip()
    words = cleaned.split()
    title = " ".join(words[:8]).strip(" .") or "Personal note"
    return {"title": title, "body": cleaned or title}


def _followup_task_title(message: str, context: dict) -> str | None:
    text = message.lower()
    if "turn this note" in text and context.get("current_note_id"):
        return f"Follow up on note {context['current_note_id']}"
    if "turn this document" in text or "turn this file" in text:
        if context.get("current_document_filename"):
            return f"Follow up on {context['current_document_filename']}"
    explicit = _title_after(message, "follow-up task") or _title_after(message, "follow up task")
    if explicit:
        return explicit
    if context.get("current_note_id"):
        return f"Follow up on note {context['current_note_id']}"
    if context.get("current_document_filename"):
        return f"Follow up on {context['current_document_filename']}"
    return None


def _task_description(message: str, context: dict) -> str:
    parts = [message]
    if context.get("current_note_id"):
        parts.append(f"Source note: {context['current_note_id']}")
    if context.get("current_document_filename"):
        parts.append(f"Source document: {context['current_document_filename']}")
    return " | ".join(parts)


def _search_query(message: str) -> str:
    for marker in ("search tasks for", "find tasks for", "search tasks", "find tasks"):
        value = _title_after(message, marker)
        if value:
            return value
    return message
