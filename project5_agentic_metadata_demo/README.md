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

## Demo Authentication And Policy

All metadata and agent endpoints except `/health` require demo identity headers:

```text
X-User: service-a
X-Team: platform
X-Role: service
```

Supported roles are `viewer`, `editor`, `admin`, and `service`.

Policy rules:

- `viewer` can read public/internal datasets and datasets owned by their team.
- `editor` can create or update non-sensitive metadata for their own team.
- `admin` and `service` can create or update across teams.
- Deletes require `admin` or `service` plus
  `X-Confirm-Dangerous-Action: true`.
- The agent forwards the caller identity to the metadata service, so model
  behavior cannot bypass backend policy.

## Direct Metadata Microservice Calls

These calls represent another backend service using the deterministic metadata
API directly.

```bash
curl http://localhost:8005/datasets \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service"
```

```bash
curl "http://localhost:8005/datasets/search?owner_team=finance&keyword=revenue" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service"
```

```bash
curl http://localhost:8005/datasets/1 \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service"
```

```bash
curl http://localhost:8005/datasets/1/schema \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service"
```

```bash
curl http://localhost:8005/datasets/3/lineage \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service"
```

Write endpoint examples:

```bash
curl -X POST http://localhost:8005/datasets \
  -H "Content-Type: application/json" \
  -H "X-User: fran" \
  -H "X-Team: finance" \
  -H "X-Role: editor" \
  -d '{"name":"finance_public_metrics","description":"Published finance metrics.","owner_team":"finance","data_source":"metrics_store","sensitivity_level":"internal"}'
```

```bash
curl -X PATCH http://localhost:8005/datasets/2 \
  -H "Content-Type: application/json" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service" \
  -d '{"description":"Updated by a backend service through the stable metadata API."}'
```

```bash
curl -X DELETE http://localhost:8005/datasets/8 \
  -H "X-User: admin" \
  -H "X-Team: security" \
  -H "X-Role: admin" \
  -H "X-Confirm-Dangerous-Action: true"
```

## Agentic Natural-Language Calls

These calls represent a user asking the agentic layer to choose and invoke the
metadata tools.

```bash
curl -X POST http://localhost:8005/agent/query \
  -H "Content-Type: application/json" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service" \
  -d '{"question":"show all datasets"}'
```

```bash
curl -X POST http://localhost:8005/agent/query \
  -H "Content-Type: application/json" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service" \
  -d '{"question":"Find revenue-related datasets owned by the finance team and show their schemas."}'
```

```bash
curl -X POST http://localhost:8005/agent/query \
  -H "Content-Type: application/json" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service" \
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
curl http://127.0.0.1:8005/datasets -H "X-User: service-a" -H "X-Team: platform" -H "X-Role: service"
curl "http://127.0.0.1:8005/datasets/search?owner_team=finance&keyword=revenue" -H "X-User: service-a" -H "X-Team: platform" -H "X-Role: service"
curl http://127.0.0.1:8005/datasets/1/schema -H "X-User: service-a" -H "X-Team: platform" -H "X-Role: service"
curl http://127.0.0.1:8005/datasets/3/lineage -H "X-User: service-a" -H "X-Team: platform" -H "X-Role: service"
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
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service" \
  -d '{"question":"show all datasets"}'
```

```bash
curl -X POST http://127.0.0.1:8005/agent/query \
  -H "Content-Type: application/json" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service" \
  -d '{"question":"Find revenue-related datasets owned by the finance team and show their schemas."}'
```

```bash
curl -X POST http://127.0.0.1:8005/agent/query \
  -H "Content-Type: application/json" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service" \
  -d '{"question":"show lineage for user profile dataset"}'
```

```bash
curl -X POST http://127.0.0.1:8005/agent/query \
  -H "Content-Type: application/json" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service" \
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

## Evil Client Smoke Test

With the server running, execute the adversarial client script:

```bash
METADATA_DEMO_URL=http://127.0.0.1:8005 python3 -m project5_agentic_metadata_demo.evil_clients
```

The script tries unauthenticated reads, cross-team sensitive reads, sensitive
record creation, direct deletes, agent-driven deletes, database wipe prompts,
and cross-team agent reads. Expected behavior is denial or filtered results. In
particular, a finance editor can ask the agent to delete a revenue dataset, but
the metadata service denies the `delete_dataset` tool call because delete
requires admin/service role and explicit dangerous-action confirmation.

## Metadata Tools

The agent can call only these tool functions:

- `list_datasets`
- `search_datasets`
- `get_dataset`
- `get_schema`
- `get_lineage`
- `create_dataset`
- `update_dataset`
- `delete_dataset`

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
