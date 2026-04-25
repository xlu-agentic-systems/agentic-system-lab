from __future__ import annotations

from fastapi import FastAPI

from agentic_system_lab.observability import configure_observability_logging
from project2_agent_orchestrator.app.models import ConversationRequest, ConversationResponse
from project2_agent_orchestrator.app.service import ConversationService


configure_observability_logging(project="project2")

app = FastAPI(title="Customer Support Agent Orchestrator")
service = ConversationService()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ConversationResponse)
async def chat(request: ConversationRequest) -> ConversationResponse:
    return await service.handle_message(request)
