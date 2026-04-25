# Project 4: Agentic Project Copilot

Project 4 is a new use case in this repo: an agentic project copilot that can
talk to files, structured task data, and project API tools.

It demonstrates a different pattern from the e-commerce support projects:

```text
orchestrator + RAG over files + safe text-to-SQL + confirmed API tool use
```

## Capabilities

- Upload markdown, text, reStructuredText, or PDF documents.
- Extract text, chunk it, create embeddings, and store chunks in local SQLite.
- Retrieve relevant chunks for file questions and answer with citations.
- Query a local SQLite task database through generated read-only SQL.
- Block destructive SQL such as `DELETE`, `UPDATE`, `DROP`, `INSERT`, and `ALTER`.
- Propose project API actions such as `create_task`, `update_task_status`,
  `assign_task`, and `add_comment`.
- Require confirmation before any state-changing API action executes.
- Track session context: current project, current task, current document,
  conversation history, and pending actions.
- Log orchestration decisions and tool usage to JSONL traces.
- Run an evaluation harness for routing, retrieval, SQL safety, and tool behavior.

## Run

```bash
pip install -e ".[dev]"
set -a
source .env
set +a
uvicorn project4_agentic_project_copilot.app.main:app --reload --port 8004
```

Then open:

```text
http://127.0.0.1:8004/
```

The live app uses OpenAI for LLM calls and embeddings. Tests use deterministic
local clients so they do not require network access.

Live configuration comes from the environment:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.5
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_TIMEOUT_SECONDS=30
```

The browser UI sends the active `session_id` with file uploads, and the upload
response includes the updated session context. The uploaded document becomes the
session's current document. Follow-ups such as "what is this file doing?" or
"how about now" after an upload retrieve from that document and return
citations. If a model or API call fails, the backend returns a visible chat
response and the UI shows request/upload errors instead of silently dropping the
turn.

## Local Data

Project 4 uses local SQLite for the MVP:

```text
project4_agentic_project_copilot/data/copilot.sqlite3
```

The task schema includes:

- `projects`
- `tasks`
- `users`
- `comments`
- `status_history`
- `documents`
- `document_chunks`

The vector store is implemented as SQLite `document_chunks` rows with JSON
embeddings. This keeps the MVP local and inspectable while preserving the core
RAG shape.

## Safety Model

The LLM proposes. Backend code validates and executes.

For SQL:

```text
user question -> SQL agent proposes SELECT -> backend validates read-only SQL -> SQLite executes -> assistant explains
```

For API tools:

```text
user request -> tool agent proposes action -> assistant shows preview -> user confirms -> backend executes
```

State-changing actions are never executed on the first model proposal.

## Tests

```bash
pytest project4_agentic_project_copilot/tests -q
python3 -m project4_agentic_project_copilot.app.evaluation
```
