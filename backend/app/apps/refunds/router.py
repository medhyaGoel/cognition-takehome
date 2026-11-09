import uuid

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.apps.refunds import service
from app.apps.refunds.schemas import (
    ApproveRequest,
    AuditEntryOut,
    PaymentOut,
    RefundCreate,
    RefundDetailOut,
    RefundOut,
    RefundStatus,
    RejectRequest,
)
from app.db import get_db
from app.platform.auth import Principal, Role, require_roles

router = APIRouter(prefix="/api", tags=["refunds"])

ALL_READ = (Role.VIEWER, Role.REFUND_AGENT, Role.REFUND_APPROVER, Role.FINANCE_ADMIN)
CREATE_ROLES = (Role.REFUND_AGENT, Role.FINANCE_ADMIN)
DECIDE_ROLES = (Role.REFUND_APPROVER, Role.FINANCE_ADMIN)
EXPORT_ROLES = (Role.FINANCE_ADMIN,)


@router.get("/payments", response_model=list[PaymentOut])
def list_payments(
    q: str | None = Query(default=None, max_length=100),
    principal: Principal = Depends(require_roles(*ALL_READ)),
    db: Session = Depends(get_db),
):
    return service.list_payments(db, principal, q)


@router.get("/payments/{payment_id}", response_model=PaymentOut)
def get_payment(
    payment_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*ALL_READ)),
    db: Session = Depends(get_db),
):
    return service.get_payment(db, principal, payment_id)


@router.get("/refunds", response_model=list[RefundOut])
def list_refunds(
    status: RefundStatus | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: Principal = Depends(require_roles(*ALL_READ)),
    db: Session = Depends(get_db),
):
    return service.list_refunds(db, status=status, limit=limit, offset=offset)


@router.post("/refunds", response_model=RefundOut, status_code=201)
def create_refund(
    body: RefundCreate,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    principal: Principal = Depends(require_roles(*CREATE_ROLES)),
    db: Session = Depends(get_db),
):
    refund, created = service.create_refund(db, principal, idempotency_key, body)
    if not created:
        response.status_code = 200
    return refund


# Declared before /refunds/{refund_id} so "export.csv" is not parsed as an id.
@router.get("/refunds/export.csv", response_class=StreamingResponse)
def export_refunds_csv(
    status: RefundStatus | None = None,
    _: Principal = Depends(require_roles(*EXPORT_ROLES)),
    db: Session = Depends(get_db),
):
    return StreamingResponse(
        service.export_refunds_csv(db, status=status),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="refunds.csv"'},
    )


@router.get("/refunds/{refund_id}", response_model=RefundDetailOut)
def get_refund(
    refund_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*ALL_READ)),
    db: Session = Depends(get_db),
):
    return service.get_refund_detail(db, principal, refund_id)


@router.post("/refunds/{refund_id}/approve", response_model=RefundOut)
def approve_refund(
    refund_id: uuid.UUID,
    body: ApproveRequest,
    principal: Principal = Depends(require_roles(*DECIDE_ROLES)),
    db: Session = Depends(get_db),
):
    return service.approve_refund(db, principal, refund_id, body)


@router.post("/refunds/{refund_id}/reject", response_model=RefundOut)
def reject_refund(
    refund_id: uuid.UUID,
    body: RejectRequest,
    principal: Principal = Depends(require_roles(*DECIDE_ROLES)),
    db: Session = Depends(get_db),
):
    return service.reject_refund(db, principal, refund_id, body)


@router.get("/refunds/{refund_id}/audit", response_model=list[AuditEntryOut])
def get_refund_audit(
    refund_id: uuid.UUID,
    _: Principal = Depends(require_roles(*ALL_READ)),
    db: Session = Depends(get_db),
):
    return service.get_refund_audit(db, refund_id)
