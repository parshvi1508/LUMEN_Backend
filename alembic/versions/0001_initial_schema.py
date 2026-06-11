"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column(
            "attributes", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("total_spend", sa.Numeric(12, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("order_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_order_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customers"),
        sa.UniqueConstraint("external_id", name="uq_customers_external_id"),
    )
    op.create_index("ix_customers_last_order_at", "customers", ["last_order_at"])
    op.create_index("ix_customers_total_spend", "customers", ["total_spend"])

    op.create_table(
        "segments",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("ai_rationale", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_segments"),
        sa.CheckConstraint("source IN ('manual','ai')", name="ck_segments_source"),
    )

    op.create_table(
        "campaigns",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("segment_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("message_template", sa.Text(), nullable=False),
        sa.Column("ai_reasoning", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("audience_size", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_campaigns"),
        sa.ForeignKeyConstraint(
            ["segment_id"], ["segments.id"], name="fk_campaigns_segment_id_segments"
        ),
        sa.CheckConstraint("channel IN ('whatsapp','sms','email')", name="ck_campaigns_channel"),
        sa.CheckConstraint(
            "status IN ('draft','dispatching','active','completed')", name="ck_campaigns_status"
        ),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("customer_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "items", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attributed_campaign_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.UniqueConstraint("external_id", name="uq_orders_external_id"),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_orders_customer_id_customers"
        ),
        sa.ForeignKeyConstraint(
            ["attributed_campaign_id"],
            ["campaigns.id"],
            name="fk_orders_attributed_campaign_id_campaigns",
        ),
    )
    op.create_index("ix_orders_customer_id_ordered_at", "orders", ["customer_id", "ordered_at"])

    op.create_table(
        "communications",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("rendered_message", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("status_rank", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_communications"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name="fk_communications_campaign_id_campaigns"
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_communications_customer_id_customers"
        ),
    )
    op.create_index(
        "ix_communications_campaign_id_status", "communications", ["campaign_id", "status"]
    )

    op.create_table(
        "communication_events",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("communication_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_communication_events"),
        sa.ForeignKeyConstraint(
            ["communication_id"],
            ["communications.id"],
            name="fk_communication_events_communication_id_communications",
        ),
        sa.UniqueConstraint(
            "communication_id",
            "event_type",
            name="uq_communication_events_communication_id_event_type",
        ),
    )


def downgrade() -> None:
    op.drop_table("communication_events")
    op.drop_index("ix_communications_campaign_id_status", table_name="communications")
    op.drop_table("communications")
    op.drop_index("ix_orders_customer_id_ordered_at", table_name="orders")
    op.drop_table("orders")
    op.drop_table("campaigns")
    op.drop_table("segments")
    op.drop_index("ix_customers_total_spend", table_name="customers")
    op.drop_index("ix_customers_last_order_at", table_name="customers")
    op.drop_table("customers")
