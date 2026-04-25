from __future__ import annotations

import logging

from fastapi import FastAPI

from app.return_models import ReturnConversationRequest, ReturnConversationResponse
from app.return_service import ReturnConversationService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="E-commerce Return Chatbot")
return_service = ReturnConversationService()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/returns/chat", response_model=ReturnConversationResponse)
async def returns_chat(request: ReturnConversationRequest) -> ReturnConversationResponse:
    return await return_service.handle_message(request)
