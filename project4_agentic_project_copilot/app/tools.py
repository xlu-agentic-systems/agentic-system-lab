from __future__ import annotations

from typing import Any

from project4_agentic_project_copilot.app.database import CopilotDatabase
from project4_agentic_project_copilot.app.models import ToolArgs, ToolCall, ToolResult


WRITE_TOOLS = {"create_task", "update_task_status", "assign_task", "add_comment", "create_note"}


class ProjectToolService:
    def __init__(self, db: CopilotDatabase | None = None) -> None:
        self.db = db or CopilotDatabase()

    def preview(self, tool_call: ToolCall) -> str:
        args = _args_dict(tool_call.args)
        if tool_call.name == "create_task":
            return f"Create task '{args.get('title')}' in project {args.get('project_id')}."
        if tool_call.name == "update_task_status":
            return f"Update task {args.get('task_id')} to status '{args.get('status')}'."
        if tool_call.name == "assign_task":
            return f"Assign task {args.get('task_id')} to user {args.get('user_id')}."
        if tool_call.name == "add_comment":
            return f"Add a comment to task {args.get('task_id')}: {args.get('body')}"
        if tool_call.name == "create_note":
            return f"Create personal note '{args.get('title')}'."
        if tool_call.name == "search_notes":
            return f"Search personal notes for {args.get('query')!r}."
        return f"Search tasks for {args.get('query')!r}."

    def execute(self, tool_call: ToolCall) -> ToolResult:
        args = _args_dict(tool_call.args)
        try:
            if tool_call.name == "create_task":
                return self._create_task(args)
            if tool_call.name == "update_task_status":
                return self._update_task_status(args)
            if tool_call.name == "assign_task":
                return self._assign_task(args)
            if tool_call.name == "add_comment":
                return self._add_comment(args)
            if tool_call.name == "search_tasks":
                return self._search_tasks(args)
            if tool_call.name == "create_note":
                return self._create_note(args)
            if tool_call.name == "search_notes":
                return self._search_notes(args)
        except Exception as exc:
            return ToolResult(name=tool_call.name, ok=False, error=str(exc))
        return ToolResult(name=tool_call.name, ok=False, error=f"Unsupported tool: {tool_call.name}")

    def _create_task(self, args: dict[str, Any]) -> ToolResult:
        project_id = _required_int(args, "project_id")
        title = _required_str(args, "title")
        description = str(args.get("description") or "")
        _require_exists(self.db, "projects", "project_id", project_id)
        if args.get("assignee_id") is not None:
            _require_exists(self.db, "users", "user_id", int(args["assignee_id"]))
        task_id = self.db.execute_write(
            """
            INSERT INTO tasks(project_id, title, description, status, assignee_id)
            VALUES (?, ?, ?, 'open', ?)
            """,
            (project_id, title, description, args.get("assignee_id")),
        )
        return ToolResult(name="create_task", ok=True, result={"task_id": task_id, "project_id": project_id})

    def _update_task_status(self, args: dict[str, Any]) -> ToolResult:
        task_id = _required_int(args, "task_id")
        status = _required_str(args, "status")
        if status not in {"open", "in_progress", "blocked", "done"}:
            raise ValueError("status must be open, in_progress, blocked, or done")
        existing = self.db.execute_read("SELECT status FROM tasks WHERE task_id = ?", (task_id,))
        if not existing:
            raise ValueError(f"task {task_id} does not exist")
        old_status = existing[0]["status"]
        if args.get("changed_by") is not None:
            _require_exists(self.db, "users", "user_id", int(args["changed_by"]))
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                (status, task_id),
            )
            conn.execute(
                "INSERT INTO status_history(task_id, old_status, new_status, changed_by) VALUES (?, ?, ?, ?)",
                (task_id, old_status, status, args.get("changed_by") or 1),
            )
            conn.commit()
        return ToolResult(name="update_task_status", ok=True, result={"task_id": task_id, "status": status})

    def _assign_task(self, args: dict[str, Any]) -> ToolResult:
        task_id = _required_int(args, "task_id")
        user_id = _required_int(args, "user_id")
        _require_exists(self.db, "tasks", "task_id", task_id)
        _require_exists(self.db, "users", "user_id", user_id)
        with self.db.connect() as conn:
            updated = conn.execute(
                "UPDATE tasks SET assignee_id = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                (user_id, task_id),
            ).rowcount
            conn.commit()
        if not updated:
            raise ValueError(f"task {task_id} does not exist")
        return ToolResult(name="assign_task", ok=True, result={"task_id": task_id, "user_id": user_id})

    def _add_comment(self, args: dict[str, Any]) -> ToolResult:
        task_id = _required_int(args, "task_id")
        user_id = int(args.get("user_id") or 1)
        body = _required_str(args, "body")
        _require_exists(self.db, "tasks", "task_id", task_id)
        _require_exists(self.db, "users", "user_id", user_id)
        comment_id = self.db.execute_write(
            "INSERT INTO comments(task_id, user_id, body) VALUES (?, ?, ?)",
            (task_id, user_id, body),
        )
        return ToolResult(name="add_comment", ok=True, result={"comment_id": comment_id, "task_id": task_id})

    def _search_tasks(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or "").lower()
        rows = self.db.execute_read(
            """
            SELECT task_id, title, status, project_id, assignee_id
            FROM tasks
            WHERE lower(title) LIKE ? OR lower(description) LIKE ? OR lower(status) LIKE ?
            ORDER BY task_id
            """,
            (f"%{query}%", f"%{query}%", f"%{query}%"),
        )
        return ToolResult(name="search_tasks", ok=True, result=rows)

    def _create_note(self, args: dict[str, Any]) -> ToolResult:
        title = _required_str(args, "title")
        body = _required_str(args, "body")
        note_id = self.db.execute_write(
            """
            INSERT INTO personal_notes(title, body, source_document_id, source_filename)
            VALUES (?, ?, ?, ?)
            """,
            (
                title,
                body,
                args.get("source_document_id"),
                args.get("source_filename"),
            ),
        )
        return ToolResult(
            name="create_note",
            ok=True,
            result={
                "note_id": note_id,
                "title": title,
                "source_document_id": args.get("source_document_id"),
            },
        )

    def _search_notes(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or "").lower()
        rows = self.db.execute_read(
            """
            SELECT note_id, title, body, source_filename, created_at
            FROM personal_notes
            WHERE lower(title) LIKE ? OR lower(body) LIKE ? OR lower(source_filename) LIKE ?
            ORDER BY note_id
            """,
            (f"%{query}%", f"%{query}%", f"%{query}%"),
        )
        return ToolResult(name="search_notes", ok=True, result=rows)


def _required_int(args: dict[str, Any], key: str) -> int:
    value = args.get(key)
    if value is None:
        raise ValueError(f"missing required arg: {key}")
    return int(value)


def _required_str(args: dict[str, Any], key: str) -> str:
    value = str(args.get(key) or "").strip()
    if not value:
        raise ValueError(f"missing required arg: {key}")
    return value


def _require_exists(db: CopilotDatabase, table: str, column: str, value: int) -> None:
    rows = db.execute_read(f"SELECT 1 AS found FROM {table} WHERE {column} = ?", (value,))
    if not rows:
        raise ValueError(f"{table}.{column}={value} does not exist")


def _args_dict(args: ToolArgs | dict[str, Any]) -> dict[str, Any]:
    if isinstance(args, ToolArgs):
        return args.clean()
    return dict(args)
