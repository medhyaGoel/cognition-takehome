"""Refund execution against the payment provider (runs in the worker, never in a request).

    approved --claim (commit)--> processing --provider.refund(key=refund id)--> succeeded|failed
                                                                               (commit)

* Claiming uses SELECT ... FOR UPDATE SKIP LOCKED plus a conditional (id, status, version)
  update, so concurrent workers never claim the same refund.
* The provider is called outside any DB transaction with idempotency key = refund id.
* A provider exception means "outcome unknown": the refund stays `processing`.
  `reconcile_stuck_processing` re-calls the idempotent provider for such rows; because the key
  is the same, money moves at most once, and the conditional finalize records it at most once.
* All of this runs as `app_worker`; audit actor is `system:worker`.
"""

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.apps.refunds import service
from app.apps.refunds.service import P, R
from app.db import WORKER_ACTOR, WorkerSessionLocal
from app.platform import audit
from app.platform.connectors.payment_provider import (
    PaymentProvider,
    ProviderRefundResult,
    get_provider,
)

log = logging.getLogger(__name__)

BATCH_SIZE = 20
STUCK_AFTER = timedelta(minutes=5)


def _claimable():
    return (
        select(R, P.c.provider_ref.label("payment_ref"))
        .join(P, P.c.id == R.c.payment_id)
        .order_by(R.c.updated_at, R.c.id)
        .with_for_update(of=R, skip_locked=True)
    )


def _audit(db: Session, action: str, before: Any, after: Any, **extra: Any) -> None:
    audit.record(
        db,
        actor=WORKER_ACTOR,
        action=action,
        entity_type="refund",
        entity_id=str(after["id"]),
        business_unit=after["business_unit"],
        before=service.snapshot(before),
        after={**service.snapshot(after), **extra},
    )


def _finalize(
    db: Session, r: dict[str, Any], result: ProviderRefundResult, *, reconciled: bool
) -> bool:
    with db.begin():
        if result.succeeded:
            new = service.try_transition(
                db, r, to="succeeded", provider_refund_id=result.provider_refund_id
            )
            if new is None:
                return False
            service.move_reservation_to_refunded(db, r)
            _audit(db, "refund.succeeded", r, new, reconciled=reconciled)
            service.emit(
                db,
                "refund.succeeded",
                new,
                WORKER_ACTOR,
                provider_refund_id=new["provider_refund_id"],
            )
        else:
            new = service.try_transition(
                db, r, to="failed", failure_reason=result.failure_reason or "provider_failure"
            )
            if new is None:
                return False
            service.release_reservation(db, r)
            _audit(db, "refund.failed", r, new, reconciled=reconciled)
            service.emit(
                db, "refund.failed", new, WORKER_ACTOR, failure_reason=new["failure_reason"]
            )
    return True


def _execute(
    db: Session, provider: PaymentProvider, r: dict[str, Any], *, reconciled: bool
) -> bool:
    try:
        result = provider.refund(
            idempotency_key=str(r["id"]),
            payment_ref=r["payment_ref"],
            amount_cents=r["amount_cents"],
            currency=r["currency"],
        )
    except Exception:
        log.exception("provider call failed for refund %s; left in processing", r["id"])
        return False
    if not _finalize(db, r, result, reconciled=reconciled):
        log.info("refund %s already finalized by another worker", r["id"])
        return False
    return True


def run_once(db: Session, provider: PaymentProvider, *, batch_size: int = BATCH_SIZE) -> int:
    """Claim approved refunds, execute them, record results. Returns the number claimed."""
    claimed: list[dict[str, Any]] = []
    with db.begin():
        rows = db.execute(_claimable().where(R.c.status == "approved").limit(batch_size))
        for r in rows.mappings().all():
            new = service.transition(db, r, to="processing")
            _audit(db, "refund.processing", r, new)
            claimed.append({**new, "payment_ref": r["payment_ref"]})
    for r in claimed:
        _execute(db, provider, r, reconciled=False)
    return len(claimed)


def reconcile_stuck_processing(
    db: Session,
    provider: PaymentProvider,
    older_than: timedelta = STUCK_AFTER,
    *,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Re-call the idempotent provider for refunds stuck in `processing`. Returns the number
    finalized. Picked rows get a fresh `updated_at` lease so other reconcilers skip them."""
    picked: list[dict[str, Any]] = []
    with db.begin():
        rows = db.execute(
            _claimable()
            .where(R.c.status == "processing", R.c.updated_at < func.now() - older_than)
            .limit(batch_size)
        )
        for r in rows.mappings().all():
            db.execute(
                update(R)
                .where(R.c.id == r["id"], R.c.status == "processing", R.c.version == r["version"])
                .values(updated_at=func.now())
            )
            picked.append(dict(r))
    return sum(_execute(db, provider, r, reconciled=True) for r in picked)


def execute_pending() -> int:
    """Worker entry point: one pass of execution plus reconciliation, as app_worker."""
    provider = get_provider()
    with WorkerSessionLocal() as db:
        return run_once(db, provider) + reconcile_stuck_processing(db, provider)
