# Project 4 RAG Pipeline

Project 4 is an agentic project copilot with several capability paths:

- file RAG over uploaded documents
- read-only SQL over a local task database
- confirmed API tool calls for task actions
- session context and clarification

The RAG path is one branch of the larger copilot architecture. It is used when
the user asks about uploaded files or when the current conversation context
points to a selected document.

## What Gets Persisted

Uploaded files are persisted in local SQLite:

```text
project4_agentic_project_copilot/data/copilot.sqlite3
```

Project 4 stores document metadata and extracted chunks:

- `documents`: `document_id`, `filename`, `content_type`, `created_at`
- `document_chunks`: `chunk_id`, `document_id`, `chunk_index`, extracted
  `text`, and `embedding_json`

The raw original file bytes are not stored. The system stores extracted text
chunks and embeddings.

Session context is stored separately in JSON:

```text
project4_agentic_project_copilot/data/sessions.json
```

The active session tracks the current document:

```text
current_document_id
current_document_filename
```

This is why a follow-up like "what does this file say?" can resolve to the file
selected in that session without searching another user's current document.

## Upload And Index Flow

Upload entry point:

```text
POST /upload
```

The current implementation follows this flow:

```mermaid
sequenceDiagram
    participant UI as Browser UI
    participant API as FastAPI /upload
    participant Extract as extract_text
    participant Chunk as chunk_text
    participant Embed as OpenAI embeddings
    participant DB as SQLite document tables
    participant Session as JsonSessionStore

    UI->>API: file + session_id
    API->>Extract: read markdown/text/rst/pdf text
    Extract->>Chunk: normalize and split text
    API->>DB: short write transaction inserts document + chunks
    DB->>DB: trigger pending index events for new chunks
    API->>Embed: embed changed chunks from pending events
    API->>DB: update embedding_json and mark events processed
    API->>Session: set current_document_id and filename
    API-->>UI: UploadResponse with document id, chunk count, context
```

Important details:

- Text files, markdown, reStructuredText, and PDFs are supported.
- PDF text extraction uses `pypdf`.
- Chunk embeddings are computed before the SQLite write transaction starts, so
  the database is not locked while waiting on external embedding calls.
- After insert, SQLite triggers enqueue per-chunk index events. The app
  processes those events immediately for read-after-write freshness.

## Document Library Flow

The UI document library is backed by these endpoints:

```text
GET /documents
POST /documents/{document_id}/select
DELETE /documents/{document_id}?session_id=...
```

The library lets the user:

- view all stored documents
- select an existing document as the current session document
- delete a document

Selecting a document updates only session context. It does not recompute
embeddings because the stored document chunks did not change.

Deleting a document removes its chunks, clears the current session document if
that deleted document was selected, and records delete events so the freshness
processor can mark the removed chunks as processed.

## Retrieval And Answer Flow

Chat entry point:

```text
POST /chat
```

The file RAG path follows this flow:

```mermaid
sequenceDiagram
    participant UI as Browser UI
    participant API as FastAPI /chat
    participant Service as ProjectCopilotService
    participant Store as DocumentStore
    participant Embed as OpenAI embeddings
    participant QA as FileQaAgent
    participant Trace as JsonlTraceStore

    UI->>API: user message + session_id
    API->>Service: load session context
    Service->>Service: decide file path from message/context
    Service->>Store: search current document chunks
    Store->>Embed: embed user query
    Store->>Store: cosine similarity over stored JSON embeddings
    Store-->>Service: top retrieved chunks
    Service->>QA: ask model to answer using only chunks
    QA-->>Service: FileAnswer + cited chunk ids
    Service->>Trace: append decision and response trace
    Service-->>UI: ChatResponse with answer and citations
```

The backend constrains retrieval to the current session document for file
questions. This avoids one session accidentally answering from a document that
another session uploaded.

In a typical RAG turn, there are two LLM-backed agents:

- `CopilotOrchestrator`: routes the request to `file_retrieval` and may produce
  the search query.
- `FileQaAgent`: handles the generation step by answering from retrieved chunks
  and returning cited chunk ids.

`DocumentStore` is not an agent. It handles query embedding, SQLite chunk lookup,
cosine ranking, and top-k retrieval. The orchestrator does not call other agents
directly; `ProjectCopilotService` receives its route decision and invokes the
matching handler: `FileQaAgent` for file retrieval, `SqlAgent` for SQL,
`ToolAgent` for API tools, or service-level context and clarification handlers.

The `FileQaAgent` is instructed to answer using only retrieved chunks. The
response includes citations built from retrieved chunk metadata:

```text
document_id
filename
chunk_id
chunk_index
score
quote
```

## Where The Orchestrator Fits

Project 4 is not a pure RAG app. The copilot can route a turn to:

- `file_retrieval`
- `sql_query`
- `api_tool`
- `context`
- `clarify`

For direct file questions with a selected current document, backend preflight
routes to the RAG path. For other requests, `CopilotOrchestrator` produces a
structured `OrchestratorDecision`.

This keeps RAG as a capability path inside an agentic orchestration layer rather
than making retrieval the only behavior.

## Chunking Benchmarks

Project 4 can compare fixed, paragraph, and sentence chunking without network
calls:

```bash
python3 -m project4_agentic_project_copilot.app.chunking_benchmark
```

The benchmark uses deterministic hash embeddings and a fixed corpus. Current
results are documented in `project4_rag_chunking_freshness.md`.

## Why Event-Driven Freshness

The user-facing requirement is that stored document changes should keep the
vector store current. Project 4 now captures `document_chunks` changes in a
SQLite `document_index_events` table and processes pending events to refresh
only affected chunks.

Tradeoffs:

- **Simple and inspectable:** freshness state is visible in SQLite.
- **Good for a small local demo:** pending events are processed inline after
  upload/delete, so no worker is required.
- **Closer to production:** the same event table can be drained by a background
  worker later.
- **Still local:** retrieval remains a full scan over SQLite JSON embeddings.

In production, this would usually become transactional CDC plus incremental
vector index updates and a background repair/reindex job.

## Safety And Limitations

The RAG path improves grounding but does not make the answer externally
verified. File answers mean "according to the uploaded document."

Current limitations:

- The vector store is SQLite rows with JSON embeddings, not a dedicated vector
  database.
- Retrieval uses a full scan with cosine similarity.
- The original uploaded file bytes are not retained.
- Inline event processing still runs in the request path for this MVP.
- Document access is session-context based in the prototype; there is no auth
  middleware.

These constraints keep the project small and easy to inspect while preserving
the real RAG architecture shape.
