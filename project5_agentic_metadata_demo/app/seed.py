from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from project5_agentic_metadata_demo.app.database import SessionLocal, init_db
from project5_agentic_metadata_demo.app.models import Dataset, DatasetSchema, Lineage, Owner


OWNERS = [
    Owner(id=1, team_name="finance", contact_email="finance-data@example.com", slack_channel="#team-finance-data"),
    Owner(id=2, team_name="analytics", contact_email="analytics@example.com", slack_channel="#analytics"),
    Owner(id=3, team_name="growth", contact_email="growth-data@example.com", slack_channel="#growth-data"),
    Owner(id=4, team_name="commerce", contact_email="commerce-data@example.com", slack_channel="#commerce-data"),
    Owner(id=5, team_name="customer_support", contact_email="support-ops@example.com", slack_channel="#support-ops"),
    Owner(id=6, team_name="operations", contact_email="ops-data@example.com", slack_channel="#ops-data"),
]


DATASETS = [
    Dataset(
        id=1,
        name="revenue_transactions",
        description="Fact table for booked revenue transactions from the billing platform.",
        owner_team="finance",
        created_at="2025-01-15T09:00:00Z",
        updated_at="2026-04-18T12:30:00Z",
        data_source="billing_db",
        sensitivity_level="high",
    ),
    Dataset(
        id=2,
        name="revenue_forecast",
        description="Monthly revenue forecast assembled from bookings, pipeline, and historical actuals.",
        owner_team="finance",
        created_at="2025-03-01T09:00:00Z",
        updated_at="2026-04-20T10:15:00Z",
        data_source="finance_planning",
        sensitivity_level="internal",
    ),
    Dataset(
        id=3,
        name="customer_profiles",
        description="Golden customer profile records with account traits and lifecycle status.",
        owner_team="growth",
        created_at="2024-12-12T14:00:00Z",
        updated_at="2026-04-15T08:45:00Z",
        data_source="crm",
        sensitivity_level="restricted",
    ),
    Dataset(
        id=4,
        name="product_catalog",
        description="Current sellable products, packaging, public descriptions, and launch status.",
        owner_team="commerce",
        created_at="2024-10-02T11:00:00Z",
        updated_at="2026-03-28T16:10:00Z",
        data_source="catalog_service",
        sensitivity_level="public",
    ),
    Dataset(
        id=5,
        name="support_tickets",
        description="Customer support tickets with queue, priority, and resolution metadata.",
        owner_team="customer_support",
        created_at="2025-04-06T09:30:00Z",
        updated_at="2026-04-19T19:20:00Z",
        data_source="zendesk",
        sensitivity_level="confidential",
    ),
    Dataset(
        id=6,
        name="web_events",
        description="Clickstream events from the product website and logged-in application.",
        owner_team="analytics",
        created_at="2025-02-10T00:00:00Z",
        updated_at="2026-04-21T00:05:00Z",
        data_source="event_collector",
        sensitivity_level="internal",
    ),
    Dataset(
        id=7,
        name="finance_expenses",
        description="Approved departmental expenses, vendors, purchase categories, and cost centers.",
        owner_team="finance",
        created_at="2025-05-04T15:30:00Z",
        updated_at="2026-04-11T07:25:00Z",
        data_source="erp",
        sensitivity_level="high",
    ),
    Dataset(
        id=8,
        name="inventory_levels",
        description="Warehouse inventory positions and reorder status for physical goods.",
        owner_team="operations",
        created_at="2025-06-17T13:10:00Z",
        updated_at="2026-04-12T22:00:00Z",
        data_source="warehouse_management",
        sensitivity_level="internal",
    ),
]


SCHEMAS = [
    DatasetSchema(
        id=1,
        dataset_id=1,
        column_name="transaction_id",
        column_type="TEXT",
        is_nullable=False,
        description="Unique billing transaction identifier.",
    ),
    DatasetSchema(id=2, dataset_id=1, column_name="account_id", column_type="TEXT", is_nullable=False, description="Customer account identifier."),
    DatasetSchema(id=3, dataset_id=1, column_name="amount_usd", column_type="NUMERIC", is_nullable=False, description="Booked transaction amount in USD."),
    DatasetSchema(id=4, dataset_id=1, column_name="booked_at", column_type="TIMESTAMP", is_nullable=False, description="Booking timestamp."),
    DatasetSchema(id=5, dataset_id=2, column_name="forecast_month", column_type="DATE", is_nullable=False, description="Forecast month."),
    DatasetSchema(id=6, dataset_id=2, column_name="scenario", column_type="TEXT", is_nullable=False, description="Planning scenario name."),
    DatasetSchema(id=7, dataset_id=2, column_name="forecast_revenue_usd", column_type="NUMERIC", is_nullable=False, description="Projected revenue."),
    DatasetSchema(id=8, dataset_id=3, column_name="customer_id", column_type="TEXT", is_nullable=False, description="Canonical customer identifier."),
    DatasetSchema(id=9, dataset_id=3, column_name="email_hash", column_type="TEXT", is_nullable=True, description="Hashed customer email."),
    DatasetSchema(id=10, dataset_id=3, column_name="lifecycle_stage", column_type="TEXT", is_nullable=False, description="Current lifecycle stage."),
    DatasetSchema(id=11, dataset_id=4, column_name="product_id", column_type="TEXT", is_nullable=False, description="Product identifier."),
    DatasetSchema(id=12, dataset_id=4, column_name="product_name", column_type="TEXT", is_nullable=False, description="Display product name."),
    DatasetSchema(id=13, dataset_id=4, column_name="launch_status", column_type="TEXT", is_nullable=False, description="Launch lifecycle status."),
    DatasetSchema(id=14, dataset_id=5, column_name="ticket_id", column_type="TEXT", is_nullable=False, description="Support ticket identifier."),
    DatasetSchema(id=15, dataset_id=5, column_name="priority", column_type="TEXT", is_nullable=False, description="Ticket priority."),
    DatasetSchema(id=16, dataset_id=5, column_name="resolution_seconds", column_type="INTEGER", is_nullable=True, description="Time to resolution."),
    DatasetSchema(id=17, dataset_id=6, column_name="event_id", column_type="TEXT", is_nullable=False, description="Unique event identifier."),
    DatasetSchema(id=18, dataset_id=6, column_name="event_name", column_type="TEXT", is_nullable=False, description="Event name."),
    DatasetSchema(id=19, dataset_id=6, column_name="occurred_at", column_type="TIMESTAMP", is_nullable=False, description="Event timestamp."),
    DatasetSchema(id=20, dataset_id=7, column_name="expense_id", column_type="TEXT", is_nullable=False, description="Expense identifier."),
    DatasetSchema(id=21, dataset_id=7, column_name="cost_center", column_type="TEXT", is_nullable=False, description="Owning cost center."),
    DatasetSchema(id=22, dataset_id=7, column_name="amount_usd", column_type="NUMERIC", is_nullable=False, description="Approved amount in USD."),
    DatasetSchema(id=23, dataset_id=8, column_name="sku", column_type="TEXT", is_nullable=False, description="Stock keeping unit."),
    DatasetSchema(id=24, dataset_id=8, column_name="warehouse_id", column_type="TEXT", is_nullable=False, description="Warehouse identifier."),
    DatasetSchema(id=25, dataset_id=8, column_name="available_units", column_type="INTEGER", is_nullable=False, description="Currently available units."),
]


LINEAGE = [
    Lineage(id=1, upstream_dataset_id=1, downstream_dataset_id=2, relationship_type="forecast_input"),
    Lineage(id=2, upstream_dataset_id=3, downstream_dataset_id=6, relationship_type="identity_enrichment"),
    Lineage(id=3, upstream_dataset_id=4, downstream_dataset_id=8, relationship_type="sku_reference"),
    Lineage(id=4, upstream_dataset_id=5, downstream_dataset_id=3, relationship_type="support_signal"),
    Lineage(id=5, upstream_dataset_id=7, downstream_dataset_id=2, relationship_type="planning_input"),
]


def seed_database(db: Session | None = None) -> int:
    owns_session = db is None
    if db is None:
        init_db()
        db = SessionLocal()
    try:
        existing = db.scalar(select(func.count(Dataset.id))) or 0
        if existing:
            return int(existing)
        for record in [*OWNERS, *DATASETS, *SCHEMAS, *LINEAGE]:
            db.merge(record)
        db.commit()
        return len(DATASETS)
    finally:
        if owns_session:
            db.close()


if __name__ == "__main__":
    count = seed_database()
    print(f"metadata.db is ready with {count} datasets")
