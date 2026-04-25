from __future__ import annotations

from fastapi import FastAPI, HTTPException

from agentic_system_lab.observability import configure_observability_logging
from project3_adaptive_eval_system.app.models import (
    EvaluationRunRequest,
    EvaluationRunResult,
    Project1FeedbackRunRequest,
    Project1FeedbackRunResult,
    Project1TraceImportResult,
    PromptPatch,
    PromptPatchReviewRequest,
)
from project3_adaptive_eval_system.app.service import EvaluationService


configure_observability_logging(project="project3")

app = FastAPI(title="Adaptive Evaluation System")
service = EvaluationService()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/evaluations/run", response_model=EvaluationRunResult)
async def run_evaluation(request: EvaluationRunRequest) -> EvaluationRunResult:
    return await service.run_evaluation(request.trace_limit)


@app.post("/project1/import-traces", response_model=Project1TraceImportResult)
async def import_project1_traces(request: Project1FeedbackRunRequest) -> Project1TraceImportResult:
    return service.import_project1_traces(
        trace_path=request.trace_path,
        trace_limit=request.trace_limit,
        include_loki_context=request.include_loki_context,
        loki_since_minutes=request.loki_since_minutes,
    )


@app.post("/project1/feedback/run", response_model=Project1FeedbackRunResult)
async def run_project1_feedback(request: Project1FeedbackRunRequest) -> Project1FeedbackRunResult:
    return await service.run_project1_feedback(
        trace_path=request.trace_path,
        trace_limit=request.trace_limit,
        include_loki_context=request.include_loki_context,
        loki_since_minutes=request.loki_since_minutes,
    )


@app.post("/prompt-patches/review", response_model=PromptPatch)
async def review_prompt_patch(request: PromptPatchReviewRequest) -> PromptPatch:
    try:
        return service.review_prompt_patch(request.patch_id, request.approved)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
