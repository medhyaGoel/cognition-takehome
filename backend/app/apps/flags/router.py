import uuid

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.orm import Session

from app.apps.flags import service
from app.apps.flags.schemas import (
    ChangeRequestOut,
    ChangeStatus,
    DecideRequest,
    FlagStateOut,
    ToggleRequest,
)
from app.apps.refunds.schemas import AuditEntryOut
from app.db import get_db
from app.platform.auth import Principal, Role, require_roles

router = APIRouter(prefix="/api/flags", tags=["flags"])

READ_ROLES = (
    Role.VIEWER,
    Role.REFUND_AGENT,
    Role.REFUND_APPROVER,
    Role.FINANCE_ADMIN,
    Role.FLAG_EDITOR,
    Role.FLAG_APPROVER,
)
EDIT_ROLES = (Role.FLAG_EDITOR,)
APPROVE_ROLES = (Role.FLAG_APPROVER,)


@router.get("", response_model=list[FlagStateOut])
def list_flags(
    _: Principal = Depends(require_roles(*READ_ROLES)),
    db: Session = Depends(get_db),
):
    return service.list_states(db)


@router.get("/change-requests", response_model=list[ChangeRequestOut])
def list_change_requests(
    status: ChangeStatus | None = None,
    _: Principal = Depends(require_roles(*READ_ROLES)),
    db: Session = Depends(get_db),
):
    return service.list_change_requests(db, status=status)


@router.post("/states/{state_id}/toggle", response_model=ChangeRequestOut, status_code=201)
def toggle_flag(
    state_id: uuid.UUID,
    body: ToggleRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    principal: Principal = Depends(require_roles(*EDIT_ROLES)),
    db: Session = Depends(get_db),
):
    change, created = service.toggle(db, principal, state_id, idempotency_key, body)
    if not created:
        response.status_code = 200
    return change


@router.get("/states/{state_id}/audit", response_model=list[AuditEntryOut])
def get_flag_audit(
    state_id: uuid.UUID,
    _: Principal = Depends(require_roles(*READ_ROLES)),
    db: Session = Depends(get_db),
):
    return service.get_state_audit(db, state_id)


@router.post("/change-requests/{cr_id}/approve", response_model=ChangeRequestOut)
def approve_change(
    cr_id: uuid.UUID,
    body: DecideRequest,
    principal: Principal = Depends(require_roles(*APPROVE_ROLES)),
    db: Session = Depends(get_db),
):
    return service.approve(db, principal, cr_id, body)


@router.post("/change-requests/{cr_id}/reject", response_model=ChangeRequestOut)
def reject_change(
    cr_id: uuid.UUID,
    body: DecideRequest,
    principal: Principal = Depends(require_roles(*APPROVE_ROLES)),
    db: Session = Depends(get_db),
):
    return service.reject(db, principal, cr_id, body)
