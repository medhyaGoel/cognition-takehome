"""Feature-flag domain logic. Every write is one transaction (`with db.begin():`) holding the
state change, the change-request row, the audit row and the outbox/workflow event.

  * Every toggle inserts a flag_change_requests row keyed by (requested_by, Idempotency-Key)
    with ON CONFLICT DO NOTHING, so a replay returns the original outcome and never re-applies.
  * dev/staging: the flag state moves with a conditional UPDATE on (id, version); stale -> 409.
  * prod: the row starts `pending`. A different user approves (checked here and by the
    flag_change_requests_maker_checker trigger). Approval locks the request, applies the state
    change conditionally on the version the maker saw, and closes the request, atomically.
"""

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import RowMapping, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.apps.flags.schemas import DecideRequest, ToggleRequest
from app.models import AuditLog, FeatureFlag, FlagChangeRequest, FlagState
from app.platform import audit, outbox, workflows
from app.platform.auth import Principal
from app.platform.errors import Conflict, Forbidden, NotFound

F = FeatureFlag.__table__
S = FlagState.__table__
C = FlagChangeRequest.__table__

ENV_ORDER = {"dev": 0, "staging": 1, "prod": 2}
APPROVAL_ENVIRONMENTS = frozenset({"prod"})


def _state_select():
    return select(S, F.c.description).join(F, F.c.key == S.c.flag_key)


def list_states(db: Session) -> list[RowMapping]:
    with db.begin():
        rows = db.execute(_state_select()).mappings().all()
    return sorted(
        rows, key=lambda r: (r["flag_key"], r["business_unit"], ENV_ORDER[r["environment"]])
    )


def _get_state(db: Session, state_id: uuid.UUID, *, lock: bool = False) -> RowMapping:
    stmt = _state_select().where(S.c.id == state_id)
    if lock:
        stmt = stmt.with_for_update(of=S)
    row = db.execute(stmt).mappings().one_or_none()
    if row is None:
        raise NotFound("flag state not found")
    return row


def get_state_audit(db: Session, state_id: uuid.UUID) -> list[AuditLog]:
    with db.begin():
        _get_state(db, state_id)
        return list(
            db.scalars(
                select(AuditLog)
                .where(AuditLog.entity_type == "flag_state", AuditLog.entity_id == str(state_id))
                .order_by(AuditLog.id)
            )
        )


def list_change_requests(db: Session, *, status: str | None) -> list[RowMapping]:
    stmt = select(C).order_by(C.c.created_at.desc()).limit(200)
    if status is not None:
        stmt = stmt.where(C.c.status == status)
    with db.begin():
        return list(db.execute(stmt).mappings().all())


def _snapshot(s: Mapping[str, Any]) -> dict[str, Any]:
    return {"enabled": s["enabled"], "version": s["version"]}


def _request_hash(state_id: uuid.UUID, body: ToggleRequest) -> str:
    canonical = json.dumps(
        {"state": str(state_id), "enabled": body.enabled, "v": body.expected_version},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _emit(db: Session, name: str, cr: Mapping[str, Any], actor: str) -> None:
    data = {
        "flag_key": cr["flag_key"],
        "business_unit": cr["business_unit"],
        "environment": cr["environment"],
        "desired_enabled": cr["desired_enabled"],
        "state": "on" if cr["desired_enabled"] else "off",
        "status": cr["status"],
        "change_request_id": str(cr["id"]),
    }
    outbox.enqueue(
        db,
        topic=name,
        payload={"adapter": "log", "event": name, "entity_id": str(cr["flag_state_id"]), **data},
        dedupe_key=f"{name}:{cr['id']}",
    )
    workflows.on_event(
        db,
        workflows.DomainEvent(
            name=name,
            entity_type="flag_change_request",
            entity_id=str(cr["id"]),
            actor=actor,
            data=data,
        ),
    )


def _apply(db: Session, state: Mapping[str, Any], enabled: bool, version: int, actor: str):
    """Conditional update on (id, version); bumps version. Zero rows -> 409."""
    row = (
        db.execute(
            update(S)
            .where(S.c.id == state["id"], S.c.version == version)
            .values(
                enabled=enabled, version=S.c.version + 1, updated_at=func.now(), updated_by=actor
            )
            .returning(*S.c)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise Conflict("flag was modified concurrently; refetch", code="version_conflict")
    return row


def _find_by_key(db: Session, actor: str, key: str) -> RowMapping | None:
    stmt = select(C).where(C.c.requested_by == actor, C.c.idempotency_key == key)
    return db.execute(stmt).mappings().one_or_none()


def _replay(existing: RowMapping, req_hash: str) -> RowMapping:
    if existing["request_hash"] != req_hash:
        raise Conflict(
            "Idempotency-Key was already used with a different request",
            code="idempotency_mismatch",
        )
    return existing


def toggle(
    db: Session,
    principal: Principal,
    state_id: uuid.UUID,
    idempotency_key: str,
    body: ToggleRequest,
) -> tuple[RowMapping, bool]:
    """Returns (change request, created). dev/staging -> `applied`; prod -> `pending`."""
    actor = principal.subject
    req_hash = _request_hash(state_id, body)
    with db.begin():
        existing = _find_by_key(db, actor, idempotency_key)
        if existing is not None:
            return _replay(existing, req_hash), False
        state = _get_state(db, state_id, lock=True)
        if state["version"] != body.expected_version:
            raise Conflict("flag was modified concurrently; refetch", code="version_conflict")
        if state["enabled"] == body.enabled:
            raise Conflict(f"flag is already {'on' if body.enabled else 'off'}", code="no_change")
        needs_approval = state["environment"] in APPROVAL_ENVIRONMENTS
        cr = (
            db.execute(
                pg_insert(C)
                .values(
                    id=uuid.uuid4(),
                    flag_state_id=state["id"],
                    flag_key=state["flag_key"],
                    business_unit=state["business_unit"],
                    environment=state["environment"],
                    desired_enabled=body.enabled,
                    expected_version=body.expected_version,
                    status="pending" if needs_approval else "applied",
                    requested_by=actor,
                    idempotency_key=idempotency_key,
                    request_hash=req_hash,
                )
                .on_conflict_do_nothing(constraint="flag_change_requests_idempotency")
                .returning(*C.c)
            )
            .mappings()
            .one_or_none()
        )
        if cr is None:  # a concurrent replay with the same key won the insert
            return _replay(_find_by_key(db, actor, idempotency_key), req_hash), False
        common = dict(
            actor=actor,
            entity_type="flag_state",
            entity_id=str(state["id"]),
            business_unit=state["business_unit"],
        )
        if needs_approval:
            audit.record(
                db,
                action="flag.change_requested",
                before=_snapshot(state),
                after={"enabled": body.enabled, "change_request_id": str(cr["id"])},
                **common,
            )
            _emit(db, "flag.change_requested", cr, actor)
        else:
            after = _apply(db, state, body.enabled, body.expected_version, actor)
            audit.record(
                db,
                action="flag.toggled",
                before=_snapshot(state),
                after={**_snapshot(after), "change_request_id": str(cr["id"])},
                **common,
            )
            _emit(db, "flag.toggled", cr, actor)
    return cr, True


def _decide(
    db: Session,
    principal: Principal,
    cr_id: uuid.UUID,
    body: DecideRequest,
    *,
    approve: bool,
) -> RowMapping:
    actor = principal.subject
    with db.begin():
        cr = db.execute(select(C).where(C.c.id == cr_id).with_for_update()).mappings().one_or_none()
        if cr is None:
            raise NotFound("change request not found")
        if cr["status"] != "pending":
            raise Conflict(f"change request is already {cr['status']}", code="invalid_state")
        if cr["version"] != body.expected_version:
            raise Conflict("change request was modified; refetch", code="version_conflict")
        if cr["requested_by"] == actor:
            raise Forbidden("you cannot decide your own change request", code="self_approval")
        state = _get_state(db, cr["flag_state_id"], lock=True)
        if approve:
            after = _apply(db, state, cr["desired_enabled"], cr["expected_version"], actor)
        decided = (
            db.execute(
                update(C)
                .where(C.c.id == cr_id, C.c.status == "pending", C.c.version == cr["version"])
                .values(
                    status="approved" if approve else "rejected",
                    decided_by=actor,
                    decided_at=func.now(),
                    decision_reason=body.reason,
                    version=C.c.version + 1,
                )
                .returning(*C.c)
            )
            .mappings()
            .one_or_none()
        )
        if decided is None:
            raise Conflict("change request was modified; refetch", code="version_conflict")
        action = "flag.change_approved" if approve else "flag.change_rejected"
        audit.record(
            db,
            actor=actor,
            action=action,
            entity_type="flag_state",
            entity_id=str(state["id"]),
            business_unit=state["business_unit"],
            before=_snapshot(state),
            after={
                **(_snapshot(after) if approve else _snapshot(state)),
                "change_request_id": str(cr_id),
                "requested_by": cr["requested_by"],
                "reason": body.reason,
            },
        )
        _emit(db, action, decided, actor)
    return decided


def approve(db: Session, principal: Principal, cr_id: uuid.UUID, body: DecideRequest):
    return _decide(db, principal, cr_id, body, approve=True)


def reject(db: Session, principal: Principal, cr_id: uuid.UUID, body: DecideRequest):
    return _decide(db, principal, cr_id, body, approve=False)
