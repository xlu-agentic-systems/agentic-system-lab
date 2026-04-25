from __future__ import annotations

from pydantic import BaseModel

from project1_multi_agent_return_bot.app.models import ConversationRequest, PlannerOutput, RoutingOutput, ToolExecutionResult


class ReturnConversationRequest(ConversationRequest):
    pass


class ReturnConversationResponse(ConversationRequest):
    response: str
    routing: RoutingOutput
    planner: PlannerOutput
    tool_results: list[ToolExecutionResult]


class QAOutput(BaseModel):
    response: str
