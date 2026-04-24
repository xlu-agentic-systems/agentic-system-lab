from __future__ import annotations

from app.models import ConversationRequest, PlannerOutput, RoutingOutput, ToolExecutionResult


class ReturnConversationRequest(ConversationRequest):
    pass


class ReturnConversationResponse(ConversationRequest):
    response: str
    routing: RoutingOutput
    planner: PlannerOutput
    tool_results: list[ToolExecutionResult]
