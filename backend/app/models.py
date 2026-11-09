"""ORM models. Schema source of truth is the Alembic migrations; keep these in sync."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_ref: Mapped[str] = mapped_column(Text, unique=True)
    customer_name: Mapped[str] = mapped_column(Text)
    customer_email: Mapped[str] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    reserved_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    refunded_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    business_unit: Mapped[str] = mapped_column(Text)
    source_system: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    business_unit: Mapped[str] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(Text)
    required_approvals: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(Text)
    provider_refund_id: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefundApproval(Base):
    __tablename__ = "refund_approvals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    refund_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    business_unit: Mapped[str] = mapped_column(Text)
    approver: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(Text)
    business_unit: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(Text)


class OutboxMessage(Base):
    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dedupe_key: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    business_unit: Mapped[str | None] = mapped_column(Text)
    recipient_role: Mapped[str | None] = mapped_column(Text)
    recipient: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    outbox_id: Mapped[int | None] = mapped_column(ForeignKey("outbox.id"), unique=True)
