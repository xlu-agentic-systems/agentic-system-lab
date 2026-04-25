from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel

from project3_adaptive_eval_system.app.models import (
    ConversationTrace,
    GeneratedTestCase,
    PromptPatch,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRACE_PATH = PROJECT_ROOT / "sample_traces" / "traces.jsonl"
DEFAULT_GENERATED_TEST_PATH = PROJECT_ROOT / "generated_tests" / "regression_cases.jsonl"
DEFAULT_PROMPT_PATCH_PATH = PROJECT_ROOT / "prompt_patches.jsonl"

T = TypeVar("T", bound=BaseModel)


class JsonlStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def read(self, model: type[T]) -> list[T]:
        if not self.path.exists():
            return []
        records: list[T] = []
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            records.append(model.model_validate_json(line))
        return records

    def write_all(self, records: Iterable[BaseModel]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(record.model_dump_json() for record in records)
        self.path.write_text(f"{payload}\n" if payload else "")


class ConversationTraceStore(JsonlStore):
    def __init__(self, path: Path | str = DEFAULT_TRACE_PATH) -> None:
        super().__init__(path)

    def load_traces(self, limit: int | None = None) -> list[ConversationTrace]:
        if limit is not None and limit < 0:
            raise ValueError("trace limit must be non-negative")
        traces = self.read(ConversationTrace)
        return traces[:limit] if limit is not None else traces


class GeneratedTestStore(JsonlStore):
    def __init__(self, path: Path | str = DEFAULT_GENERATED_TEST_PATH) -> None:
        super().__init__(path)

    def save_tests(self, tests: list[GeneratedTestCase]) -> None:
        self.write_all(tests)
        _write_python_regression_file(tests, self.path.parent / "test_generated_regressions.py")


class PromptPatchStore(JsonlStore):
    def __init__(self, path: Path | str = DEFAULT_PROMPT_PATCH_PATH) -> None:
        super().__init__(path)

    def save_patches(self, patches: list[PromptPatch]) -> list[PromptPatch]:
        existing_statuses = {patch.patch_id: patch.status for patch in self.read(PromptPatch)}
        preserved = [
            patch.model_copy(update={"status": existing_statuses[patch.patch_id]})
            if patch.patch_id in existing_statuses and existing_statuses[patch.patch_id] != "proposed"
            else patch
            for patch in patches
        ]
        self.write_all(preserved)
        return preserved

    def review_patch(self, patch_id: str, approved: bool) -> PromptPatch:
        patches = self.read(PromptPatch)
        for index, patch in enumerate(patches):
            if patch.patch_id != patch_id:
                continue
            reviewed = patch.model_copy(update={"status": "approved" if approved else "rejected"})
            patches[index] = reviewed
            self.write_all(patches)
            return reviewed
        raise KeyError(f"prompt patch not found: {patch_id}")


def _write_python_regression_file(tests: list[GeneratedTestCase], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '"""Generated regression cases from Project 3 evaluation traces."""',
        "",
        "import asyncio",
        "import json",
        "from pathlib import Path",
        "",
        "from project1_multi_agent_return_bot.app.catalog import Catalog",
        "from project1_multi_agent_return_bot.app.models import ToolCallArgs, ToolCallProposal",
        "from project1_multi_agent_return_bot.app.policy import check_return_policy",
        "from project1_multi_agent_return_bot.app.tools import BackendTools, validate_and_execute_tool",
        "",
        "",
        "def _cases():",
        "    path = Path(__file__).with_name('regression_cases.jsonl')",
        "    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]",
        "",
        "",
        "def _run(coro):",
        "    return asyncio.run(coro)",
        "",
        "",
        "def test_generated_regression_cases_have_expected_behavior():",
        "    cases = _cases()",
        "    if not cases:",
        "        return",
        "    for case in cases:",
        "        assert case['expected_behavior']",
        "        assert case['assertions']",
        "        assertion_text = ' '.join(case['assertions']).lower()",
        "        assert any(term in assertion_text for term in ('policy', 'validation', 'ownership', 'delivery date'))",
        "",
        "",
        "def test_delivery_date_regression_replays_policy_behavior():",
        "    cases = {case['trace_id']: case for case in _cases()}",
        "    if 'trace-bad-purchase-date' not in cases:",
        "        return",
        "    catalog = Catalog()",
        "    order = catalog.get_order('order-1002')",
        "    assert order is not None",
        "    order.delivered_at = catalog.today",
        "    result = check_return_policy(catalog, 'order-1002', 'item-3')",
        "    assert result.eligible is True",
        "    assert 'return_window_expired' not in result.reason_codes",
        "",
        "",
        "def test_unsafe_refund_regression_replays_ownership_gate():",
        "    cases = {case['trace_id']: case for case in _cases()}",
        "    if 'trace-bad-unsafe-refund' not in cases:",
        "        return",
        "    tools = BackendTools(Catalog())",
        "    proposal = ToolCallProposal(",
        "        name='issue_refund',",
        "        args=ToolCallArgs(order_id='order-2001', item_id='item-4', amount='129.99'),",
        "        safety='unsafe_write',",
        "        reason='generated regression ownership check',",
        "    )",
        "    result = _run(validate_and_execute_tool(tools, proposal, user_id='user-1'))",
        "    assert result.executed is False",
        "    assert result.ok is False",
        "    assert 'user does not own order' in (result.error or '')",
        "    assert tools.refunds == []",
        "",
    ]
    path.write_text("\n".join(lines))
