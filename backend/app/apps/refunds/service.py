"""Refunds domain logic. Every write is one transaction (`with db.begin():`) that contains the
state change, the ledger movement on the payment, the audit row, and the workflow event.

Concurrency model:
  * Creation reserves money with a single conditional UPDATE on the payment row
    (`reserved_cents + amount <= amount_cents`); Postgres row locks serialize contenders.
  * Idempotency: (requested_by, idempotency_key) is UNIQUE. The refund row is inserted with
    ON CONFLICT DO NOTHING *before* reserving, so a concurrent replay blocks on the first
    insert and then returns the committed refund instead of reserving twice.
  * Decisions lock the refund (SELECT ... FOR UPDATE) and transition with a conditional
    UPDATE on (id, status, version) that bumps version. Zero rows -> 409.
"""

import csv
import hashlib
import io
import json
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

from sqlalchemy import RowMapping, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.apps.refunds.schemas import ApproveRequest, RefundCreate, RejectRequest
from app.models import AuditLog, Payment, Refund, RefundApproval
from app.platform import audit, workflows
from app.platform.auth import Principal
from app.platform.errors import Conflict, Forbidden, NotFound
from app.platform.masking import can_view_pii, mask_email

P = Payment.__table__
R = Refund.__table__
A = RefundApproval.__table__

DEFAULT_REQUIRED_APPROVALS = 1
PAYMENT_SEARCH_LIMIT = 50

_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"


def _approved_count_expr():
    return (
        select(func.count())
        .select_from(A)
        .where(A.c.refund_id == R.c.id, A.c.decision == "approved")
        .correlate(R)
        .scalar_subquery()
        .label("approvals_count")
    )


def _refund_select():
    return select(R, _approved_count_expr())


def request_hash(body: RefundCreate) -> str:
    canonical = json.dumps(
        {
            "amount_cents": body.amount_cents,
            "payment_id": str(body.payment_id),
            "reason": body.reason,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def snapshot(r: Mapping[str, Any]) -> dict[str, Any]:
    """Audit-safe view of a refund (no customer PII)."""
    return {
        "status": r["status"],
        "version": r["version"],
        "amount_cents": r["amount_cents"],
        "currency": r["currency"],
        "payment_id": str(r["payment_id"]),
        "required_approvals": r["required_approvals"],
        "provider_refund_id": r["provider_refund_id"],
        "failure_reason": r["failure_reason"],
    }


def emit(
    db: Session, name: str, r: Mapping[str, Any], actor: str, **extra: Any
) -> workflows.WorkflowOutcome:
    data = {
        "amount_cents": r["amount_cents"],
        "currency": r["currency"],
        "status": r["status"],
        "requested_by": r["requested_by"],
        "payment_id": str(r["payment_id"]),
        "business_unit": r["business_unit"],
        **extra,
    }
    return workflows.on_event(
        db,
        workflows.DomainEvent(
            name=name, entity_type="refund", entity_id=str(r["id"]), actor=actor, data=data
        ),
    )


def try_transition(
    db: Session, r: Mapping[str, Any], *, to: str, **values: Any
) -> RowMapping | None:
    """Conditional update on (id, status, version); bumps version. None if the row moved."""
    stmt = (
        update(R)
        .where(R.c.id == r["id"], R.c.status == r["status"], R.c.version == r["version"])
        .values(status=to, version=R.c.version + 1, updated_at=func.now(), **values)
        .returning(*R.c)
    )
    return db.execute(stmt).mappings().one_or_none()


def transition(db: Session, r: Mapping[str, Any], *, to: str, **values: Any) -> RowMapping:
    row = try_transition(db, r, to=to, **values)
    if row is None:
        raise Conflict("refund was modified concurrently; refetch", code="version_conflict")
    return row


def release_reservation(db: Session, r: Mapping[str, Any]) -> None:
    amount = r["amount_cents"]
    updated = db.execute(
        update(P)
        .where(P.c.id == r["payment_id"], P.c.reserved_cents - amount >= P.c.refunded_cents)
        .values(reserved_cents=P.c.reserved_cents - amount)
    ).rowcount
    if updated != 1:
        raise RuntimeError("payment ledger invariant violated while releasing reservation")


def move_reservation_to_refunded(db: Session, r: Mapping[str, Any]) -> None:
    amount = r["amount_cents"]
    updated = db.execute(
        update(P)
        .where(P.c.id == r["payment_id"], P.c.refunded_cents + amount <= P.c.reserved_cents)
        .values(refunded_cents=P.c.refunded_cents + amount)
    ).rowcount
    if updated != 1:
        raise RuntimeError("payment ledger invariant violated while recording refund")


# --- serialization -------------------------------------------------------------------------


def payment_out(row: Mapping[str, Any], principal: Principal) -> dict[str, Any]:
    email = row["customer_email"]
    return {
        "id": row["id"],
        "provider_ref": row["provider_ref"],
        "customer_name": row["customer_name"],
        "customer_email": email if can_view_pii(principal) else mask_email(email),
        "amount_cents": row["amount_cents"],
        "currency": row["currency"],
        "business_unit": row["business_unit"],
        "reserved_cents": row["reserved_cents"],
        "refunded_cents": row["refunded_cents"],
        "refundable_cents": row["amount_cents"] - row["reserved_cents"],
        "captured_at": row["captured_at"],
    }


def _refund_out(row: Mapping[str, Any], approvals_count: int | None = None) -> dict[str, Any]:
    out = {k: v for k, v in row.items() if k not in ("idempotency_key", "request_hash")}
    if approvals_count is not None:
        out["approvals_count"] = approvals_count
    return out


# --- payments (read-only mirror) -----------------------------------------------------------


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_payments(db: Session, principal: Principal, q: str | None) -> list[dict[str, Any]]:
    stmt = select(P).order_by(P.c.captured_at.desc(), P.c.id).limit(PAYMENT_SEARCH_LIMIT)
    if q:
        pattern = f"%{_escape_like(q)}%"
        stmt = stmt.where(
            or_(
                P.c.provider_ref.ilike(pattern, escape="\\"),
                P.c.customer_name.ilike(pattern, escape="\\"),
            )
        )
    with db.begin():
        rows = db.execute(stmt).mappings().all()
    return [payment_out(r, principal) for r in rows]


def _get_payment_row(db: Session, payment_id: uuid.UUID) -> RowMapping:
    row = db.execute(select(P).where(P.c.id == payment_id)).mappings().one_or_none()
    if row is None:
        raise NotFound("payment not found")
    return row


def get_payment(db: Session, principal: Principal, payment_id: uuid.UUID) -> dict[str, Any]:
    with db.begin():
        return payment_out(_get_payment_row(db, payment_id), principal)


# --- refunds: reads ------------------------------------------------------------------------


def _get_refund_row(db: Session, refund_id: uuid.UUID) -> RowMapping:
    row = db.execute(_refund_select().where(R.c.id == refund_id)).mappings().one_or_none()
    if row is None:
        raise NotFound("refund not found")
    return row


def list_refunds(
    db: Session, *, status: str | None, limit: int, offset: int
) -> list[dict[str, Any]]:
    stmt = _refund_select().order_by(R.c.created_at.desc(), R.c.id.desc())
    if status:
        stmt = stmt.where(R.c.status == status)
    with db.begin():
        rows = db.execute(stmt.limit(limit).offset(offset)).mappings().all()
    return [_refund_out(r) for r in rows]


def get_refund_detail(db: Session, principal: Principal, refund_id: uuid.UUID) -> dict[str, Any]:
    with db.begin():
        refund = _get_refund_row(db, refund_id)
        approvals = (
            db.execute(
                select(A.c.approver, A.c.decision, A.c.comment, A.c.created_at)
                .where(A.c.refund_id == refund_id)
                .order_by(A.c.created_at, A.c.id)
            )
            .mappings()
            .all()
        )
        payment = _get_payment_row(db, refund["payment_id"])
    return {
        **_refund_out(refund),
        "approvals": [dict(a) for a in approvals],
        "payment": payment_out(payment, principal),
    }


def get_refund_audit(db: Session, refund_id: uuid.UUID) -> list[AuditLog]:
    with db.begin():
        _get_refund_row(db, refund_id)
        return list(
            db.scalars(
                select(AuditLog)
                .where(AuditLog.entity_type == "refund", AuditLog.entity_id == str(refund_id))
                .order_by(AuditLog.id)
            )
        )


# --- refunds: create -----------------------------------------------------------------------


def _find_by_key(db: Session, requested_by: str, key: str) -> RowMapping | None:
    stmt = _refund_select().where(R.c.requested_by == requested_by, R.c.idempotency_key == key)
    return db.execute(stmt).mappings().one_or_none()


def _replay(existing: RowMapping | None, req_hash: str) -> dict[str, Any]:
    if existing is None or existing["request_hash"] != req_hash:
        raise Conflict(
            "Idempotency-Key was already used with a different request",
            code="idempotency_mismatch",
        )
    return _refund_out(existing)


def create_refund(
    db: Session, principal: Principal, idempotency_key: str, body: RefundCreate
) -> tuple[dict[str, Any], bool]:
    """Returns (refund, created). created=False means an idempotent replay."""
    actor = principal.subject
    req_hash = request_hash(body)
    with db.begin():
        existing = _find_by_key(db, actor, idempotency_key)
        if existing is not None:
            return _replay(existing, req_hash), False

        payment = _get_payment_row(db, body.payment_id)
        refund = (
            db.execute(
                pg_insert(R)
                .values(
                    id=uuid.uuid4(),
                    payment_id=payment["id"],
                    business_unit=payment["business_unit"],
                    amount_cents=body.amount_cents,
                    currency=payment["currency"],
                    reason=body.reason,
                    status="pending_approval",
                    requested_by=actor,
                    required_approvals=DEFAULT_REQUIRED_APPROVALS,
                    idempotency_key=idempotency_key,
                    request_hash=req_hash,
                )
                .on_conflict_do_nothing(constraint="refunds_idempotency")
                .returning(*R.c)
            )
            .mappings()
            .one_or_none()
        )
        if refund is None:  # a concurrent request with this key committed first
            return _replay(_find_by_key(db, actor, idempotency_key), req_hash), False

        reserved = db.execute(
            update(P)
            .where(
                P.c.id == payment["id"],
                P.c.reserved_cents + body.amount_cents <= P.c.amount_cents,
            )
            .values(reserved_cents=P.c.reserved_cents + body.amount_cents)
            .returning(P.c.id)
        ).first()
        if reserved is None:
            raise Conflict(
                "amount exceeds the payment's refundable amount",
                code="insufficient_refundable_amount",
            )

        outcome = emit(db, "refund.created", refund, actor)
        rules = list(outcome.matched_rules)
        required = (
            DEFAULT_REQUIRED_APPROVALS
            if outcome.required_approvals is None
            else outcome.required_approvals
        )
        if required != refund["required_approvals"]:
            refund = (
                db.execute(
                    update(R)
                    .where(R.c.id == refund["id"])
                    .values(required_approvals=required)
                    .returning(*R.c)
                )
                .mappings()
                .one()
            )
        audit.record(
            db,
            actor=actor,
            action="refund.created",
            entity_type="refund",
            entity_id=str(refund["id"]),
            business_unit=refund["business_unit"],
            after={**snapshot(refund), "reason": refund["reason"], "rules": rules},
        )

        if outcome.auto_approve or required == 0:
            approved = transition(db, refund, to="approved")
            audit.record(
                db,
                actor=actor,
                action="refund.auto_approved",
                entity_type="refund",
                entity_id=str(refund["id"]),
                business_unit=refund["business_unit"],
                before=snapshot(refund),
                after={**snapshot(approved), "rules": rules},
            )
            emit(db, "refund.approved", approved, actor, auto_approved=True)
            refund = approved
        return _refund_out(refund, approvals_count=0), True


# --- refunds: decisions --------------------------------------------------------------------


def _lock_for_decision(
    db: Session, principal: Principal, refund_id: uuid.UUID, expected_version: int
) -> RowMapping:
    r = db.execute(select(R).where(R.c.id == refund_id).with_for_update()).mappings().one_or_none()
    if r is None:
        raise NotFound("refund not found")
    if r["requested_by"] == principal.subject:
        raise Forbidden("the requester cannot approve or reject their own refund")
    if r["version"] != expected_version:
        raise Conflict("refund was modified; refetch", code="version_conflict")
    if r["status"] != "pending_approval":
        raise Conflict(f"refund is {r['status']}", code="invalid_transition")
    return r


def _insert_decision(
    db: Session, principal: Principal, r: Mapping[str, Any], decision: str, comment: str | None
) -> None:
    try:
        db.execute(
            insert(A).values(
                refund_id=r["id"],
                business_unit=r["business_unit"],
                approver=principal.subject,
                decision=decision,
                comment=comment,
            )
        )
    except IntegrityError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate == _UNIQUE_VIOLATION:
            raise Conflict("you have already decided this refund") from exc
        if sqlstate == _CHECK_VIOLATION:
            raise Forbidden("the requester cannot decide their own refund") from exc
        raise


def _approved_count(db: Session, refund_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count())
        .select_from(A)
        .where(A.c.refund_id == refund_id, A.c.decision == "approved")
    )


def approve_refund(
    db: Session, principal: Principal, refund_id: uuid.UUID, body: ApproveRequest
) -> dict[str, Any]:
    actor = principal.subject
    with db.begin():
        r = _lock_for_decision(db, principal, refund_id, body.expected_version)
        _insert_decision(db, principal, r, "approved", body.comment)
        count = _approved_count(db, r["id"])
        to = "approved" if count >= r["required_approvals"] else "pending_approval"
        new = transition(db, r, to=to)
        audit.record(
            db,
            actor=actor,
            action="refund.approved" if to == "approved" else "refund.approval_recorded",
            entity_type="refund",
            entity_id=str(r["id"]),
            business_unit=r["business_unit"],
            before={**snapshot(r), "approvals_count": count - 1},
            after={**snapshot(new), "approvals_count": count},
        )
        if to == "approved":
            emit(db, "refund.approved", new, actor)
        return _refund_out(new, approvals_count=count)


def reject_refund(
    db: Session, principal: Principal, refund_id: uuid.UUID, body: RejectRequest
) -> dict[str, Any]:
    actor = principal.subject
    with db.begin():
        r = _lock_for_decision(db, principal, refund_id, body.expected_version)
        _insert_decision(db, principal, r, "rejected", body.reason)
        new = transition(db, r, to="rejected")
        release_reservation(db, r)
        audit.record(
            db,
            actor=actor,
            action="refund.rejected",
            entity_type="refund",
            entity_id=str(r["id"]),
            business_unit=r["business_unit"],
            before=snapshot(r),
            after={**snapshot(new), "reason": body.reason},
        )
        emit(db, "refund.rejected", new, actor)
        return _refund_out(new, approvals_count=_approved_count(db, r["id"]))


# --- CSV export ----------------------------------------------------------------------------

CSV_COLUMNS = [
    "refund_id",
    "created_at",
    "updated_at",
    "status",
    "business_unit",
    "payment_id",
    "payment_provider_ref",
    "amount",
    "amount_cents",
    "currency",
    "requested_by",
    "reason",
    "provider_refund_id",
    "failure_reason",
]
_MINOR_UNITS = {"JPY": 0, "KRW": 0, "BHD": 3, "KWD": 3}


def format_minor_units(cents: int, currency: str) -> str:
    """Integer minor units -> decimal string, without floats ("-12.05")."""
    digits = _MINOR_UNITS.get(currency.upper(), 2)
    sign = "-" if cents < 0 else ""
    major, minor = divmod(abs(cents), 10**digits)
    return f"{sign}{major}.{minor:0{digits}d}" if digits else f"{sign}{major}"


def _csv_safe(value: str | None) -> str:
    """Neutralize spreadsheet formula injection in free-text cells."""
    if not value:
        return ""
    return "'" + value if value[0] in "=+-@\t\r" else value


def export_refunds_csv(db: Session, *, status: str | None) -> Iterator[str]:
    stmt = (
        select(R, P.c.provider_ref)
        .join(P, P.c.id == R.c.payment_id)
        .order_by(R.c.created_at, R.c.id)
        .execution_options(yield_per=500)
    )
    if status:
        stmt = stmt.where(R.c.status == status)

    buf = io.StringIO()
    writer = csv.writer(buf)

    def take() -> str:
        value = buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        return value

    writer.writerow(CSV_COLUMNS)
    yield take()
    with db.begin():
        for r in db.execute(stmt).mappings():
            writer.writerow(
                [
                    r["id"],
                    r["created_at"].isoformat(),
                    r["updated_at"].isoformat(),
                    r["status"],
                    r["business_unit"],
                    r["payment_id"],
                    _csv_safe(r["provider_ref"]),
                    format_minor_units(r["amount_cents"], r["currency"]),
                    r["amount_cents"],
                    r["currency"],
                    _csv_safe(r["requested_by"]),
                    _csv_safe(r["reason"]),
                    _csv_safe(r["provider_refund_id"]),
                    _csv_safe(r["failure_reason"]),
                ]
            )
            yield take()
