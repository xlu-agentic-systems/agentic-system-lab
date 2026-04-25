from __future__ import annotations

import asyncio
import logging
import re

from project2_agent_orchestrator.app.llm import LlmClient, OpenAILlmClient
from project2_agent_orchestrator.app.models import (
    AgentName,
    AgentResult,
    ChatMessage,
    ConversationRequest,
    ConversationResponse,
    ExecutionPlan,
    ExecutionStep,
    OrchestratorDecision,
    ProposedAction,
    ReturnContext,
    TraceEvent,
)
from project2_agent_orchestrator.app.session_store import JsonSessionStore
from project2_agent_orchestrator.app.tools import BackendTools, validate_and_execute_action


logger = logging.getLogger(__name__)

ORDER_RE = re.compile(r"\border-\d+\b", re.IGNORECASE)
ITEM_RE = re.compile(r"\bitem-\d+\b", re.IGNORECASE)


class ReturnAgent:
    name = "return_agent"

    def __init__(self, tools: BackendTools, llm_client: LlmClient) -> None:
        self.tools = tools
        self.llm_client = llm_client

    async def run(self, message: str, user_id: str, context: ReturnContext) -> AgentResult:
        resolved = await self.tools.resolve_order_item(
            user_id=user_id,
            order_id=context.order_id,
            item_id=context.item_id,
            product_hint=context.product_hint,
        )
        eligibility = None
        if "error" not in resolved:
            order = resolved["order"]
            item = resolved["item"]
            eligibility = await self.tools.check_return_eligibility(order["order_id"], item["item_id"])
        result = await self.llm_client.parse(
            task_name=self.name,
            system_prompt=_specialist_prompt(
                self.name,
                "Assess return eligibility from backend facts. Propose start_return_authorization only "
                "when eligibility.eligible is true. Do not claim that a refund or return has executed.",
            ),
            user_payload={
                "message": message,
                "context": context.model_dump(mode="json"),
                "resolved": resolved,
                "eligibility": eligibility,
            },
            response_model=AgentResult,
        )
        return _normalize_return_result(result, resolved, eligibility)


class ShippingAgent:
    name = "shipping_agent"

    def __init__(self, tools: BackendTools, llm_client: LlmClient) -> None:
        self.tools = tools
        self.llm_client = llm_client

    async def run(self, message: str, user_id: str, context: ReturnContext) -> AgentResult:
        status = await self.tools.get_shipping_status(user_id, context.order_id)
        return await self.llm_client.parse(
            task_name=self.name,
            system_prompt=_specialist_prompt(
                self.name,
                "Assess package status from backend shipping facts. Escalate delayed, lost, or missing shipments.",
            ),
            user_payload={"message": message, "context": context.model_dump(mode="json"), "shipping_status": status},
            response_model=AgentResult,
        )


class PaymentAgent:
    name = "payment_agent"

    def __init__(self, tools: BackendTools, llm_client: LlmClient) -> None:
        self.tools = tools
        self.llm_client = llm_client

    async def run(self, message: str, user_id: str, context: ReturnContext) -> AgentResult:
        text = message.lower()
        timeline = None
        if any(term in text for term in ("timeline", "how long", "when will", "refund status")):
            timeline = await self.tools.get_refund_timeline()
        duplicates = await self.tools.find_duplicate_charges(user_id, context.order_id)
        return await self.llm_client.parse(
            task_name=self.name,
            system_prompt=_specialist_prompt(
                self.name,
                "Assess payment facts. Unsafe payment changes may only be proposed, never described as "
                "executed. If duplicate charges are present, propose refund_duplicate_charge with "
                "requires_approval=true.",
            ),
            user_payload={
                "message": message,
                "context": context.model_dump(mode="json"),
                "duplicate_charge_result": duplicates,
                "refund_timeline": timeline,
            },
            response_model=AgentResult,
        )


class AccountAgent:
    name = "account_agent"

    def __init__(self, tools: BackendTools, llm_client: LlmClient) -> None:
        self.tools = tools
        self.llm_client = llm_client

    async def run(self, message: str, user_id: str, context: ReturnContext) -> AgentResult:
        profile = await self.tools.get_account_profile(user_id)
        return await self.llm_client.parse(
            task_name=self.name,
            system_prompt=_specialist_prompt(
                self.name,
                "Assess account/profile questions. Sensitive account or payment-method changes require "
                "escalation and identity verification; do not claim changes were made.",
            ),
            user_payload={"message": message, "context": context.model_dump(mode="json"), "profile": profile},
            response_model=AgentResult,
        )


class EscalationAgent:
    name = "escalation_agent"

    def __init__(self, llm_client: LlmClient) -> None:
        self.llm_client = llm_client

    async def run(
        self,
        message: str,
        user_id: str,
        context: ReturnContext,
        prior_results: list[AgentResult],
    ) -> AgentResult:
        low_confidence = [result.agent for result in prior_results if result.confidence < 0.6]
        needs_escalation = [result.agent for result in prior_results if result.needs_escalation]
        reasons = [code for result in prior_results for code in result.reason_codes]
        reason = f"Customer request: {message}; reason codes: {', '.join(reasons) or 'unknown'}"
        result = await self.llm_client.parse(
            task_name=self.name,
            system_prompt=_specialist_prompt(
                self.name,
                "Decide how to explain a human escalation. If escalation is needed, propose "
                "create_support_ticket with safety=safe_write and requires_approval=false.",
            ),
            user_payload={
                "message": message,
                "context": context.model_dump(mode="json"),
                "low_confidence_agents": low_confidence,
                "escalating_agents": needs_escalation,
                "prior_reason_codes": reasons,
                "prior_results": [result.model_dump(mode="json") for result in prior_results],
                "ticket_reason": reason,
            },
            response_model=AgentResult,
        )
        result.agent = self.name
        if not any(action.name == "create_support_ticket" for action in result.proposed_actions):
            result.proposed_actions.append(
                ProposedAction(
                    name="create_support_ticket",
                    args={"reason": reason},
                    safety="safe_write",
                    requires_approval=False,
                    reason="The orchestrator determined this request needs human review.",
                )
            )
        return result


class ConversationService:
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
        self.agent_map = {
            "return_agent": ReturnAgent(self.tools, self.llm_client),
            "shipping_agent": ShippingAgent(self.tools, self.llm_client),
            "payment_agent": PaymentAgent(self.tools, self.llm_client),
            "account_agent": AccountAgent(self.tools, self.llm_client),
            "escalation_agent": EscalationAgent(self.llm_client),
        }

    async def handle_message(self, request: ConversationRequest) -> ConversationResponse:
        trace: list[TraceEvent] = []
        state = await self.session_store.load(request.session_id, request.user_id)
        state.history.append(ChatMessage(role="user", content=request.message))
        base_context = _merge_context(state.context, request.message)
        decision = await self._route_with_llm(request.message, base_context, state.history)
        context = _merge_context(base_context, request.message, decision.extracted_context)
        state.context = context
        trace.append(
            _trace(
                "context",
                "Loaded isolated session context and merged LLM-extracted fields.",
                context.model_dump(),
            )
        )

        selected_agents = _normalize_selected_agents(decision.selected_agents)
        plan = self._build_execution_plan(request.message, selected_agents, decision.execution_mode)
        trace.append(
            _trace(
                "routing",
                "LLM orchestrator selected specialist agents and execution mode.",
                {
                    "selected_agents": selected_agents,
                    "mode": plan.mode,
                    "llm_reasoning": decision.reasoning,
                },
            )
        )
        logger.info(
            "orchestrator decision: session_id=%s agents=%s mode=%s reason=%s",
            request.session_id,
            selected_agents,
            plan.mode,
            plan.reason,
        )

        agent_results = await self._execute_plan(plan, request.message, request.user_id, context, trace)
        agent_results = await self._maybe_escalate(
            request.message,
            request.user_id,
            context,
            selected_agents,
            agent_results,
            trace,
        )
        await self._execute_backend_actions(agent_results, request.user_id, trace)

        final_response = _aggregate_response(agent_results)
        reasoning = _reasoning(selected_agents, plan, agent_results)
        state.history.append(ChatMessage(role="assistant", content=final_response))
        state.last_selected_agents = selected_agents
        await self.session_store.save(state)

        logger.info("orchestrator final response: %s", final_response)
        return ConversationResponse(
            session_id=request.session_id,
            selected_agents=selected_agents,
            execution_plan=plan,
            agent_results=agent_results,
            final_response=final_response,
            reasoning=reasoning,
            trace=trace,
        )

    async def _route_with_llm(
        self,
        message: str,
        context: ReturnContext,
        history: list[ChatMessage],
    ) -> OrchestratorDecision:
        return await self.llm_client.parse(
            task_name="orchestrator_routing",
            system_prompt=(
                "You are the orchestrator for a customer-support agent system. Select one or more "
                "specialist agents from return_agent, shipping_agent, payment_agent, account_agent, "
                "and escalation_agent. Choose execution_mode as single_agent, sequential, or parallel. "
                "Extract obvious order_id, item_id, return_reason, refund_requested, and product_hint values. "
                "Escalate unknown, low-confidence, or sensitive account/payment-change requests. Return only "
                "the structured OrchestratorDecision."
            ),
            user_payload={
                "message": message,
                "current_context": context.model_dump(mode="json"),
                "recent_history": [item.model_dump(mode="json") for item in history[-6:]],
            },
            response_model=OrchestratorDecision,
        )

    def _select_agents(self, message: str) -> list[AgentName]:
        text = message.lower()
        selected: list[AgentName] = []
        if any(term in text for term in ("return", "exchange", "refund the item", "send back")):
            selected.append("return_agent")
        if any(term in text for term in ("shipping", "tracking", "package", "delivery", "delayed", "lost")):
            selected.append("shipping_agent")
        if any(term in text for term in ("charged", "charge", "payment", "card", "refund timeline", "refund status", "paid")):
            selected.append("payment_agent")
        if any(term in text for term in ("account", "address", "login", "password", "email", "profile")):
            selected.append("account_agent")
        if not selected:
            selected.append("escalation_agent")
        return selected

    def _build_execution_plan(
        self,
        message: str,
        selected_agents: list[AgentName],
        requested_mode: str,
    ) -> ExecutionPlan:
        if selected_agents == ["escalation_agent"]:
            return ExecutionPlan(
                mode="single_agent",
                steps=[ExecutionStep(step=1, agents=["escalation_agent"], mode="single_agent", reason="No domain specialist matched the request.")],
                reason="Unknown intent is routed safely to escalation.",
            )
        if len(selected_agents) == 1:
            return ExecutionPlan(
                mode="single_agent",
                steps=[ExecutionStep(step=1, agents=selected_agents, mode="single_agent", reason="Only one domain is needed.")],
                reason="The request maps to a single specialist domain.",
            )
        if requested_mode == "sequential" or _requires_sequential(message, selected_agents):
            return ExecutionPlan(
                mode="sequential",
                steps=[
                    ExecutionStep(step=index + 1, agents=[agent], mode="single_agent", reason="This domain may affect later support guidance.")
                    for index, agent in enumerate(selected_agents)
                ],
                reason="The request contains a dependency or sensitive change, so agents run in order.",
            )
        if requested_mode == "single_agent":
            return ExecutionPlan(
                mode="sequential",
                steps=[
                    ExecutionStep(
                        step=index + 1,
                        agents=[agent],
                        mode="single_agent",
                        reason="LLM selected multiple agents; backend runs them sequentially to preserve a valid plan.",
                    )
                    for index, agent in enumerate(selected_agents)
                ],
                reason="Multiple selected agents cannot run as single_agent, so the backend converted the plan to sequential.",
            )
        return ExecutionPlan(
            mode="parallel",
            steps=[ExecutionStep(step=1, agents=selected_agents, mode="parallel", reason="Selected domains are independent and can be checked concurrently.")],
            reason="Independent specialist checks can run in parallel.",
        )

    async def _execute_plan(
        self,
        plan: ExecutionPlan,
        message: str,
        user_id: str,
        context: ReturnContext,
        trace: list[TraceEvent],
    ) -> list[AgentResult]:
        results: list[AgentResult] = []
        for step in plan.steps:
            trace.append(_trace("execution", f"Running step {step.step}.", {"agents": step.agents, "mode": step.mode}))
            if step.mode == "parallel":
                step_results = await asyncio.gather(
                    *(self._run_agent(agent, message, user_id, context, results) for agent in step.agents)
                )
            else:
                step_results = []
                for agent in step.agents:
                    step_results.append(await self._run_agent(agent, message, user_id, context, results))
            results.extend(step_results)
        return results

    async def _run_agent(
        self,
        agent: AgentName,
        message: str,
        user_id: str,
        context: ReturnContext,
        prior_results: list[AgentResult],
    ) -> AgentResult:
        instance = self.agent_map[agent]
        if agent == "escalation_agent":
            result = await instance.run(message, user_id, context, prior_results)
        else:
            result = await instance.run(message, user_id, context)
        result.agent = agent
        return result

    async def _maybe_escalate(
        self,
        message: str,
        user_id: str,
        context: ReturnContext,
        selected_agents: list[AgentName],
        agent_results: list[AgentResult],
        trace: list[TraceEvent],
    ) -> list[AgentResult]:
        if "escalation_agent" in selected_agents:
            return agent_results
        should_escalate = any(result.needs_escalation or result.confidence < 0.6 for result in agent_results)
        if _has_disagreement(agent_results):
            should_escalate = True
        if not should_escalate:
            return agent_results
        trace.append(_trace("escalation", "Safe escalation selected after specialist review."))
        escalation_result = await self._run_agent("escalation_agent", message, user_id, context, agent_results)
        return [*agent_results, escalation_result]

    async def _execute_backend_actions(
        self,
        agent_results: list[AgentResult],
        user_id: str,
        trace: list[TraceEvent],
    ) -> None:
        for result in agent_results:
            for action in result.proposed_actions:
                if action.name != "create_support_ticket" or action.requires_approval:
                    continue
                backend_result = await validate_and_execute_action(self.tools, action, user_id=user_id)
                result.backend_actions.append(backend_result)
                trace.append(
                    _trace(
                        "backend_action",
                        "Backend validated and executed an approved safe action.",
                        {"agent": result.agent, "action": action.name, "status": backend_result.status},
                    )
                )


def _merge_context(
    previous: ReturnContext,
    message: str,
    llm_context: ReturnContext | None = None,
) -> ReturnContext:
    context = previous.model_copy()
    if llm_context:
        for field in ("order_id", "item_id", "return_reason", "product_hint"):
            value = getattr(llm_context, field)
            if value:
                setattr(context, field, value)
        context.refund_requested = context.refund_requested or llm_context.refund_requested
    order_match = ORDER_RE.search(message)
    item_match = ITEM_RE.search(message)
    if order_match:
        context.order_id = order_match.group(0).lower()
    if item_match:
        context.item_id = item_match.group(0).lower()
    product_hint = _extract_product_hint(message)
    if product_hint:
        context.product_hint = product_hint
    return context


def _normalize_selected_agents(selected_agents: list[AgentName]) -> list[AgentName]:
    normalized: list[AgentName] = []
    for agent in selected_agents:
        if agent not in normalized:
            normalized.append(agent)
    return normalized or ["escalation_agent"]


def _specialist_prompt(agent_name: str, role_guidance: str) -> str:
    return (
        f"You are {agent_name} in a customer-support agent system. {role_guidance} "
        "Return an AgentResult. Keep details grounded only in provided backend facts. "
        "Use confidence between 0 and 1. Set needs_escalation=true for low confidence, "
        "sensitive changes, missing records, disagreement, or human-review cases. "
        "No irreversible actions are executed by agents; agents only propose actions."
    )


def _normalize_return_result(
    result: AgentResult,
    resolved: dict,
    eligibility: dict | None,
) -> AgentResult:
    result.agent = "return_agent"
    if "error" in resolved or not eligibility:
        return result
    has_action = any(action.name == "start_return_authorization" for action in result.proposed_actions)
    if eligibility.get("eligible") and not has_action:
        result.proposed_actions.append(
            ProposedAction(
                name="start_return_authorization",
                args={
                    "order_id": resolved["order"]["order_id"],
                    "item_id": resolved["item"]["item_id"],
                    "amount": eligibility["amount"],
                },
                safety="safe_write",
                requires_approval=True,
                reason="Create a return authorization only after customer confirmation.",
            )
        )
    if not eligibility.get("eligible"):
        result.proposed_actions = [
            action for action in result.proposed_actions if action.name != "start_return_authorization"
        ]
    return result


def _extract_product_hint(message: str) -> str | None:
    text = message.lower()
    for hint in ("shoes", "shoe", "sneakers", "headphones", "mug"):
        if hint in text:
            return hint
    return None


def _requires_sequential(message: str, selected_agents: list[AgentName]) -> bool:
    text = message.lower()
    sensitive = any(term in text for term in ("change", "update", "password", "login", "address"))
    shipping_dependency = "shipping_agent" in selected_agents and (
        "return_agent" in selected_agents or "payment_agent" in selected_agents
    )
    return sensitive or shipping_dependency


def _has_disagreement(results: list[AgentResult]) -> bool:
    codes = {code for result in results for code in result.reason_codes}
    contradictory_pairs = {
        ("return_eligible", "return_window_expired"),
        ("return_eligible", "item_not_refundable"),
        ("return_eligible", "policy_disallows_refund"),
        ("duplicate_charge_found", "duplicate_charge_not_found"),
        ("account_loaded", "account_not_found"),
    }
    return any(left in codes and right in codes for left, right in contradictory_pairs)


def _aggregate_response(results: list[AgentResult]) -> str:
    domain_results = [result for result in results if result.agent != "escalation_agent"]
    escalation = next((result for result in results if result.agent == "escalation_agent"), None)
    parts = [result.summary for result in domain_results] or [results[0].summary]
    blocked_actions = [
        action
        for result in domain_results
        for action in result.proposed_actions
        if action.safety == "unsafe_write"
    ]
    if blocked_actions:
        parts.append("I did not make payment or account changes; those actions require approval.")
    if escalation:
        ticket = next(
            (
                action_result.result
                for action_result in escalation.backend_actions
                if action_result.ok and action_result.result
            ),
            None,
        )
        if ticket:
            parts.append(f"I created support ticket {ticket['ticket_id']} for human review.")
        else:
            parts.append(escalation.summary)
    return " ".join(parts)


def _reasoning(selected_agents: list[AgentName], plan: ExecutionPlan, results: list[AgentResult]) -> str:
    codes = [code for result in results for code in result.reason_codes]
    return (
        f"Selected {', '.join(selected_agents)} because the request matched those domains. "
        f"Execution mode was {plan.mode}: {plan.reason} "
        f"Result reason codes: {', '.join(codes) if codes else 'none'}."
    )


def _trace(stage: str, message: str, data: dict | None = None) -> TraceEvent:
    return TraceEvent(stage=stage, message=message, data=data or {})
