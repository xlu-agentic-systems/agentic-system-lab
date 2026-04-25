from __future__ import annotations

import argparse
import json

from agentic_system_lab.observability.tools import LokiLogQueryTool


def main() -> None:
    parser = argparse.ArgumentParser(description="Query agentic-system logs from Loki")
    parser.add_argument("--project", required=True, help="Project label, for example project1")
    parser.add_argument("--session-id")
    parser.add_argument("--agent")
    parser.add_argument("--event")
    parser.add_argument("--tool-name")
    parser.add_argument("--since-minutes", type=int, default=15)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--allow-broad-query",
        action="store_true",
        help="Developer-only escape hatch for project-wide local debugging without session_id.",
    )
    args = parser.parse_args()

    result = LokiLogQueryTool(require_session_scope=not args.allow_broad_query).query_agent_events(
        project=args.project,
        session_id=args.session_id,
        agent=args.agent,
        event=args.event,
        tool_name=args.tool_name,
        since_minutes=args.since_minutes,
        limit=args.limit,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
