from __future__ import annotations

import logging

from agentic_system_lab.observability import log_agent_event
from project1_multi_agent_return_bot.app.llm import LlmClient, OpenAILlmClient
from project1_multi_agent_return_bot.app.models import ChatMessage, PlannerOutput, ToolExecutionResult
from project1_multi_agent_return_bot.app.return_agents import PlannerAgent, QAAgent, RoutingAgent
from project1_multi_agent_return_bot.app.return_models import ReturnConversationRequest, ReturnConversationResponse
from project1_multi_agent_return_bot.app.session_store import JsonSessionStore
from project1_multi_agent_return_bot.app.tools import BackendTools, validate_and_execute_tool
from project1_multi_agent_return_bot.app.trace_store import JsonlTraceStore, ReturnConversationTrace


logger = logging.getLogger(__name__)


class ReturnConversationService:
    def __init__(
        self,
        *,
        session_store: JsonSessionStore | None = None,
        trace_store: JsonlTraceStore | None = None,
        tools: BackendTools | None = None,
        llm_client: LlmClient | None = None,
    ) -> None:
        self.session_store = session_store or JsonSessionStore()
        self.trace_store = trace_store or JsonlTraceStore()
        self.tools = tools or BackendTools()
        self.llm_client = llm_client or OpenAILlmClient()
        self.routing_agent = RoutingAgent(self.llm_client)
        self.planner_agent = PlannerAgent(self.tools.catalog, self.llm_client)
        self.qa_agent = QAAgent(self.llm_client)

    async def handle_message(self, request: ReturnConversationRequest) -> ReturnConversationResponse:
        logger.info(
            "return chatbot user message received: session_id=%s user_id=%s message_length=%s",
            request.session_id,
            request.user_id,
            len(request.message),
        )
        state = await self.session_store.load(request.session_id, request.user_id)
        state.history.append(ChatMessage(role="user", content=request.message))
        log_agent_event(
            logger,
            event="user_message",
            message="project1 user message received",
            session_id=request.session_id,
            user_id=request.user_id,
            attributes={"message_length": len(request.message)},
        )

        routing = await self.routing_agent.run(request.message, state)
        state.context = routing.extracted_fields
        log_agent_event(
            logger,
            event="agent_decision",
            message="project1 routing agent decision",
            agent="routing_agent",
            session_id=request.session_id,
            user_id=request.user_id,
            status=routing.intent,
            attributes={
                "missing_fields": routing.missing_fields,
                "has_clarification": bool(routing.clarification_question),
            },
        )

        planner = await self.planner_agent.run(routing, request.user_id)
        log_agent_event(
            logger,
            event="agent_decision",
            message="project1 planner agent decision",
            agent="planner_agent",
            session_id=request.session_id,
            user_id=request.user_id,
            status=planner.status,
            attributes={
                "reason_codes": planner.reason_codes,
                "proposed_tool_count": len(planner.proposed_tool_calls),
            },
        )
        logger.info("return chatbot handoff: routing_agent -> planner_agent -> backend_tools")
        tool_results = await self._execute_tool_proposals(planner, user_id=request.user_id)
        for result in tool_results:
            log_agent_event(
                logger,
                event="tool_execution",
                message="project1 backend tool validation result",
                session_id=request.session_id,
                user_id=request.user_id,
                tool_name=result.proposal.name,
                status="executed" if result.executed else "blocked",
                attributes={"ok": result.ok, "error": result.error},
            )

        logger.info("return chatbot handoff: backend_tools -> qa_agent")
        response = await self.qa_agent.run(routing, planner, tool_results)
        log_agent_event(
            logger,
            event="agent_decision",
            message="project1 qa agent response generated",
            agent="qa_agent",
            session_id=request.session_id,
            user_id=request.user_id,
            status=planner.status,
            attributes={"response_length": len(response)},
        )
        state.history.append(ChatMessage(role="assistant", content=response))
        state.last_status = planner.status
        await self.session_store.save(state)
        await self.trace_store.append(
            ReturnConversationTrace(
                session_id=request.session_id,
                user_id=request.user_id,
                user_message=request.message,
                response=response,
                routing=routing,
                planner=planner,
                tool_results=tool_results,
            )
        )

        logger.info("return chatbot final response generated: length=%s", len(response))
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
