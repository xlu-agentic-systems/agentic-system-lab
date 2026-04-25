import pytest

from project4_agentic_project_copilot.app.sql_safety import SqlSafetyError, validate_read_only_sql


def test_sql_safety_allows_select() -> None:
    assert validate_read_only_sql("SELECT task_id, title FROM tasks;") == "SELECT task_id, title FROM tasks"


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM tasks",
        "UPDATE tasks SET status = 'done'",
        "DROP TABLE tasks",
        "INSERT INTO tasks(title) VALUES ('x')",
        "SELECT * FROM tasks; DELETE FROM tasks",
        "PRAGMA table_info(tasks)",
    ],
)
def test_sql_safety_blocks_destructive_sql(sql: str) -> None:
    with pytest.raises(SqlSafetyError):
        validate_read_only_sql(sql)
