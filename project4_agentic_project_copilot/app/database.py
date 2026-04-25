from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "copilot.sqlite3"


class CopilotDatabase:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            self._seed(conn)

    def schema_description(self) -> str:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT m.name AS table_name, p.name AS column_name, p.type AS column_type
                FROM sqlite_master m
                JOIN pragma_table_info(m.name) p
                WHERE m.type = 'table'
                  AND m.name NOT LIKE 'sqlite_%'
                  AND m.name NOT IN ('documents', 'document_chunks')
                ORDER BY m.name, p.cid
                """
            ).fetchall()
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row["table_name"], []).append(f"{row['column_name']} {row['column_type']}")
        return "\n".join(f"{table}({', '.join(columns)})" for table, columns in grouped.items())

    def execute_read(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def execute_write(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return int(cursor.lastrowid)

    def _seed(self, conn: sqlite3.Connection) -> None:
        existing = conn.execute("SELECT COUNT(*) AS count FROM projects").fetchone()["count"]
        if existing:
            return
        conn.executemany(
            "INSERT INTO users(user_id, name, email) VALUES (?, ?, ?)",
            [
                (1, "Alice Chen", "alice@example.com"),
                (2, "Ben Patel", "ben@example.com"),
                (3, "Casey Rivera", "casey@example.com"),
            ],
        )
        conn.executemany(
            "INSERT INTO projects(project_id, name, description) VALUES (?, ?, ?)",
            [
                (1, "Apollo Launch", "Project launch checklist and delivery work."),
                (2, "Design System", "Component library and UI governance."),
            ],
        )
        conn.executemany(
            """
            INSERT INTO tasks(task_id, project_id, title, description, status, assignee_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 1, "Draft launch brief", "Write project launch narrative.", "open", 1),
                (2, 1, "Review API contract", "Validate integration contract with backend.", "in_progress", 2),
                (3, 1, "Prepare rollout checklist", "Confirm owners and launch gates.", "blocked", None),
                (4, 2, "Audit button states", "Document button variants and gaps.", "open", 3),
            ],
        )
        conn.executemany(
            "INSERT INTO comments(comment_id, task_id, user_id, body) VALUES (?, ?, ?, ?)",
            [
                (1, 2, 1, "Need confirmation on pagination fields."),
                (2, 3, 2, "Blocked by missing QA owner."),
            ],
        )
        conn.executemany(
            """
            INSERT INTO status_history(history_id, task_id, old_status, new_status, changed_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, 2, "open", "in_progress", 2),
                (2, 3, "open", "blocked", 2),
            ],
        )


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS projects (
  project_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK(status IN ('open', 'in_progress', 'blocked', 'done')),
  assignee_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(project_id) REFERENCES projects(project_id),
  FOREIGN KEY(assignee_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS comments (
  comment_id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS status_history (
  history_id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL,
  old_status TEXT,
  new_status TEXT NOT NULL,
  changed_by INTEGER,
  changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(changed_by) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT PRIMARY KEY,
  filename TEXT NOT NULL,
  content_type TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS document_chunks (
  chunk_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  embedding_json TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(document_id)
);
"""
