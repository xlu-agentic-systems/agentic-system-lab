from __future__ import annotations

import os

from fastapi import APIRouter, Request

from project5_agentic_metadata_demo.app.llm_agent import run_openai_agent
from project5_agentic_metadata_demo.app.rule_based_agent import run_rule_based_agent
from project5_agentic_metadata_demo.app.schemas import AgentQueryRequest, AgentQueryResponse
from project5_agentic_metadata_demo.app.tools import MetadataTools


router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/query", response_model=AgentQueryResponse)
async def query_agent(request_body: AgentQueryRequest, request: Request) -> AgentQueryResponse:
    tools = MetadataTools(request.app)
    if os.getenv("OPENAI_API_KEY"):
        return await run_openai_agent(request_body.question, tools)
    return await run_rule_based_agent(request_body.question, tools)

