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
  task_id INTEGER PRIMARY KEY AUTOINCREMENT,
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
  comment_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS status_history (
  history_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  old_status TEXT,
  new_status TEXT NOT NULL,
  changed_by INTEGER,
  changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(changed_by) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS personal_notes (
  note_id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  source_document_id TEXT,
  source_filename TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(source_document_id) REFERENCES documents(document_id)
);

CREATE TABLE IF NOT EXISTS productivity_workflows (
  workflow_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  workflow_type TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('awaiting_review', 'completed', 'failed')),
  pending_action_id TEXT,
  source_document_id TEXT,
  source_note_id INTEGER,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(source_document_id) REFERENCES documents(document_id),
  FOREIGN KEY(source_note_id) REFERENCES personal_notes(note_id)
);

CREATE TABLE IF NOT EXISTS workflow_steps (
  step_id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_id TEXT NOT NULL,
  step_index INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  output_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(workflow_id) REFERENCES productivity_workflows(workflow_id)
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
  FOREIGN KEY(document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trace_turns (
  trace_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  user_message TEXT NOT NULL,
  final_response TEXT NOT NULL,
  route TEXT NOT NULL,
  status TEXT NOT NULL,
  span_count INTEGER NOT NULL DEFAULT 0,
  spans_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS tasks_project_idx ON tasks(project_id);
CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks(status);
CREATE INDEX IF NOT EXISTS document_chunks_document_idx ON document_chunks(document_id, chunk_index);
CREATE INDEX IF NOT EXISTS trace_turns_created_at_idx ON trace_turns(created_at);

INSERT OR IGNORE INTO users(user_id, name, email) VALUES
  (1, 'Alice Chen', 'alice@example.com'),
  (2, 'Ben Patel', 'ben@example.com'),
  (3, 'Casey Rivera', 'casey@example.com');

INSERT OR IGNORE INTO projects(project_id, name, description) VALUES
  (1, 'Apollo Launch', 'Project launch checklist and delivery work.'),
  (2, 'Design System', 'Component library and UI governance.');

INSERT OR IGNORE INTO tasks(task_id, project_id, title, description, status, assignee_id) VALUES
  (1, 1, 'Draft launch brief', 'Write project launch narrative.', 'open', 1),
  (2, 1, 'Review API contract', 'Validate integration contract with backend.', 'in_progress', 2),
  (3, 1, 'Prepare rollout checklist', 'Confirm owners and launch gates.', 'blocked', NULL),
  (4, 2, 'Audit button states', 'Document button variants and gaps.', 'open', 3);

INSERT OR IGNORE INTO comments(comment_id, task_id, user_id, body) VALUES
  (1, 2, 1, 'Need confirmation on pagination fields.'),
  (2, 3, 2, 'Blocked by missing QA owner.');

INSERT OR IGNORE INTO status_history(history_id, task_id, old_status, new_status, changed_by) VALUES
  (1, 2, 'open', 'in_progress', 2),
  (2, 3, 'open', 'blocked', 2);
