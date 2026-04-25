from __future__ import annotations

from fastapi import FastAPI

from agentic_system_lab.observability import configure_observability_logging
from project1_multi_agent_return_bot.app.return_models import ReturnConversationRequest, ReturnConversationResponse
from project1_multi_agent_return_bot.app.return_service import ReturnConversationService


configure_observability_logging(project="project1")

app = FastAPI(title="E-commerce Return Chatbot")
return_service = ReturnConversationService()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/returns/chat", response_model=ReturnConversationResponse)
async def returns_chat(request: ReturnConversationRequest) -> ReturnConversationResponse:
    return await return_service.handle_message(request)
