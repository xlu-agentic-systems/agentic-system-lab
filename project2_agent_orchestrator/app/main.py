from __future__ import annotations

import logging

from fastapi import FastAPI

from project2_agent_orchestrator.app.models import ConversationRequest, ConversationResponse
from project2_agent_orchestrator.app.service import ConversationService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="Customer Support Agent Orchestrator")
service = ConversationService()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ConversationResponse)
async def chat(request: ConversationRequest) -> ConversationResponse:
    return await service.handle_message(request)
