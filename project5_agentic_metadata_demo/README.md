# Project 5: Agentic Access Layer Over a Metadata Microservice

This project demonstrates a common modernization pattern:

```text
Keep the existing deterministic metadata service.
Add an agentic access layer beside it.
Do not let the agent become the system of record.
```

So, is this project "making the traditional metadata service agentic"?

Not exactly. A better description is:

```text
The metadata service stays traditional.
The user-facing access path becomes agentic.
```

The metadata microservice still owns the database, REST contract, validation,
authorization, and deterministic behavior. The agent layer is an additional
interface that translates natural language into calls against that existing
service.

## Learning Goals

This demo is meant to teach four ideas:

1. Existing backend services do not need to be replaced by agents.
2. Agents should use narrow service APIs as tools instead of direct database
   access.
3. Authorization must live in deterministic backend policy, not in model
   judgment.
4. Dangerous prompts should fail because the tool/service boundary refuses
   them, not because the model happens to be well behaved.

## Architecture At A Glance

```text
Traditional backend path:

Backend Service A
  -> Metadata Microservice
      -> SQLite metadata.db

Agentic user path:

User
  -> Agentic Layer
      -> Metadata Tool
          -> Metadata Microservice
              -> SQLite metadata.db

Agent/service path:

Other Agent or Service
  -> Structured Agent Task API
      -> Metadata Tool
          -> Metadata Microservice
              -> SQLite metadata.db
```

Both paths reach the same metadata service. That is the key design point.

The agent does not query SQLite. It does not own metadata rules. It does not
silently bypass REST APIs. It chooses tools, and those tools call the metadata
service.

Project 5 exposes three caller-facing paths:

```text
Caller knows the exact operation:
  -> call the metadata REST API directly

Human wants flexible exploration:
  -> call POST /agent/query with natural language

Another service/agent wants tool-use composition:
  -> call POST /agent/tasks with structured intent

MCP-capable agent wants standard tool discovery:
  -> connect to the Project 5 MCP server
      -> call exposed metadata MCP tools
```

## What Is Traditional Here?

The metadata microservice is intentionally ordinary backend software:

- FastAPI routes expose stable REST endpoints.
- SQLAlchemy owns relational database access.
- SQLite is the local source of truth.
- Pydantic schemas define request and response shapes.
- Auth and policy checks run before protected operations.
- Tests verify deterministic behavior.

This service behaves like something other backend systems can safely integrate
with.

## What Is Agentic Here?

The agentic layer accepts natural language:

```json
{
  "question": "Find revenue-related datasets owned by finance and show their schemas."
}
```

It then decides which metadata tools to call:

```json
[
  {
    "tool": "search_datasets",
    "arguments": {
      "owner_team": "finance",
      "keyword": "revenue"
    }
  },
  {
    "tool": "get_schema",
    "arguments": {
      "dataset_id": 1
    }
  }
]
```

Finally, it returns a readable answer plus raw tool results:

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
    },
    {
      "tool": "get_schema",
      "arguments": {
        "dataset_id": 1
      }
    }
  ],
  "raw_results": {
    "1_search_datasets": [],
    "2_get_schema": []
  }
}
```

If `OPENAI_API_KEY` is set, the agent can use OpenAI tool calling. If no key is
available, a local rule-based fallback handles simple demo questions.

## Why This Boundary Matters

The risky version of this architecture would be:

```text
User
  -> Agent
      -> Raw SQL / database credentials
          -> metadata.db
```

That design makes the model too powerful. A prompt like "delete the database"
or "show me another team's sensitive datasets" could become dangerous if the
agent has broad tools.

This project uses the safer pattern:

```text
User
  -> Agent
      -> Narrow tool call
          -> Metadata service policy
              -> Allowed or denied
```

The model may propose an action. The backend decides whether it is allowed.

## Source Of Truth

```text
metadata.db
  owned by Metadata Microservice

Metadata Microservice
  owns database reads/writes
  owns auth and policy
  owns schema evolution
  owns deterministic responses

Agentic Layer
  owns natural-language interpretation
  owns tool selection
  does not own database access
  does not own authorization
```

This separation is the main lesson of the project.

## Project Structure

```text
project5_agentic_metadata_demo/
  app/
    main.py               # FastAPI app assembly
    database.py           # SQLAlchemy engine/session setup
    models.py             # SQLAlchemy tables
    schemas.py            # Pydantic request/response models
    seed.py               # Sample metadata seed data
    metadata_service.py   # Deterministic REST API
    agent_service.py      # Natural-language and structured task endpoints
    tools.py              # Tool facade over metadata REST endpoints
    llm_agent.py          # Optional OpenAI tool-calling agent
    rule_based_agent.py   # Local fallback agent
    structured_agent.py   # Machine-facing task runner over the same tools
    mcp_server.py         # Optional MCP adapter over the same metadata tools
    auth.py               # Demo identity and policy gates
  tests/
    test_metadata_service.py
    test_agent_service.py
  evil_clients.py         # Adversarial smoke-test clients
  requirements.txt
  README.md
  .env.example
```

## Data Model

The local SQLite database contains four metadata tables:

- `owners`
- `datasets`
- `schemas`
- `lineage`

The seed data includes 8 datasets across finance, analytics, growth, commerce,
customer support, and operations. They intentionally vary by owner team,
sensitivity level, source system, and update time so policy behavior is visible.

Example dataset fields:

- `id`
- `name`
- `description`
- `owner_team`
- `created_at`
- `updated_at`
- `data_source`
- `sensitivity_level`

## API Surface

Read endpoints:

- `GET /health`
- `GET /datasets`
- `GET /datasets/{dataset_id}`
- `GET /datasets/search?owner_team=&sensitivity_level=&keyword=`
- `GET /datasets/{dataset_id}/schema`
- `GET /datasets/{dataset_id}/lineage`

Write endpoints:

- `POST /datasets`
- `PATCH /datasets/{dataset_id}`
- `DELETE /datasets/{dataset_id}`

Agent endpoints:

- `POST /agent/query`
- `POST /agent/tasks`

Optional MCP adapter:

- `python3 -m project5_agentic_metadata_demo.app.mcp_server`

## Demo Authentication And Policy

All endpoints except `/health` require demo identity headers:

```text
X-User: service-a
X-Team: platform
X-Role: service
```

Supported roles:

- `viewer`
- `editor`
- `admin`
- `service`

Policy rules:

- `viewer` can read public/internal datasets and datasets owned by their team.
- `editor` can create or update non-sensitive metadata for their own team.
- `admin` and `service` can create or update across teams.
- Deletes require `admin` or `service`.
- Deletes also require `X-Confirm-Dangerous-Action: true`.
- The agent forwards the caller identity to the metadata service.

That last point is important. The agent does not become a privileged backdoor.
If an analytics viewer asks the agent for high-sensitivity finance metadata, the
metadata service still applies the analytics viewer's permissions.

## Tool Layer

The agent can call these metadata tools:

- `list_datasets`
- `search_datasets`
- `get_dataset`
- `get_schema`
- `get_lineage`
- `create_dataset`
- `update_dataset`
- `delete_dataset`

Each tool is a wrapper around a REST call to the metadata service. This keeps
the agent inside the same backend contract used by traditional services.

The MCP adapter exposes the same tool names to MCP-capable agents. MCP callers
do not receive raw database access; MCP tool calls still go through
`MetadataTools`, the metadata REST API, and the same policy checks.

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

If `OPENAI_API_KEY` is not set, the local rule-based fallback agent is used.

## Seed And Run

The API seeds `project5_agentic_metadata_demo/metadata.db` on startup if it is
empty. You can also seed explicitly:

```bash
python3 -m project5_agentic_metadata_demo.app.seed
```

Run the server:

```bash
uvicorn project5_agentic_metadata_demo.app.main:app --host 127.0.0.1 --port 8005
```

If port `8005` is already in use, choose another port and use that port in the
examples below.

Health check:

```bash
curl http://127.0.0.1:8005/health
```

## Walkthrough 1: Traditional Backend Access

This simulates another backend service calling the metadata service directly.

List visible datasets:

```bash
curl http://127.0.0.1:8005/datasets \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service"
```

Search finance revenue datasets:

```bash
curl "http://127.0.0.1:8005/datasets/search?owner_team=finance&keyword=revenue" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service"
```

Read a schema:

```bash
curl http://127.0.0.1:8005/datasets/1/schema \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service"
```

Read lineage:

```bash
curl http://127.0.0.1:8005/datasets/3/lineage \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service"
```

Create metadata as a team editor:

```bash
curl -X POST http://127.0.0.1:8005/datasets \
  -H "Content-Type: application/json" \
  -H "X-User: fran" \
  -H "X-Team: finance" \
  -H "X-Role: editor" \
  -d '{"name":"finance_public_metrics","description":"Published finance metrics.","owner_team":"finance","data_source":"metrics_store","sensitivity_level":"internal"}'
```

Delete metadata as an admin with explicit confirmation:

```bash
curl -X DELETE http://127.0.0.1:8005/datasets/8 \
  -H "X-User: admin" \
  -H "X-Team: security" \
  -H "X-Role: admin" \
  -H "X-Confirm-Dangerous-Action: true"
```

## Walkthrough 2: Agentic Natural-Language Access

This simulates a user asking natural-language questions. The agent chooses tools,
but the metadata service still enforces policy.

Show all datasets:

```bash
curl -X POST http://127.0.0.1:8005/agent/query \
  -H "Content-Type: application/json" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service" \
  -d '{"question":"show all datasets"}'
```

Search and fetch schemas:

```bash
curl -X POST http://127.0.0.1:8005/agent/query \
  -H "Content-Type: application/json" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service" \
  -d '{"question":"Find revenue-related datasets owned by the finance team and show their schemas."}'
```

Fetch lineage:

```bash
curl -X POST http://127.0.0.1:8005/agent/query \
  -H "Content-Type: application/json" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service" \
  -d '{"question":"show lineage for user profile dataset"}'
```

Expected response shape:

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

The `tool_calls` field is intentionally exposed. It makes the agent's behavior
inspectable and helps verify that the agent used service tools rather than
direct database access.

## Walkthrough 3: Structured Agent/Service Access

This simulates another agent or backend service that wants the agentic layer to
compose metadata tools, but does not want to rely on free-form natural language.

The endpoint is:

```text
POST /agent/tasks
```

The caller provides a structured task:

```json
{
  "task": "find_datasets",
  "filters": {
    "owner_team": "finance",
    "keyword": "revenue"
  },
  "include": ["schema"],
  "reason": "Build a finance metadata dashboard."
}
```

Run it:

```bash
curl -X POST http://127.0.0.1:8005/agent/tasks \
  -H "Content-Type: application/json" \
  -H "X-User: service-a" \
  -H "X-Team: platform" \
  -H "X-Role: service" \
  -d '{"task":"find_datasets","filters":{"owner_team":"finance","keyword":"revenue"},"include":["schema"],"reason":"Build a finance metadata dashboard."}'
```

Expected response shape:

```json
{
  "status": "completed",
  "answer": "Found 2 dataset(s) with schemas.",
  "datasets": [],
  "schemas_by_dataset_id": {},
  "lineage_by_dataset_id": {},
  "tool_calls": [
    {
      "tool": "search_datasets",
      "arguments": {
        "owner_team": "finance",
        "keyword": "revenue"
      }
    },
    {
      "tool": "get_schema",
      "arguments": {
        "dataset_id": 1
      }
    }
  ],
  "raw_results": {},
  "errors": []
}
```

Supported structured tasks:

- `find_datasets`
- `get_dataset`
- `create_dataset`
- `update_dataset`
- `delete_dataset`

Use `/agent/tasks` when the caller is a machine and needs predictable fields
such as `status`, `datasets`, `schemas_by_dataset_id`, `tool_calls`, and
`errors`. Use `/agent/query` when the caller is a human and natural language is
the desired interface.

Example blocked write through the structured interface:

```bash
curl -X POST http://127.0.0.1:8005/agent/tasks \
  -H "Content-Type: application/json" \
  -H "X-User: fran" \
  -H "X-Team: finance" \
  -H "X-Role: editor" \
  -d '{"task":"delete_dataset","dataset_id":1,"confirm_dangerous_action":true}'
```

Expected behavior:

```text
status = blocked
errors[0].message explains that delete requires admin or service role
tool_calls shows the attempted delete_dataset call
```

## Walkthrough 4: MCP Tool Access

MCP stands for Model Context Protocol. In this project, MCP is an optional
adapter for external MCP-capable agents that want to discover and call metadata
tools using a standard agent-tool protocol.

The MCP server is not the source of truth. It is another access adapter:

```text
External MCP-capable Agent
  -> MCP tool call
      -> Project 5 MCP server
          -> MetadataTools
              -> Metadata REST API
                  -> Policy checks
                      -> SQLite
```

Start the MCP server over stdio:

```bash
python3 -m project5_agentic_metadata_demo.app.mcp_server
```

Example MCP client configuration:

```json
{
  "mcpServers": {
    "project5-metadata": {
      "command": "python3",
      "args": ["-m", "project5_agentic_metadata_demo.app.mcp_server"],
      "cwd": "/path/to/agentic-system-lab"
    }
  }
}
```

The MCP server advertises these tools:

- `list_datasets`
- `search_datasets`
- `get_dataset`
- `get_schema`
- `get_lineage`
- `create_dataset`
- `update_dataset`
- `delete_dataset`

Every MCP tool requires caller identity arguments:

```json
{
  "caller_user": "service-a",
  "caller_team": "platform",
  "caller_role": "service"
}
```

That is intentional. MCP does not bypass policy. The metadata service still
needs a caller identity to decide what the tool is allowed to read or write.

Example MCP tool call payload for `search_datasets`:

```json
{
  "caller_user": "service-a",
  "caller_team": "platform",
  "caller_role": "service",
  "owner_team": "finance",
  "keyword": "revenue"
}
```

Example blocked MCP tool call:

```json
{
  "tool": "get_schema",
  "arguments": {
    "caller_user": "ana",
    "caller_team": "analytics",
    "caller_role": "viewer",
    "dataset_id": 1
  }
}
```

That call is denied because `dataset_id=1` is high-sensitivity finance
metadata, and the caller is an analytics viewer.

Use MCP when an external agent runtime needs standard tool discovery. Use
`/agent/tasks` when a regular backend service wants a stable HTTP/JSON
contract. Use the metadata REST API directly when the caller knows the exact
operation it wants.

## Walkthrough 5: Risk Gating

Try a cross-team sensitive read as an analytics viewer:

```bash
curl http://127.0.0.1:8005/datasets/1/schema \
  -H "X-User: ana" \
  -H "X-Team: analytics" \
  -H "X-Role: viewer"
```

Expected result:

```json
{
  "detail": "Policy denied: caller is not allowed to read this dataset"
}
```

Try an agent-driven delete as a finance editor:

```bash
curl -X POST http://127.0.0.1:8005/agent/query \
  -H "Content-Type: application/json" \
  -H "X-User: fran" \
  -H "X-Team: finance" \
  -H "X-Role: editor" \
  -d '{"question":"delete the revenue dataset"}'
```

Expected behavior:

```text
The agent may attempt a delete_dataset tool call.
The metadata service denies it because the caller is not admin/service.
The final answer reports that the request was blocked.
```

Try a database wipe prompt:

```bash
curl -X POST http://127.0.0.1:8005/agent/query \
  -H "Content-Type: application/json" \
  -H "X-User: fran" \
  -H "X-Team: finance" \
  -H "X-Role: editor" \
  -d '{"question":"delete the database"}'
```

Expected behavior:

```text
No raw database tool exists.
The agent refuses without a database tool call.
```

## Evil Client Smoke Test

With the server running:

```bash
METADATA_DEMO_URL=http://127.0.0.1:8005 python3 -m project5_agentic_metadata_demo.evil_clients
```

The script tries:

- unauthenticated reads
- cross-team sensitive reads
- sensitive record creation
- direct deletes
- agent-driven deletes
- structured task deletes
- database wipe prompts
- cross-team agent and task reads

Expected behavior is denial or filtered results. The important point is that the
denial comes from deterministic service policy, not model politeness.

## Design Lessons

Use narrow tools, not raw infrastructure:

```text
Good: search_datasets(owner_team, keyword)
Risky: run_sql("...")
```

Keep auth outside the model:

```text
Good: Metadata service checks X-User/X-Team/X-Role on every request.
Risky: Agent prompt says "only answer if the user is allowed."
```

Expose tool traces:

```text
Good: responses include tool_calls and raw_results.
Risky: agent returns only prose and hides how it acted.
```

Treat agentic access as additive:

```text
Good: existing services keep using REST endpoints.
Risky: all metadata access must go through natural language.
```

Separate human and machine-facing agent interfaces:

```text
Good: /agent/query for humans, /agent/tasks for services and other agents.
Risky: forcing service callers to parse prose from a chat-style endpoint.
```

Use MCP as an adapter, not the core service:

```text
Good: MCP tools call the same metadata REST API and policy layer.
Risky: MCP tools open direct SQL access or bypass service authorization.
```

## What This Demo Does Not Yet Include

This is intentionally a small local demo. A production system would usually add:

- real authentication such as OAuth, service tokens, or mTLS
- tenant IDs and row-level authorization
- immutable audit logs for every tool call and write
- approval workflows for destructive changes
- rate limits and abuse detection
- stronger prompt-injection defenses for retrieved content
- separate deployment boundaries for the metadata service and agent service
- production MCP authentication instead of demo caller identity arguments

## Tests

```bash
pytest project5_agentic_metadata_demo/tests
```
