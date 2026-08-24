"""multi-tenancy, customer scores, and campaign cost

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = ("customers", "orders", "segments", "campaigns", "communications")


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
    )

    for table in _TENANT_TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_tenant_id_tenants", table, "tenants", ["tenant_id"], ["id"]
        )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])

    op.add_column("segments", sa.Column("definition_hash", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_segments_tenant_id_definition_hash", "segments", ["tenant_id", "definition_hash"]
    )

    op.add_column("campaigns", sa.Column("cost_per_message", sa.Numeric(10, 4), nullable=True))

    op.create_table(
        "customer_scores",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("reactivation_probability", sa.Numeric(7, 6), nullable=False),
        sa.Column("expected_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("value_tier", sa.Text(), nullable=False),
        sa.Column("recency_days", sa.Integer(), nullable=True),
        sa.Column("frequency", sa.Integer(), nullable=True),
        sa.Column("monetary_total", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "reasons", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_customer_scores"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_customer_scores_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_customer_scores_customer_id_customers"
        ),
        sa.UniqueConstraint(
            "tenant_id", "customer_id", name="uq_customer_scores_tenant_id_customer_id"
        ),
    )
    op.create_index("ix_customer_scores_tenant_id", "customer_scores", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_scores_tenant_id", table_name="customer_scores")
    op.drop_table("customer_scores")

    op.drop_column("campaigns", "cost_per_message")

    op.drop_constraint("uq_segments_tenant_id_definition_hash", "segments", type_="unique")
    op.drop_column("segments", "definition_hash")

    for table in _TENANT_TABLES:
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_constraint(f"fk_{table}_tenant_id_tenants", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")

    op.drop_table("tenants")
