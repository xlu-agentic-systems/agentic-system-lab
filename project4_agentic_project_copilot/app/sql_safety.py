from __future__ import annotations

import re


BLOCKED_SQL_TERMS = {
    "alter",
    "attach",
    "create",
    "delete",
    "detach",
    "drop",
    "insert",
    "pragma",
    "replace",
    "truncate",
    "update",
    "vacuum",
}


class SqlSafetyError(ValueError):
    pass


def validate_read_only_sql(sql: str) -> str:
    normalized = sql.strip()
    if not normalized:
        raise SqlSafetyError("SQL is empty.")
    if ";" in normalized.rstrip(";"):
        raise SqlSafetyError("Only one SQL statement is allowed.")
    normalized = normalized.rstrip(";").strip()
    first_token = normalized.split(maxsplit=1)[0].lower()
    if first_token not in {"select", "with"}:
        raise SqlSafetyError("Only SELECT/CTE read-only SQL is allowed.")
    tokens = set(re.findall(r"[a-z_]+", normalized.lower()))
    blocked = sorted(tokens & BLOCKED_SQL_TERMS)
    if blocked:
        raise SqlSafetyError(f"Blocked destructive SQL terms: {', '.join(blocked)}")
    return normalized
