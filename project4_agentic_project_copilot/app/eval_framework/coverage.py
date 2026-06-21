from __future__ import annotations

from collections.abc import Sequence

from project4_agentic_project_copilot.app.eval_framework.models import (
    CoverageBucket,
    EvaluationCase,
    EvaluationCoverage,
    EvaluationResult,
)


def build_coverage(cases: Sequence[EvaluationCase], results: Sequence[EvaluationResult]) -> EvaluationCoverage:
    passed_by_case_id = {result.case_id: result.passed for result in results}
    routes: dict[str, CoverageBucket] = {}
    data_sources: dict[str, CoverageBucket] = {}
    tools: dict[str, CoverageBucket] = {}
    workflow_statuses: dict[str, CoverageBucket] = {}
    workflow_types: dict[str, CoverageBucket] = {}
    confirmation = CoverageBucket()
    sql_refusal = CoverageBucket()

    for case in cases:
        passed = passed_by_case_id.get(case.case_id, False)
        _add_bucket(routes, case.expected_tool_choice, case.case_id, passed)
        _add_bucket(data_sources, case.expected_data_source, case.case_id, passed)
        _add_bucket(tools, case.expected_tool_name, case.case_id, passed)
        _add_bucket(workflow_statuses, case.expected_workflow_status, case.case_id, passed)
        _add_bucket(workflow_types, case.expected_workflow_type, case.case_id, passed)
        if _expects_confirmation(case):
            _add_to_bucket(confirmation, case.case_id, passed)
        if case.expected_sql_refused:
            _add_to_bucket(sql_refusal, case.case_id, passed)

    return EvaluationCoverage(
        routes=_sorted_buckets(routes),
        data_sources=_sorted_buckets(data_sources),
        tools=_sorted_buckets(tools),
        workflow_statuses=_sorted_buckets(workflow_statuses),
        workflow_types=_sorted_buckets(workflow_types),
        confirmation=confirmation,
        sql_refusal=sql_refusal,
    )


def _expects_confirmation(case: EvaluationCase) -> bool:
    if case.expected_requires_confirmation is not None:
        return case.expected_requires_confirmation
    return case.confirm_action


def _add_bucket(
    buckets: dict[str, CoverageBucket],
    key: str | None,
    case_id: str,
    passed: bool,
) -> None:
    if key is None:
        return
    _add_to_bucket(buckets.setdefault(key, CoverageBucket()), case_id, passed)


def _add_to_bucket(bucket: CoverageBucket, case_id: str, passed: bool) -> None:
    bucket.total += 1
    bucket.passed += int(passed)
    bucket.case_ids.append(case_id)


def _sorted_buckets(buckets: dict[str, CoverageBucket]) -> dict[str, CoverageBucket]:
    return dict(sorted(buckets.items()))
