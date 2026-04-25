from __future__ import annotations

import logging

from app.llm import LlmClient, OpenAILlmClient
from app.models import ChatMessage, PlannerOutput, ToolExecutionResult
from app.return_agents import PlannerAgent, QAAgent, RoutingAgent
from app.return_models import ReturnConversationRequest, ReturnConversationResponse
from app.session_store import JsonSessionStore
from app.tools import BackendTools, validate_and_execute_tool


logger = logging.getLogger(__name__)


class ReturnConversationService:
    def __init__(
        self,
        *,
        session_store: JsonSessionStore | None = None,
        tools: BackendTools | None = None,
        llm_client: LlmClient | None = None,
    ) -> None:
        self.session_store = session_store or JsonSessionStore()
        self.tools = tools or BackendTools()
        self.llm_client = llm_client or OpenAILlmClient()
        self.routing_agent = RoutingAgent(self.llm_client)
        self.planner_agent = PlannerAgent(self.tools.catalog, self.llm_client)
        self.qa_agent = QAAgent(self.llm_client)

    async def handle_message(self, request: ReturnConversationRequest) -> ReturnConversationResponse:
        logger.info(
            "return chatbot user message: session_id=%s user_id=%s message=%s",
            request.session_id,
            request.user_id,
            request.message,
        )
        state = await self.session_store.load(request.session_id, request.user_id)
        state.history.append(ChatMessage(role="user", content=request.message))

        routing = await self.routing_agent.run(request.message, state)
        state.context = routing.extracted_fields

        planner = await self.planner_agent.run(routing, request.user_id)
        logger.info("return chatbot handoff: routing_agent -> planner_agent -> backend_tools")
        tool_results = await self._execute_tool_proposals(planner, user_id=request.user_id)

        logger.info("return chatbot handoff: backend_tools -> qa_agent")
        response = await self.qa_agent.run(routing, planner, tool_results)
        state.history.append(ChatMessage(role="assistant", content=response))
        state.last_status = planner.status
        await self.session_store.save(state)

        logger.info("return chatbot final response: %s", response)
        return ReturnConversationResponse(
            session_id=request.session_id,
            user_id=request.user_id,
            message=request.message,
            response=response,
            routing=routing,
            planner=planner,
            tool_results=tool_results,
        )

    async def _execute_tool_proposals(
        self,
        planner: PlannerOutput,
        *,
        user_id: str,
    ) -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        for proposal in planner.proposed_tool_calls:
            results.append(await validate_and_execute_tool(self.tools, proposal, user_id=user_id))
        return results
