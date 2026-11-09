"""Transactional outbox. `enqueue` writes a message in the caller's transaction; a worker
dispatches it after commit. A rolled-back transaction therefore never notifies anyone.

Dispatch (`dispatch_batch`, worker only, role `app_worker`) gives at-least-once delivery
with no external call inside a database transaction:
  1. claim (short transaction): due rows (dispatched_at IS NULL, available_at <= now(),
     attempts < max) are locked with `FOR UPDATE SKIP LOCKED` in small batches and leased:
     attempts + 1, available_at = now() + lease. Commit. Other dispatchers skip locked rows
     and, after the commit, the lease hides them.
  2. send through the payload's adapter (`notifications`). External adapters (webhook, email,
     log) run with no transaction open; transactional ones (in_app) run inside the step-3
     transaction, so their write commits atomically with the mark.
  3. record (short transaction), fenced on the claimed `attempts` value so a dispatcher whose
     lease expired cannot overwrite a newer claim: success -> dispatched_at = now();
     failure -> truncated last_error, available_at = now() + exponential backoff.
     attempts >= max and not dispatched -> dead letter (never claimed again).
A crash after an external send but before step 3 re-sends after the lease; adapters are
idempotent or identifiable on the message id (in_app: unique outbox_id; webhook header
X-Outbox-Message-Id; email Message-ID).
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import OutboxMessage

log = logging.getLogger("outbox")

DEFAULT_BATCH_SIZE = 10
DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_BASE_BACKOFF_SECONDS = 5
DEFAULT_MAX_BACKOFF_SECONDS = 3600
MAX_ERROR_LENGTH = 500
DEFAULT_ADAPTER = "log"
# Must exceed the slowest adapter call (webhook 5s / SMTP 10s timeouts).
DEFAULT_LEASE_SECONDS = 120


def enqueue(
    db: Session, *, topic: str, payload: dict[str, Any], dedupe_key: str | None = None
) -> bool:
    """Returns True if a row was inserted (False: an identical dedupe_key already exists)."""
    if not db.in_transaction():
        raise RuntimeError("outbox.enqueue must be called inside a transaction")
    # Core insert + inline(): no RETURNING, because app_api has INSERT but not SELECT on outbox.
    stmt = (
        insert(OutboxMessage.__table__)
        .values(topic=topic, payload=payload, dedupe_key=dedupe_key)
        .inline()
    )
    if dedupe_key is not None:
        stmt = stmt.on_conflict_do_nothing(index_elements=["dedupe_key"])
    # SQLAlchemy 2.1 only reports INSERT rowcount when asked to preserve it.
    return db.execute(stmt.execution_options(preserve_rowcount=True)).rowcount == 1


@dataclass(frozen=True)
class OutboxRecord:
    id: int
    topic: str
    payload: dict[str, Any]
    attempts: int  # including the current claim


class Adapter:
    """Delivers one message; raise to signal failure (the message is retried).

    `transactional = True` adapters get a Session inside the transaction that marks the message
    dispatched; others get `db=None` and must not touch the database."""

    name = "adapter"
    transactional = False

    def send(self, db: Session | None, message: OutboxRecord) -> None:
        raise NotImplementedError


@dataclass
class DispatchReport:
    claimed: int = 0
    sent: int = 0
    failed: int = 0
    dead_lettered: int = 0
    lost_lease: int = 0


def backoff_seconds(
    attempts: int,
    base: int = DEFAULT_BASE_BACKOFF_SECONDS,
    cap: int = DEFAULT_MAX_BACKOFF_SECONDS,
) -> int:
    """Delay after the `attempts`-th failed attempt: base, 2*base, 4*base, ... capped."""
    return min(cap, base * 2 ** max(0, attempts - 1))


def _describe(exc: Exception) -> str:
    if isinstance(exc, SQLAlchemyError):
        # The full message embeds SQL parameters; keep only the driver's first line.
        detail = str(getattr(exc, "orig", None) or exc).splitlines()[0]
    else:
        detail = str(exc)
    return f"{type(exc).__name__}: {detail}"[:MAX_ERROR_LENGTH]


_CLAIM = text(
    """
    UPDATE outbox o
    SET attempts = o.attempts + 1, available_at = now() + make_interval(secs => :lease)
    FROM (
        SELECT id FROM outbox
        WHERE dispatched_at IS NULL AND available_at <= now() AND attempts < :max_attempts
        ORDER BY available_at, id
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    ) due
    WHERE o.id = due.id
    RETURNING o.id, o.topic, o.payload, o.attempts
    """
)
_MARK_SENT = text(
    "UPDATE outbox SET dispatched_at = now(), last_error = NULL"
    " WHERE id = :id AND attempts = :attempts AND dispatched_at IS NULL"
)
_MARK_FAILED = text(
    "UPDATE outbox SET last_error = :error, available_at = now() + make_interval(secs => :delay)"
    " WHERE id = :id AND attempts = :attempts AND dispatched_at IS NULL"
)


class _LostLease(Exception):
    pass


def dispatch_batch(
    *,
    adapters: Mapping[str, Adapter] | None = None,
    session_factory: Callable[[], Session] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_backoff_seconds: int = DEFAULT_BASE_BACKOFF_SECONDS,
    max_backoff_seconds: int = DEFAULT_MAX_BACKOFF_SECONDS,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> DispatchReport:
    if adapters is None:
        from app.platform.notifications import default_adapters

        adapters = default_adapters()
    if session_factory is None:
        from app.db import WorkerSessionLocal

        session_factory = WorkerSessionLocal
    report = DispatchReport()

    with session_factory() as db, db.begin():
        rows = db.execute(
            _CLAIM, {"max_attempts": max_attempts, "limit": batch_size, "lease": lease_seconds}
        ).all()
    report.claimed = len(rows)

    for row in sorted(rows, key=lambda r: r.id):
        message = OutboxRecord(
            id=row.id, topic=row.topic, payload=row.payload or {}, attempts=row.attempts
        )
        fence = {"id": message.id, "attempts": message.attempts}
        adapter_name = message.payload.get("adapter") or DEFAULT_ADAPTER
        adapter = adapters.get(adapter_name)
        try:
            if adapter is None:
                raise LookupError(f"no adapter {adapter_name!r}")
            if adapter.transactional:
                with session_factory() as db, db.begin():
                    adapter.send(db, message)
                    if db.execute(_MARK_SENT, fence).rowcount != 1:
                        raise _LostLease  # rolls back the adapter's write too
            else:
                adapter.send(None, message)
                with session_factory() as db, db.begin():
                    if db.execute(_MARK_SENT, fence).rowcount != 1:
                        raise _LostLease
            report.sent += 1
        except _LostLease:
            report.lost_lease += 1
            log.warning("outbox message %s: lease lost before recording result", message.id)
        except Exception as exc:
            delay = backoff_seconds(message.attempts, base_backoff_seconds, max_backoff_seconds)
            with session_factory() as db, db.begin():
                updated = db.execute(
                    _MARK_FAILED, {**fence, "error": _describe(exc), "delay": delay}
                ).rowcount
            if updated != 1:
                report.lost_lease += 1
                continue
            report.failed += 1
            if message.attempts >= max_attempts:
                report.dead_lettered += 1
                log.error(
                    "outbox message %s dead-lettered after %d attempts (adapter=%s)",
                    message.id,
                    message.attempts,
                    adapter_name,
                )
            else:
                log.warning(
                    "outbox message %s failed (attempt %d, adapter=%s, retry in %ss)",
                    message.id,
                    message.attempts,
                    adapter_name,
                    delay,
                )
    return report


def dispatch_pending(*, max_batches: int = 10, **kwargs: Any) -> DispatchReport:
    """Dispatch batches until the queue is drained or `max_batches` is reached."""
    total = DispatchReport()
    batch_size = kwargs.get("batch_size", DEFAULT_BATCH_SIZE)
    for _ in range(max_batches):
        r = dispatch_batch(**kwargs)
        for f in ("claimed", "sent", "failed", "dead_lettered", "lost_lease"):
            setattr(total, f, getattr(total, f) + getattr(r, f))
        if r.claimed < batch_size:
            break
    return total
