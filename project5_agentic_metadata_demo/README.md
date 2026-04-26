# Project 5: Agentic Metadata Demo

This project demonstrates how to keep a deterministic metadata microservice as
the source of truth while adding an agentic natural-language access path on top.

The metadata service owns SQLite access through SQLAlchemy and exposes stable
REST endpoints. The agent layer never queries the database directly. It decides
which metadata tool to use, calls the metadata service endpoint through that
tool, and returns both a readable answer and raw tool results.

## Architecture

```text
Traditional path:

Backend Service A
  -> Metadata Microservice
      -> SQLite metadata.db

Agentic path:

User
  -> Agentic Layer
      -> Tool call
          -> Metadata Microservice
              -> SQLite metadata.db
```

The key boundary is that the agentic layer is an additional access path, not a
replacement for the existing service contract.

## Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r project5_agentic_metadata_demo/requirements.txt
```

Optional OpenAI integration:

```bash
cp project5_agentic_metadata_demo/.env.example project5_agentic_metadata_demo/.env
export OPENAI_API_KEY=your_key_here
export OPENAI_MODEL=gpt-4.1-mini
```

If `OPENAI_API_KEY` is not set, the service uses the local rule-based fallback
agent.

## Seed The Database

The API seeds `project5_agentic_metadata_demo/metadata.db` on startup if it is
empty. You can also seed it explicitly:

```bash
python3 -m project5_agentic_metadata_demo.app.seed
```

The seed data includes these tables:

- `owners`
- `datasets`
- `schemas`
- `lineage`

There are 8 sample datasets across finance, analytics, growth, commerce,
customer support, and operations.

## Run The Server

```bash
uvicorn project5_agentic_metadata_demo.app.main:app --reload --port 8005
```

Then check:

```bash
curl http://localhost:8005/health
```

## Direct Metadata Microservice Calls

These calls represent another backend service using the deterministic metadata
API directly.

```bash
curl http://localhost:8005/datasets
```

```bash
curl "http://localhost:8005/datasets/search?owner_team=finance&keyword=revenue"
```

```bash
curl http://localhost:8005/datasets/1
```

```bash
curl http://localhost:8005/datasets/1/schema
```

```bash
curl http://localhost:8005/datasets/3/lineage
```

## Agentic Natural-Language Calls

These calls represent a user asking the agentic layer to choose and invoke the
metadata tools.

```bash
curl -X POST http://localhost:8005/agent/query \
  -H "Content-Type: application/json" \
  -d '{"question":"show all datasets"}'
```

```bash
curl -X POST http://localhost:8005/agent/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Find revenue-related datasets owned by the finance team and show their schemas."}'
```

```bash
curl -X POST http://localhost:8005/agent/query \
  -H "Content-Type: application/json" \
  -d '{"question":"show lineage for user profile dataset"}'
```

Example response shape:

```json
{
  "answer": "I found 2 matching dataset(s), revenue_transactions, revenue_forecast, and retrieved 7 schema columns.",
  "tool_calls": [
    {
      "tool": "search_datasets",
      "arguments": {
        "owner_team": "finance",
        "keyword": "revenue"
      }
    }
  ],
  "raw_results": {}
}
```

## Manual Smoke Test

Run this end-to-end check from the repository root after installing
dependencies:

```bash
python3 -m project5_agentic_metadata_demo.app.seed
uvicorn project5_agentic_metadata_demo.app.main:app --host 127.0.0.1 --port 8005
```

In another terminal, test the deterministic metadata service path:

```bash
curl http://127.0.0.1:8005/health
curl http://127.0.0.1:8005/datasets
curl "http://127.0.0.1:8005/datasets/search?owner_team=finance&keyword=revenue"
curl http://127.0.0.1:8005/datasets/1/schema
curl http://127.0.0.1:8005/datasets/3/lineage
```

Expected results:

- `/health` returns `{"status":"ok"}`.
- `/datasets` returns 8 seeded datasets.
- The finance revenue search returns `revenue_transactions` and
  `revenue_forecast`.
- `/datasets/1/schema` returns the `revenue_transactions` columns.
- `/datasets/3/lineage` returns lineage for `customer_profiles`.

Then test the natural-language agent path:

```bash
curl -X POST http://127.0.0.1:8005/agent/query \
  -H "Content-Type: application/json" \
  -d '{"question":"show all datasets"}'
```

```bash
curl -X POST http://127.0.0.1:8005/agent/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Find revenue-related datasets owned by the finance team and show their schemas."}'
```

```bash
curl -X POST http://127.0.0.1:8005/agent/query \
  -H "Content-Type: application/json" \
  -d '{"question":"show lineage for user profile dataset"}'
```

```bash
curl -X POST http://127.0.0.1:8005/agent/query \
  -H "Content-Type: application/json" \
  -d '{"question":"find sensitive datasets"}'
```

Expected agent behavior:

- `show all datasets` calls `list_datasets`.
- The finance revenue schema question calls `search_datasets`, then
  `get_schema` for the matching dataset IDs.
- The user profile lineage question calls `search_datasets`, then
  `get_lineage`.
- The sensitive dataset question calls `search_datasets` with
  `sensitivity_level=high`.

Every agent response should include `answer`, `tool_calls`, and `raw_results`.

## Metadata Tools

The agent can call only these tool functions:

- `list_datasets`
- `search_datasets`
- `get_dataset`
- `get_schema`
- `get_lineage`

Each tool calls a metadata microservice REST endpoint internally. This preserves
the production-style boundary where the metadata service owns database access,
auth boundaries, schema evolution, and deterministic behavior.

## Tradeoff

The microservice path is predictable and stable for backend integrations. It is
best when callers already know the endpoint and parameters they need.

The agentic path is more flexible for human questions. It can translate a vague
request into one or more metadata API calls and summarize the results. That
flexibility adds another layer of interpretation, so the deterministic metadata
service remains the system of record and the raw tool results are returned for
inspection.

## Tests

```bash
pytest project5_agentic_metadata_demo/tests
```
