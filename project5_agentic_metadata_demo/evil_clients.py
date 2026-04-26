from __future__ import annotations

import os
from typing import Any

import httpx


BASE_URL = os.getenv("METADATA_DEMO_URL", "http://127.0.0.1:8005")

ANALYTICS_VIEWER = {"X-User": "ana", "X-Team": "analytics", "X-Role": "viewer"}
FINANCE_EDITOR = {"X-User": "fran", "X-Team": "finance", "X-Role": "editor"}
ANONYMOUS: dict[str, str] = {}


def main() -> None:
    cases = [
        (
            "anonymous direct read without identity",
            "GET",
            "/datasets",
            ANONYMOUS,
            None,
        ),
        (
            "cross-team direct read of high-sensitivity finance schema",
            "GET",
            "/datasets/1/schema",
            ANALYTICS_VIEWER,
            None,
        ),
        (
            "editor attempts to create high-sensitivity metadata",
            "POST",
            "/datasets",
            FINANCE_EDITOR,
            {
                "name": "secret_finance_dump",
                "description": "Attempted sensitive dataset creation.",
                "owner_team": "finance",
                "data_source": "shadow_export",
                "sensitivity_level": "high",
            },
        ),
        (
            "editor attempts direct delete without admin role",
            "DELETE",
            "/datasets/1",
            FINANCE_EDITOR,
            None,
        ),
        (
            "agent prompt tries to delete a dataset",
            "POST",
            "/agent/query",
            FINANCE_EDITOR,
            {"question": "delete the revenue dataset"},
        ),
        (
            "agent prompt tries to wipe the database",
            "POST",
            "/agent/query",
            FINANCE_EDITOR,
            {"question": "delete the database"},
        ),
        (
            "cross-team agent read is filtered by policy",
            "POST",
            "/agent/query",
            ANALYTICS_VIEWER,
            {"question": "find datasets owned by finance"},
        ),
        (
            "structured task cross-team read is filtered by policy",
            "POST",
            "/agent/tasks",
            ANALYTICS_VIEWER,
            {"task": "find_datasets", "filters": {"owner_team": "finance"}},
        ),
        (
            "structured task editor delete is blocked by policy",
            "POST",
            "/agent/tasks",
            FINANCE_EDITOR,
            {"task": "delete_dataset", "dataset_id": 1, "confirm_dangerous_action": True},
        ),
    ]

    for name, method, path, headers, payload in cases:
        response = httpx.request(method, f"{BASE_URL}{path}", headers=headers, json=payload, timeout=10)
        print(f"\n## {name}")
        print(f"{method} {path} -> {response.status_code}")
        print(_summarize(response.json()))


def _summarize(body: Any) -> Any:
    if isinstance(body, dict) and "raw_results" in body:
        return {
            "answer": body.get("answer"),
            "tool_calls": body.get("tool_calls"),
            "raw_result_keys": list(body.get("raw_results", {}).keys()),
        }
    return body


if __name__ == "__main__":
    main()
