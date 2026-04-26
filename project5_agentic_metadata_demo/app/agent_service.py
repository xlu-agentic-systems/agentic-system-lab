from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request

from project5_agentic_metadata_demo.app.auth import Principal, get_current_principal
from project5_agentic_metadata_demo.app.llm_agent import run_openai_agent
from project5_agentic_metadata_demo.app.rule_based_agent import run_rule_based_agent
from project5_agentic_metadata_demo.app.schemas import AgentQueryRequest, AgentQueryResponse, AgentTaskRequest, AgentTaskResponse
from project5_agentic_metadata_demo.app.structured_agent import run_structured_task
from project5_agentic_metadata_demo.app.tools import MetadataTools


router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/query", response_model=AgentQueryResponse)
async def query_agent(
    request_body: AgentQueryRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> AgentQueryResponse:
    tools = _metadata_tools_for_request(request, principal)
    if os.getenv("OPENAI_API_KEY"):
        return await run_openai_agent(request_body.question, tools)
    return await run_rule_based_agent(request_body.question, tools)


@router.post("/tasks", response_model=AgentTaskResponse)
async def run_agent_task(
    request_body: AgentTaskRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> AgentTaskResponse:
    return await run_structured_task(request_body, _metadata_tools_for_request(request, principal))


def _metadata_tools_for_request(request: Request, principal: Principal) -> MetadataTools:
    headers = {
        "X-User": principal.user,
        "X-Team": principal.team,
        "X-Role": principal.role,
    }
    if request.headers.get("X-Confirm-Dangerous-Action"):
        headers["X-Confirm-Dangerous-Action"] = request.headers["X-Confirm-Dangerous-Action"]
    return MetadataTools(request.app, headers=headers)
