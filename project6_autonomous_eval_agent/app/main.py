from __future__ import annotations

from fastapi import FastAPI

from agentic_system_lab.observability import configure_observability_logging
from project6_autonomous_eval_agent.app.models import AutonomousRunRequest, AutonomousRunResult
from project6_autonomous_eval_agent.app.service import AutonomousEvalService


configure_observability_logging(project="project6")

app = FastAPI(title="Autonomous Evaluation Agent")
service = AutonomousEvalService()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/autonomous-runs", response_model=AutonomousRunResult)
async def run_autonomous_evaluation(request: AutonomousRunRequest) -> AutonomousRunResult:
    return await service.run(goal=request.goal, use_rule_based=request.use_rule_based)
