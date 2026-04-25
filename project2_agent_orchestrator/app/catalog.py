from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from project2_agent_orchestrator.app.models import AccountProfile, Order, Payment, Product, ReturnPolicy, Shipment


DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_data.json"
SAMPLE_DATA: dict[str, Any] = {
    "today": "2026-04-24",
    "users": [
        {"user_id": "user-1", "name": "Avery Chen"},
        {"user_id": "user-2", "name": "Jordan Lee"},
    ],
    "products": [
        {
            "product_id": "prod-headphones",
            "name": "Noise Canceling Headphones",
            "category": "electronics",
            "final_sale": False,
        },
        {
            "product_id": "prod-jacket",
            "name": "Trail Rain Jacket",
            "category": "apparel",
            "final_sale": False,
        },
        {
            "product_id": "prod-clearance-mug",
            "name": "Clearance Ceramic Mug",
            "category": "clearance",
            "final_sale": True,
        },
    ],
    "return_policies": [
        {
            "category": "electronics",
            "window_days": 30,
            "allow_refunds": True,
            "notes": "Electronics are refundable within 30 days of delivery if the item is refundable.",
        },
        {
            "category": "apparel",
            "window_days": 45,
            "allow_refunds": True,
            "notes": "Apparel is refundable within 45 days of delivery if the item is refundable.",
        },
        {
            "category": "clearance",
            "window_days": 0,
            "allow_refunds": False,
            "notes": "Clearance final-sale items are not refundable.",
        },
    ],
    "orders": [
        {
            "order_id": "order-1001",
            "user_id": "user-1",
            "status": "delivered",
            "delivered_at": "2026-04-10",
            "items": [
                {
                    "item_id": "item-1",
                    "product_id": "prod-headphones",
                    "quantity": 1,
                    "unit_price": "129.99",
                    "refundable": True,
                },
                {
                    "item_id": "item-2",
                    "product_id": "prod-clearance-mug",
                    "quantity": 2,
                    "unit_price": "8.50",
                    "refundable": False,
                },
            ],
        },
        {
            "order_id": "order-1002",
            "user_id": "user-1",
            "status": "delivered",
            "delivered_at": "2026-02-01",
            "items": [
                {
                    "item_id": "item-3",
                    "product_id": "prod-jacket",
                    "quantity": 1,
                    "unit_price": "89.00",
                    "refundable": True,
                }
            ],
        },
        {
            "order_id": "order-2001",
            "user_id": "user-2",
            "status": "delivered",
            "delivered_at": "2026-04-18",
            "items": [
                {
                    "item_id": "item-4",
                    "product_id": "prod-headphones",
                    "quantity": 1,
                    "unit_price": "129.99",
                    "refundable": True,
                }
            ],
        },
    ],
}


class Catalog:
    def __init__(self, data_path: Path = DATA_PATH) -> None:
        payload = json.loads(data_path.read_text()) if data_path.exists() else SAMPLE_DATA
        self.today = date.fromisoformat(payload["today"])
        self.accounts = {
            account.user_id: account
            for account in (AccountProfile.model_validate(item) for item in payload["users"])
        }
        self.products = {
            product.product_id: product
            for product in (Product.model_validate(item) for item in payload["products"])
        }
        self.orders = {
            order.order_id: order
            for order in (Order.model_validate(item) for item in payload["orders"])
        }
        self.policies = {
            policy.category: policy
            for policy in (
                ReturnPolicy.model_validate(item) for item in payload["return_policies"]
            )
        }
        self.shipments = {
            shipment.order_id: shipment
            for shipment in (Shipment.model_validate(item) for item in payload.get("shipments", []))
        }
        self.payments = [Payment.model_validate(item) for item in payload.get("payments", [])]

    def get_account(self, user_id: str) -> AccountProfile | None:
        return self.accounts.get(user_id)

    def get_order(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)

    def get_user_orders(self, user_id: str) -> list[Order]:
        return [order for order in self.orders.values() if order.user_id == user_id]

    def get_product(self, product_id: str) -> Product | None:
        return self.products.get(product_id)

    def get_policy(self, category: str) -> ReturnPolicy | None:
        return self.policies.get(category)

    def get_order_item(self, order_id: str, item_id: str):
        order = self.get_order(order_id)
        if not order:
            return None
        return next((item for item in order.items if item.item_id == item_id), None)

    def get_shipment(self, order_id: str) -> Shipment | None:
        return self.shipments.get(order_id)

    def get_user_payments(self, user_id: str, order_id: str | None = None) -> list[Payment]:
        payments = [payment for payment in self.payments if payment.user_id == user_id]
        if order_id:
            payments = [payment for payment in payments if payment.order_id == order_id]
        return payments

    def resolve_order_item(
        self,
        *,
        user_id: str,
        order_id: str | None = None,
        item_id: str | None = None,
        product_hint: str | None = None,
    ):
        orders = self.get_user_orders(user_id)
        if order_id:
            orders = [order for order in orders if order.order_id == order_id]

        for order in orders:
            for item in order.items:
                if item_id and item.item_id != item_id:
                    continue
                if product_hint and not self._product_matches_hint(item.product_id, product_hint):
                    continue
                return order, item
        return (orders[0], None) if orders else (None, None)

    def _product_matches_hint(self, product_id: str, product_hint: str) -> bool:
        product = self.get_product(product_id)
        if not product:
            return False
        hint = product_hint.lower()
        terms = [product.name.lower(), product.category.lower(), *product.aliases]
        return any(hint in term or term in hint for term in terms)


catalog = Catalog()
