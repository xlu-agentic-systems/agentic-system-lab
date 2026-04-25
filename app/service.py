from __future__ import annotations

import asyncio
import logging
import re

from app.models import (
    AgentName,
    AgentResult,
    ChatMessage,
    ConversationRequest,
    ConversationResponse,
    ExecutionPlan,
    ExecutionStep,
    ProposedAction,
    ReturnContext,
    TraceEvent,
)
from app.session_store import JsonSessionStore
from app.tools import BackendTools, validate_and_execute_action


logger = logging.getLogger(__name__)

ORDER_RE = re.compile(r"\border-\d+\b", re.IGNORECASE)
ITEM_RE = re.compile(r"\bitem-\d+\b", re.IGNORECASE)


class ReturnAgent:
    name = "return_agent"

    def __init__(self, tools: BackendTools) -> None:
        self.tools = tools

    async def run(self, message: str, user_id: str, context: ReturnContext) -> AgentResult:
        resolved = await self.tools.resolve_order_item(
            user_id=user_id,
            order_id=context.order_id,
            item_id=context.item_id,
            product_hint=context.product_hint,
        )
        if "error" in resolved:
            return AgentResult(
                agent=self.name,
                confidence=0.35,
                summary="I could not identify the exact item to evaluate for return eligibility.",
                details=resolved,
                needs_escalation=True,
                reason_codes=[resolved["error"]],
            )
        order = resolved["order"]
        item = resolved["item"]
        product = resolved["product"]
        eligibility = await self.tools.check_return_eligibility(order["order_id"], item["item_id"])
        if eligibility["eligible"]:
            amount = eligibility["amount"]

            return AgentResult(
                agent=self.name,
                confidence=0.92,
                summary=f"{product['name']} is eligible for return. The refundable amount is ${amount}.",
                details={"resolved": resolved, "eligibility": eligibility},
                proposed_actions=[
                    ProposedAction(
                        name="start_return_authorization",
                        args={
                            "order_id": order["order_id"],
                            "item_id": item["item_id"],
                            "amount": amount,
                        },
                        safety="safe_write",
                        requires_approval=True,
                        reason="Create a return authorization only after customer confirmation.",
                    )
                ],
                reason_codes=["return_eligible"],
            )
        return AgentResult(
            agent=self.name,
            confidence=0.88,
            summary=(
                f"{product['name']} is not currently eligible for return: "
                f"{', '.join(eligibility['reason_codes'])}."
            ),
            details={"resolved": resolved, "eligibility": eligibility},
            needs_escalation="order_not_delivered" in eligibility["reason_codes"],
            reason_codes=eligibility["reason_codes"],
        )


class ShippingAgent:
    name = "shipping_agent"

    def __init__(self, tools: BackendTools) -> None:
        self.tools = tools

    async def run(self, message: str, user_id: str, context: ReturnContext) -> AgentResult:
        status = await self.tools.get_shipping_status(user_id, context.order_id)
        if "error" in status:
            return AgentResult(
                agent=self.name,
                confidence=0.4,
                summary="I could not find a shipment for this request.",
                details=status,
                needs_escalation=True,
                reason_codes=[status["error"]],
            )
        if status["status"] == "delayed":
            return AgentResult(
                agent=self.name,
                confidence=0.9,
                summary=(
                    f"Order {status['order_id']} is delayed with {status['carrier']}. "
                    f"Latest update: {status['last_update']}"
                ),
                details=status,
                needs_escalation=True,
                reason_codes=["shipment_delayed"],
            )
        return AgentResult(
            agent=self.name,
            confidence=0.9,
            summary=(
                f"Order {status['order_id']} is {status['status']} with {status['carrier']} "
                f"tracking {status['tracking_number']}."
            ),
            details=status,
            reason_codes=[f"shipment_{status['status']}"],
        )


class PaymentAgent:
    name = "payment_agent"

    def __init__(self, tools: BackendTools) -> None:
        self.tools = tools

    async def run(self, message: str, user_id: str, context: ReturnContext) -> AgentResult:
        text = message.lower()
        if any(term in text for term in ("timeline", "how long", "when will", "refund status")):
            timeline = await self.tools.get_refund_timeline()
            return AgentResult(
                agent=self.name,
                confidence=0.86,
                summary=timeline["timeline"],
                details=timeline,
                reason_codes=["refund_timeline"],
            )
        duplicates = await self.tools.find_duplicate_charges(user_id, context.order_id)
        if duplicates["duplicates"]:
            duplicate = duplicates["duplicates"][0]
            extra_payment = duplicate[1]
            from app.models import ProposedAction

            return AgentResult(
                agent=self.name,
                confidence=0.93,
                summary=(
                    f"I found a likely duplicate charge for order {extra_payment['order_id']} "
                    f"in the amount of ${extra_payment['amount']}."
                ),
                details=duplicates,
                proposed_actions=[
                    ProposedAction(
                        name="refund_duplicate_charge",
                        args={"payment_id": extra_payment["payment_id"], "amount": extra_payment["amount"]},
                        safety="unsafe_write",
                        requires_approval=True,
                        reason="Refunding a payment changes customer funds and needs approval.",
                    )
                ],
                reason_codes=["duplicate_charge_found"],
            )
        return AgentResult(
            agent=self.name,
            confidence=0.72,
            summary="I did not find a duplicate captured charge in the available payment records.",
            details=duplicates,
            reason_codes=["duplicate_charge_not_found"],
        )


class AccountAgent:
    name = "account_agent"

    def __init__(self, tools: BackendTools) -> None:
        self.tools = tools

    async def run(self, message: str, user_id: str, context: ReturnContext) -> AgentResult:
        profile = await self.tools.get_account_profile(user_id)
        if "error" in profile:
            return AgentResult(
                agent=self.name,
                confidence=0.35,
                summary="I could not load the account profile.",
                details=profile,
                needs_escalation=True,
                reason_codes=[profile["error"]],
            )
        text = message.lower()
        sensitive = any(term in text for term in ("address", "password", "login", "email", "payment method"))
        return AgentResult(
            agent=self.name,
            confidence=0.82,
            summary=(
                f"The account on file is {profile['name']} with email {profile['email']}. "
                "Sensitive profile changes require verification."
            ),
            details={"profile": profile, "sensitive_change_requested": sensitive},
            needs_escalation=sensitive,
            reason_codes=["sensitive_account_change"] if sensitive else ["account_loaded"],
        )


class EscalationAgent:
    name = "escalation_agent"

    async def run(
        self,
        message: str,
        user_id: str,
        context: ReturnContext,
        prior_results: list[AgentResult],
    ) -> AgentResult:
        from app.models import ProposedAction

        low_confidence = [result.agent for result in prior_results if result.confidence < 0.6]
        needs_escalation = [result.agent for result in prior_results if result.needs_escalation]
        reasons = [code for result in prior_results for code in result.reason_codes]
        reason = f"Customer request: {message}; reason codes: {', '.join(reasons) or 'unknown'}"
        return AgentResult(
            agent=self.name,
            confidence=0.9,
            summary="A human support ticket should be created for this request.",
            details={
                "low_confidence_agents": low_confidence,
                "escalating_agents": needs_escalation,
                "prior_reason_codes": reasons,
            },
            proposed_actions=[
                ProposedAction(
                    name="create_support_ticket",
                    args={"reason": reason},
                    safety="safe_write",
                    requires_approval=False,
                    reason="The orchestrator determined this request needs human review.",
                )
            ],
            needs_escalation=True,
            reason_codes=["human_ticket_recommended"],
        )


class ConversationService:
    def __init__(
        self,
        *,
        session_store: JsonSessionStore | None = None,
        tools: BackendTools | None = None,
    ) -> None:
        self.session_store = session_store or JsonSessionStore()
        self.tools = tools or BackendTools()
        self.agent_map = {
            "return_agent": ReturnAgent(self.tools),
            "shipping_agent": ShippingAgent(self.tools),
            "payment_agent": PaymentAgent(self.tools),
            "account_agent": AccountAgent(self.tools),
            "escalation_agent": EscalationAgent(),
        }

    async def handle_message(self, request: ConversationRequest) -> ConversationResponse:
        trace: list[TraceEvent] = []
        state = await self.session_store.load(request.session_id, request.user_id)
        state.history.append(ChatMessage(role="user", content=request.message))
        context = _merge_context(state.context, request.message)
        state.context = context
        trace.append(_trace("context", "Loaded isolated session context.", context.model_dump()))

        selected_agents = self._select_agents(request.message)
        plan = self._build_execution_plan(request.message, selected_agents)
        trace.append(
            _trace(
                "routing",
                "Orchestrator selected specialist agents and execution mode.",
                {"selected_agents": selected_agents, "mode": plan.mode},
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

    def _build_execution_plan(self, message: str, selected_agents: list[AgentName]) -> ExecutionPlan:
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
        if _requires_sequential(message, selected_agents):
            return ExecutionPlan(
                mode="sequential",
                steps=[
                    ExecutionStep(step=index + 1, agents=[agent], mode="single_agent", reason="This domain may affect later support guidance.")
                    for index, agent in enumerate(selected_agents)
                ],
                reason="The request contains a dependency or sensitive change, so agents run in order.",
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
            return await instance.run(message, user_id, context, prior_results)
        return await instance.run(message, user_id, context)

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


def _merge_context(previous: ReturnContext, message: str) -> ReturnContext:
    context = previous.model_copy()
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
