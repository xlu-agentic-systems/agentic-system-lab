# Project 4: Local-First AI Productivity Workflows

Project 4 is a local-first productivity workflow prototype. It can talk to
uploaded files and notes, structured task data, and reviewable productivity
tools while keeping documents, notes, workflow state, sessions, traces, and eval
artifacts in local files or SQLite.

Local-first means the workflow data and artifacts are local and inspectable. The
live app still uses OpenAI for LLM calls and embeddings unless deterministic
test clients or another local provider are injected.

It demonstrates a different pattern from the e-commerce support projects:

```text
orchestrator + RAG over files/notes + safe text-to-SQL + reviewed tool use
```

## Capabilities

- Upload markdown, text, reStructuredText, or PDF documents.
- Extract text, chunk it, create embeddings, and store chunks in local SQLite.
- Compare fixed, paragraph, and sentence chunking strategies with an offline
  retrieval benchmark.
- View, select, and delete uploaded documents from the document library.
- Retrieve relevant chunks for file questions and answer with citations.
- Keep chunk embeddings fresh through SQLite change events instead of full
  corpus reindexing.
- Capture personal notes through a review-gated `create_note` tool.
- Search persisted personal notes through a read-only `search_notes` tool.
- Convert current file or note context into follow-up tasks with persisted
  workflow state.
- Query a local SQLite task database through generated read-only SQL.
- Block destructive SQL such as `DELETE`, `UPDATE`, `DROP`, `INSERT`, and `ALTER`.
- Propose project API actions such as `create_task`, `update_task_status`,
  `assign_task`, `add_comment`, and `create_note`.
- Require confirmation before any state-changing productivity action executes.
- Track session context: current project, current task, current document,
  current note, current workflow, conversation history, and pending actions.
- Persist workflow runs and steps for reviewable actions in SQLite.
- Log orchestration decisions and tool usage to JSONL traces.
- Run evaluation harnesses for routing, retrieval, tool selection, output
  quality, safety, review gates, workflow state, and regressions.

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

The in-app trace viewer is available at:

```text
http://127.0.0.1:8004/debug
```

It visualizes each chat turn as an execution tree with the user turn,
orchestrator route, invoked agent/tool path, validation decisions, retrieved
evidence or SQL/tool artifacts, and final response lineage.

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
session's current document, including the uploaded filename. The UI shows the
current file in Runtime State and adds a chat-visible upload confirmation.
Follow-ups such as "what is this file doing?" or "how about now" after an upload
retrieve from that document and return citations. If a model or API call fails,
the backend returns a visible chat response and the UI shows request/upload
errors instead of silently dropping the turn.

Uploaded files persist in the local SQLite database until deleted through the
document library or the database file is removed. Deleting a selected document
clears it from the current session.

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
- `personal_notes`
- `productivity_workflows`
- `workflow_steps`
- `documents`
- `document_chunks`
- `trace_turns`
- `trace_spans`

The vector store is implemented as SQLite `document_chunks` rows with JSON
embeddings. This keeps the MVP local and inspectable while preserving the core
RAG shape. Chunk inserts, text updates, and deletes are captured in
`document_index_events` so the app can refresh only affected chunk embeddings.

Uploads keep the SQLite write transaction short by inserting extracted chunks
first, then processing pending index events after commit. This keeps the local
vector store consistent with the document library while avoiding full-corpus
reindexing after every change.

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
The pending action is also represented as a `productivity_workflows` row with
`awaiting_review` status. Confirmation executes the tool and marks the workflow
`completed` or `failed`, with `workflow_steps` preserving the proposed and
confirmed execution records.

## Trace Debugging

Project 4 records structured execution traces in SQLite. Each chat response gets
a `trace_id`, and the debug UI reads:

```text
GET /debug/traces
GET /debug/traces/{trace_id}
```

Trace spans capture:

- the user turn
- the orchestrator route decision
- RAG retrieval and answer synthesis
- SQL generation, validation, and SQLite execution
- tool proposal, review-gate blocking, and confirmed execution
- context lookups, clarification handling, and final response assembly

The trace viewer is meant for local debugging and QA. JSONL trace export remains
available when a `JsonlTraceStore` is injected in tests or local harnesses.

## Eval Harnesses

The standard eval command runs JSONL regression cases for routing, RAG,
read-only SQL, destructive-SQL refusal, tool selection, review gates, note
capture, and note-to-task workflow conversion:

```bash
python3 -m project4_agentic_project_copilot.app.evaluation
```

The same module exposes `run_goal_harness()`, which checks the specific product
claim end to end:

- RAG over an uploaded personal notes document returns citations.
- Uploaded documents persist locally.
- Personal note creation is blocked behind human review.
- Confirmed note creation writes durable note state.
- Note-to-task conversion creates persisted workflow state before execution.
- Human confirmation completes the workflow and creates the task.
- The regression eval suite still passes.

## Tests

```bash
pytest project4_agentic_project_copilot/tests -q
python3 -m project4_agentic_project_copilot.app.chunking_benchmark
python3 -m project4_agentic_project_copilot.app.evaluation
```
