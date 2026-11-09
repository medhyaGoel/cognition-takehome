"""Transactional outbox. `enqueue` writes a message in the caller's transaction; a worker
dispatches it after commit. A rolled-back transaction therefore never notifies anyone."""

from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import OutboxMessage


def enqueue(
    db: Session, *, topic: str, payload: dict[str, Any], dedupe_key: str | None = None
) -> None:
    if not db.in_transaction():
        raise RuntimeError("outbox.enqueue must be called inside a transaction")
    stmt = insert(OutboxMessage).values(topic=topic, payload=payload, dedupe_key=dedupe_key)
    if dedupe_key is not None:
        stmt = stmt.on_conflict_do_nothing(index_elements=["dedupe_key"])
    db.execute(stmt)
