"""Append-only audit trail. Call `record` inside the same transaction as the change it
describes, so the audit row exists if and only if the change committed."""

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.platform.request_context import request_id_var


def record(
    db: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    if not db.in_transaction():
        raise RuntimeError("audit.record must be called inside a transaction")
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
            request_id=request_id_var.get(),
        )
    )
