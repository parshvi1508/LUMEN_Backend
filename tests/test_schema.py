"""Metadata-level schema assertions, no live DB required.

The live upgrade and downgrade roundtrip runs manually against Supabase.
At scale the right answer is a disposable Postgres per test run
(testcontainers), documented as a tradeoff, not built at demo scope.
"""

from sqlalchemy import Integer, UniqueConstraint

from crm_api.models import Base, Communication, CommunicationEvent

EXPECTED_TABLES = {
    "customers",
    "orders",
    "segments",
    "campaigns",
    "communications",
    "communication_events",
}


def test_all_tables_present() -> None:
    assert set(Base.metadata.tables.keys()) == EXPECTED_TABLES


def test_communication_events_idempotency_constraint() -> None:
    uniques = [
        c for c in CommunicationEvent.__table__.constraints if isinstance(c, UniqueConstraint)
    ]
    assert len(uniques) == 1
    constraint = uniques[0]
    assert constraint.name == "uq_communication_events_communication_id_event_type"
    assert [col.name for col in constraint.columns] == ["communication_id", "event_type"]


def test_communications_status_rank() -> None:
    col = Communication.__table__.c.status_rank
    assert isinstance(col.type, Integer)
    assert col.nullable is False
    assert col.server_default is not None
    assert col.server_default.arg.text == "0"


def test_communications_status_default_queued() -> None:
    col = Communication.__table__.c.status
    assert col.server_default.arg.text == "'queued'"


def test_expected_indexes() -> None:
    index_names = {idx.name for table in Base.metadata.tables.values() for idx in table.indexes}
    assert {
        "ix_customers_last_order_at",
        "ix_customers_total_spend",
        "ix_orders_customer_id_ordered_at",
        "ix_communications_campaign_id_status",
    } <= index_names


def test_single_migration_revision() -> None:
    from pathlib import Path

    versions = Path(__file__).parent.parent / "alembic" / "versions"
    assert len(list(versions.glob("*.py"))) == 1
