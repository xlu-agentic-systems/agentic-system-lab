from __future__ import annotations

from datetime import date

from project1_multi_agent_return_bot.app.catalog import Catalog
from project1_multi_agent_return_bot.app.models import EligibilityResult, OrderItem, ReturnPolicy


def check_return_policy(
    catalog: Catalog,
    order_id: str,
    item_id: str,
    *,
    today: date | None = None,
) -> EligibilityResult:
    today = today or catalog.today
    order = catalog.get_order(order_id)
    if order is None:
        return EligibilityResult(eligible=False, reason_codes=["order_not_found"])

    item = next((candidate for candidate in order.items if candidate.item_id == item_id), None)
    if item is None:
        return EligibilityResult(eligible=False, reason_codes=["item_not_found"])

    product = catalog.get_product(item.product_id)
    if product is None:
        return EligibilityResult(eligible=False, reason_codes=["product_not_found"])

    policy = catalog.get_policy(product.category)
    if policy is None:
        return EligibilityResult(eligible=False, reason_codes=["policy_not_found"])

    reason_codes = _policy_reason_codes(order.status, order.delivered_at, item, policy, today)
    return EligibilityResult(
        eligible=not reason_codes,
        reason_codes=reason_codes,
        amount=item.refund_amount if not reason_codes else None,
        policy=policy,
    )


def _policy_reason_codes(
    order_status: str,
    delivered_at: date | None,
    item: OrderItem,
    policy: ReturnPolicy,
    today: date,
) -> list[str]:
    reason_codes: list[str] = []
    if order_status != "delivered":
        reason_codes.append("order_not_delivered")
    if not item.refundable:
        reason_codes.append("item_not_refundable")
    if not policy.allow_refunds:
        reason_codes.append("policy_disallows_refund")
    if delivered_at is None:
        reason_codes.append("missing_delivery_date")
    elif (today - delivered_at).days > policy.window_days:
        reason_codes.append("return_window_expired")
    return reason_codes
