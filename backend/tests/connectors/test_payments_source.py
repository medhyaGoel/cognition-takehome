from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.platform.connectors.payments_source import PostgresPaymentsSource, sync_payments


def test_sync_mirrors_captured_payments_idempotently(owner_engine):
    source = PostgresPaymentsSource(settings.source_database_url)
    with Session(owner_engine) as db:
        first = sync_payments(db, source)
        second = sync_payments(db, source)
    assert first.inserted == 281  # 8 named + 273 synthetic; pending/voided are excluded
    assert second.inserted == 0 and second.drift == []
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT business_unit, count(*) FROM payments GROUP BY 1 ORDER BY 1")
        )
        assert rows.all() == [("EU", 95), ("US", 186)]


def test_sync_reports_drift_and_never_overwrites_amount(owner_engine):
    source = PostgresPaymentsSource(settings.source_database_url)
    with Session(owner_engine) as db:
        sync_payments(db, source)
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE payments SET amount_cents = 1 WHERE provider_ref = 'ch_1001'"))
    with Session(owner_engine) as db:
        report = sync_payments(db, source)
    assert report.drift == ["ch_1001"]
    with owner_engine.connect() as conn:
        amt = conn.scalar(text("SELECT amount_cents FROM payments WHERE provider_ref = 'ch_1001'"))
    assert amt == 1
