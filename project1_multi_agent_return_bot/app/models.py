from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AgentName = Literal[
    "return_agent",
    "shipping_agent",
    "payment_agent",
    "account_agent",
    "escalation_agent",
]
ExecutionMode = Literal["single_agent", "sequential", "parallel"]
ToolSafety = Literal["read_only", "safe_write", "unsafe_write"]
ActionStatus = Literal["proposed", "executed", "blocked"]
Intent = Literal["return_request", "return_policy_question", "refund_status", "unknown"]
DecisionStatus = Literal["needs_clarification", "approved", "rejected", "escalated"]


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReturnContext(BaseModel):
    order_id: str | None = None
    item_id: str | None = None
    return_reason: str | None = None
    refund_requested: bool = False
    product_hint: str | None = None


class SessionState(BaseModel):
    session_id: str
    user_id: str
    context: ReturnContext = Field(default_factory=ReturnContext)
    history: list[ChatMessage] = Field(default_factory=list)
    last_status: DecisionStatus | None = None
    last_selected_agents: list[AgentName] = Field(default_factory=list)


class ConversationRequest(BaseModel):
    session_id: str
    user_id: str
    message: str


class RoutingOutput(BaseModel):
    intent: Intent
    extracted_fields: ReturnContext
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None


class ToolCallProposal(BaseModel):
    name: Literal[
        "get_order",
        "get_return_policy",
        "check_refund_eligibility",
        "issue_refund",
        "create_support_ticket",
    ]
    args: ToolCallArgs
    safety: ToolSafety
    reason: str


class ToolCallArgs(BaseModel):
    order_id: str | None = None
    item_id: str | None = None
    category: str | None = None
    amount: str | None = None
    reason: str | None = None


class PlannerOutput(BaseModel):
    status: DecisionStatus
    reason_codes: list[str]
    explanation: str
    proposed_tool_calls: list[ToolCallProposal] = Field(default_factory=list)


class ToolExecutionResult(BaseModel):
    proposal: ToolCallProposal
    executed: bool
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class ExecutionStep(BaseModel):
    step: int
    agents: list[AgentName]
    mode: ExecutionMode
    reason: str


class ExecutionPlan(BaseModel):
    mode: ExecutionMode
    steps: list[ExecutionStep]
    reason: str


class OrchestratorDecision(BaseModel):
    selected_agents: list[AgentName]
    execution_mode: ExecutionMode
    extracted_context: ReturnContext = Field(default_factory=ReturnContext)
    reasoning: str


class ProposedAction(BaseModel):
    name: str
    args: ProposedActionArgs = Field(default_factory=lambda: ProposedActionArgs())
    safety: ToolSafety
    requires_approval: bool = True
    reason: str


class ProposedActionArgs(BaseModel):
    order_id: str | None = None
    item_id: str | None = None
    amount: str | None = None
    payment_id: str | None = None
    reason: str | None = None


class BackendActionResult(BaseModel):
    action: ProposedAction
    status: ActionStatus
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class AgentResult(BaseModel):
    agent: AgentName
    confidence: float = Field(ge=0, le=1)
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    backend_actions: list[BackendActionResult] = Field(default_factory=list)
    needs_escalation: bool = False
    reason_codes: list[str] = Field(default_factory=list)


class TraceEvent(BaseModel):
    stage: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ConversationResponse(BaseModel):
    session_id: str
    selected_agents: list[AgentName]
    execution_plan: ExecutionPlan
    agent_results: list[AgentResult]
    final_response: str
    reasoning: str
    trace: list[TraceEvent]


class Product(BaseModel):
    product_id: str
    name: str
    category: str
    final_sale: bool = False
    aliases: list[str] = Field(default_factory=list)


class OrderItem(BaseModel):
    item_id: str
    product_id: str
    quantity: int
    unit_price: Decimal
    refundable: bool = True

    @property
    def refund_amount(self) -> Decimal:
        return self.unit_price * self.quantity


class Order(BaseModel):
    order_id: str
    user_id: str
    status: Literal["processing", "shipped", "delivered", "cancelled"]
    delivered_at: date | None
    items: list[OrderItem]


class ReturnPolicy(BaseModel):
    category: str
    window_days: int
    allow_refunds: bool
    notes: str


class EligibilityResult(BaseModel):
    eligible: bool
    reason_codes: list[str]
    amount: Decimal | None = None
    policy: ReturnPolicy | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class RefundRecord(BaseModel):
    refund_id: str
    order_id: str
    item_id: str
    amount: Decimal
    status: Literal["issued"]


class Shipment(BaseModel):
    shipment_id: str
    order_id: str
    carrier: str
    tracking_number: str
    status: Literal["label_created", "in_transit", "delayed", "delivered", "lost"]
    estimated_delivery: date | None = None
    last_update: str


class Payment(BaseModel):
    payment_id: str
    order_id: str
    user_id: str
    amount: Decimal
    status: Literal["authorized", "captured", "failed", "refunded"]
    method: str
    created_at: datetime
    duplicate_group: str | None = None


class AccountProfile(BaseModel):
    user_id: str
    name: str
    email: str
    default_address: str
    login_mfa_enabled: bool


class SupportTicket(BaseModel):
    ticket_id: str
    user_id: str
    reason: str
    status: Literal["open"] = "open"
    created_at: datetime = Field(default_factory=datetime.utcnow)
