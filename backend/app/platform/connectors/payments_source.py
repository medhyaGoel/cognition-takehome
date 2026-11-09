"""Connector to the client's payments system of record (Power Apps "connector" equivalent).

The real store is unknown (Postgres, Mongo, a core-banking API, Stripe...). Apps depend only on
`PaymentsSource`; swapping the backing store means writing one adapter, not touching app logic.

`sync_payments` mirrors records into our `payments` table so refund reservations can be enforced
atomically by our database. A transaction cannot span two databases, so:
  * our DB is authoritative for refund *reservations* (reserved/refunded cents),
  * the source is authoritative for the captured amount; a mirrored amount is never silently
    overwritten. Drift is reported for reconciliation instead,
  * the payment provider is the final arbiter of money movement (idempotent refund calls).
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Payment

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaymentRecord:
    provider_ref: str
    customer_name: str
    customer_email: str
    amount_cents: int
    currency: str
    business_unit: str
    captured_at: datetime


class PaymentsSource(Protocol):
    name: str

    def iter_captured_payments(self) -> Iterable[PaymentRecord]: ...


class PostgresPaymentsSource:
    """Adapter for a Postgres system of record exposing a `charges` table (read-only role)."""

    name = "core_payments"

    def __init__(self, url: str) -> None:
        self._engine = create_engine(url, pool_pre_ping=True)

    def iter_captured_payments(self) -> Iterable[PaymentRecord]:
        query = text(
            "SELECT charge_id, customer_name, customer_email, amount_minor, currency, region,"
            " captured_at FROM charges WHERE status = 'captured' ORDER BY charge_id"
        )
        with self._engine.connect() as conn:
            for row in conn.execute(query):
                yield PaymentRecord(
                    provider_ref=row.charge_id,
                    customer_name=row.customer_name,
                    customer_email=row.customer_email,
                    amount_cents=int(row.amount_minor),
                    currency=row.currency,
                    business_unit=row.region,
                    captured_at=row.captured_at,
                )


@dataclass
class SyncReport:
    inserted: int = 0
    unchanged_or_updated: int = 0
    drift: list[str] | None = None


def sync_payments(db: Session, source: PaymentsSource) -> SyncReport:
    """Idempotent upsert of source records. Runs as the schema owner, one transaction."""
    report = SyncReport(drift=[])
    with db.begin():
        for rec in source.iter_captured_payments():
            stmt = (
                insert(Payment)
                .values(
                    provider_ref=rec.provider_ref,
                    customer_name=rec.customer_name,
                    customer_email=rec.customer_email,
                    amount_cents=rec.amount_cents,
                    currency=rec.currency,
                    business_unit=rec.business_unit,
                    source_system=source.name,
                    captured_at=rec.captured_at,
                )
                .on_conflict_do_nothing(index_elements=["provider_ref"])
                .returning(Payment.id)
            )
            if db.execute(stmt).first() is not None:
                report.inserted += 1
                continue
            existing = db.query(Payment).filter_by(provider_ref=rec.provider_ref).one()
            if (existing.amount_cents, existing.currency, existing.business_unit) != (
                rec.amount_cents,
                rec.currency,
                rec.business_unit,
            ):
                report.drift.append(rec.provider_ref)
                log.warning("payment drift detected for %s; not overwriting", rec.provider_ref)
                continue
            existing.customer_name = rec.customer_name
            existing.customer_email = rec.customer_email
            report.unchanged_or_updated += 1
    return report
