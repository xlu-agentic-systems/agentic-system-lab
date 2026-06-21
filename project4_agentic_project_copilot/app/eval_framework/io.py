from __future__ import annotations

from pathlib import Path

from project4_agentic_project_copilot.app.eval_framework.models import EvaluationCase


EVAL_CASES_PATH = Path(__file__).resolve().parents[2] / "evals" / "cases.jsonl"


def load_cases(path: Path | str = EVAL_CASES_PATH) -> list[EvaluationCase]:
    cases = [
        EvaluationCase.model_validate_json(line)
        for line in Path(path).read_text().splitlines()
        if line.strip()
    ]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            duplicates.add(case.case_id)
        seen.add(case.case_id)
    if duplicates:
        duplicate_list = ", ".join(sorted(duplicates))
        raise ValueError(f"Duplicate eval case_id values: {duplicate_list}")
    return cases
