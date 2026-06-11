import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_last_order_at", "last_order_at"),
        Index("ix_customers_total_spend", "total_spend"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str | None] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    total_spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default=text("0"))
    order_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_order_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (CheckConstraint("source IN ('manual','ai')", name="source"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    definition: Mapped[dict] = mapped_column(JSONB)
    source: Mapped[str | None] = mapped_column(Text)
    ai_rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint("channel IN ('whatsapp','sms','email')", name="channel"),
        CheckConstraint("status IN ('draft','dispatching','active','completed')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    segment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("segments.id"))
    channel: Mapped[str | None] = mapped_column(Text)
    message_template: Mapped[str] = mapped_column(Text)
    ai_reasoning: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str | None] = mapped_column(Text)
    audience_size: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_customer_id_ordered_at", "customer_id", "ordered_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str | None] = mapped_column(Text, unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    items: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attributed_campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaigns.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Communication(Base):
    __tablename__ = "communications"
    __table_args__ = (Index("ix_communications_campaign_id_status", "campaign_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"))
    channel: Mapped[str] = mapped_column(Text)
    rendered_message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    status_rank: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class CommunicationEvent(Base):
    """Append-only truth log. Never UPDATE or DELETE rows in this table."""

    __tablename__ = "communication_events"
    __table_args__ = (
        UniqueConstraint(
            "communication_id",
            "event_type",
            name="uq_communication_events_communication_id_event_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    communication_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("communications.id"))
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
