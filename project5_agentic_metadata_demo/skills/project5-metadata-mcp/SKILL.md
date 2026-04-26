---
name: project5-metadata-mcp
description: Use this when connecting an MCP-capable agent to Project 5's metadata MCP server, configuring the server command, verifying mcp_servers.project5Metadata, or calling Project 5 metadata MCP tools with policy-aware caller identity fields.
---

# Project 5 Metadata MCP

Use this workflow to connect an MCP-capable agent to the Project 5 metadata
server and verify that policy-enforced metadata tools are available.

## Preconditions

Run commands from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r project5_agentic_metadata_demo/requirements.txt
```

The MCP server initializes and seeds the local SQLite database when tools are
called, so a separate REST API server is not required for MCP-only usage.

## MCP Server Command

Configure the client to launch the stdio MCP server from the repository root:

```json
{
  "mcpServers": {
    "project5Metadata": {
      "command": "python3",
      "args": ["-m", "project5_agentic_metadata_demo.app.mcp_server"],
      "cwd": "/absolute/path/to/agentic-system-lab"
    }
  }
}
```

Some clients may expose this server as `mcp_servers.project5Metadata`.

## Verify Connection

Make a low-risk read call first:

```json
{
  "tool": "list_datasets",
  "arguments": {
    "caller_user": "codex",
    "caller_team": "platform",
    "caller_role": "service"
  }
}
```

A successful response includes seeded datasets such as `revenue_transactions`,
`revenue_forecast`, `product_catalog`, and `web_events`.

## Caller Identity

Every tool call must include:

```json
{
  "caller_user": "service-a",
  "caller_team": "platform",
  "caller_role": "service"
}
```

Valid roles are `viewer`, `editor`, `admin`, and `service`.

This is intentional: MCP does not bypass metadata policy. The MCP server passes
identity fields to the same deterministic service policy used by the REST API.

## Tool Selection

- Use `list_datasets` or `search_datasets` before reading specific metadata.
- Use `get_dataset`, `get_schema`, or `get_lineage` only after identifying the
  target dataset ID.
- Use `create_dataset` and `update_dataset` only when the requested write is
  allowed for the caller's team and role.
- Use `delete_dataset` only with explicit user intent and
  `confirm_dangerous_action: true`.

Expected policy check: an analytics viewer reading schema for finance
high-sensitivity dataset `1` should be denied.
