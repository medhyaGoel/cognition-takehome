import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

RefundStatus = Literal[
    "pending_approval", "approved", "processing", "succeeded", "failed", "rejected"
]

_MAX_CENTS = 2**63 - 1


class RefundCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_id: uuid.UUID
    amount_cents: StrictInt = Field(gt=0, le=_MAX_CENTS)
    reason: str = Field(min_length=1, max_length=1000)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: StrictInt = Field(ge=1)
    comment: str | None = Field(default=None, max_length=1000)


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class PaymentOut(BaseModel):
    id: uuid.UUID
    provider_ref: str
    customer_name: str
    customer_email: str
    amount_cents: int
    currency: str
    business_unit: str
    reserved_cents: int
    refunded_cents: int
    refundable_cents: int
    captured_at: datetime


class RefundOut(BaseModel):
    id: uuid.UUID
    payment_id: uuid.UUID
    business_unit: str
    amount_cents: int
    currency: str
    reason: str
    status: RefundStatus
    requested_by: str
    required_approvals: int
    approvals_count: int
    provider_refund_id: str | None
    failure_reason: str | None
    version: int
    escalated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalOut(BaseModel):
    approver: str
    decision: Literal["approved", "rejected"]
    comment: str | None
    created_at: datetime


class RefundDetailOut(RefundOut):
    approvals: list[ApprovalOut]
    payment: PaymentOut


class AuditEntryOut(BaseModel):
    id: int
    occurred_at: datetime
    actor: str
    action: str
    entity_type: str
    entity_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str | None
