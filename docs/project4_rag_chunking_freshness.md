# Project 4 RAG Chunking And Freshness

Project 4 now has two explicit RAG harnesses:

- deterministic chunking strategy benchmarks
- event-driven index freshness through SQLite change events

The goal is to keep the local demo small while making retrieval quality and
index freshness measurable from a clean checkout.

## Chunking Strategies

The production default remains `fixed`, which preserves the previous character
window behavior:

```text
chunk_text(text, chunk_size=900, overlap=120, strategy="fixed")
```

The benchmark also compares:

- `paragraph`: keeps paragraph boundaries when possible, then falls back to
  fixed splitting for oversized paragraphs.
- `sentence`: keeps sentence boundaries when possible, then falls back to fixed
  splitting for oversized sentences.

All strategies share the same normalization, chunk size, and overlap settings.

## Benchmark Command

Run the offline benchmark with:

```bash
python3 -m project4_agentic_project_copilot.app.chunking_benchmark
```

The benchmark uses:

- deterministic `HashEmbeddingClient`
- a fixed Project 4 launch/readiness corpus
- five retrieval cases
- `top_k=2`
- no OpenAI or network calls

Metrics:

- `chunk_count`: number of chunks produced.
- `average_chunk_chars`: mean chunk length.
- `expansion_ratio`: total chunk characters divided by source characters,
  including overlap.
- `recall_at_2`: share of benchmark queries where the top two chunks contain at
  least one expected term.
- `mean_matched_terms_at_2`: average expected terms found in the top two chunks.
- `mean_top_score`: average cosine score for the top retrieved chunk.

## Current Benchmark Result

Command run on April 27, 2026:

```bash
python3 -m project4_agentic_project_copilot.app.chunking_benchmark
```

| Strategy | Chunks | Avg chars | Expansion | Recall@2 | Matched terms@2 | Mean top score |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed | 5 | 214.60 | 1.175 | 1.000 | 2.800 | 0.494 |
| paragraph | 7 | 158.71 | 1.217 | 1.000 | 2.800 | 0.500 |
| sentence | 7 | 164.14 | 1.258 | 1.000 | 3.000 | 0.453 |

Interpretation:

- All strategies retrieved relevant context for every benchmark case.
- `sentence` preserved the most expected terms in the retrieved context.
- `paragraph` had the highest mean top similarity in this small corpus.
- `fixed` used the fewest chunks and lowest expansion, so it remains a
  conservative default until a larger corpus shows a quality win.

## Freshness Mechanism

The previous MVP refreshed the vector store by recomputing embeddings for all
stored chunks after every document insert or delete. The new path uses a
SQLite-backed event log:

```text
document_chunks change -> document_index_events pending row -> processor refreshes affected chunk -> event processed
```

The database owns the change capture:

- `AFTER INSERT ON document_chunks` creates `chunk_inserted` events.
- `AFTER UPDATE OF text ON document_chunks` creates `chunk_text_updated` events.
- `AFTER DELETE ON document_chunks` creates `chunk_deleted` events.

The app processes pending events immediately after upload/delete so the UI still
observes read-after-write freshness. The same event table can also be processed
by a later background worker because each event has durable status:

```text
pending -> processed
pending -> failed
```

For insert and text-update events, the processor recomputes only the affected
chunk embedding. Delete events are marked processed after confirming the chunk
is gone. This makes the refresh cost proportional to changed chunks rather than
all stored chunks.

## Validation

Targeted validation:

```bash
pytest project4_agentic_project_copilot/tests -q
python3 -m project4_agentic_project_copilot.app.chunking_benchmark
python3 -m project4_agentic_project_copilot.app.evaluation
```

The tests cover:

- fixed, paragraph, and sentence chunking behavior
- deterministic benchmark execution
- upload retrieval with citations
- document-library select/delete behavior
- CDC-style `document_chunks.text` updates creating freshness events
- event processing clearing pending events and refreshing embeddings
