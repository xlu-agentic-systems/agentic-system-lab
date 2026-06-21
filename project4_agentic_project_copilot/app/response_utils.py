from __future__ import annotations

from project4_agentic_project_copilot.app.models import ChatRequest, ChatResponse, SessionContext
from project4_agentic_project_copilot.app.session_store import append_turn


def build_chat_response(
    request: ChatRequest,
    context: SessionContext,
    *,
    response: str,
    log,
    **kwargs,
) -> ChatResponse:
    append_turn(context, role="assistant", content=response)
    return ChatResponse(
        session_id=request.session_id,
        response=response,
        context=context,
        decision_log=log,
        **kwargs,
    )
