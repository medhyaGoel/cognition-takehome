import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.platform.workflows.engine import set_config


@pytest.fixture(autouse=True)
def _reset_workflow_config():
    yield
    set_config(None)


@pytest.fixture(autouse=True)
def _enable_loggers():
    # alembic's fileConfig (run by the suite's migrations) disables pre-existing loggers;
    # re-enable ours so log assertions (including "secret not logged") are meaningful.
    for name in ("outbox", "notifications", "notifications.email", "worker", "workflows"):
        logging.getLogger(name).disabled = False
    for name in list(logging.root.manager.loggerDict):
        if name.startswith(("app.", "outbox", "notifications", "workflows", "worker")):
            logging.getLogger(name).disabled = False


@pytest.fixture
def outbox_rows(owner_engine):
    def fetch():
        with owner_engine.connect() as conn:
            return conn.execute(text("SELECT * FROM outbox ORDER BY id")).mappings().all()

    return fetch


@pytest.fixture
def make_refund(owner_engine, make_payment):
    """Insert a refund row directly (setup only; the refunds service owns real creation)."""

    def factory(
        *,
        amount_cents: int = 10_000,
        business_unit: str = "US",
        status: str = "pending_approval",
        age_minutes: int = 0,
    ) -> uuid.UUID:
        payment_id = make_payment(amount_cents=max(amount_cents, 100_000), business_unit=business_unit)
        rid = uuid.uuid4()
        created = datetime.now(UTC) - timedelta(minutes=age_minutes)
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO refunds (id, payment_id, business_unit, amount_cents, currency,"
                    " reason, status, requested_by, required_approvals, idempotency_key,"
                    " request_hash, created_at, updated_at) VALUES (:id, :pid, :bu, :amt, 'USD',"
                    " 'customer request', :status, 'alice@example.com', 1, :key, 'h', :at, :at)"
                ),
                {
                    "id": rid,
                    "pid": payment_id,
                    "bu": business_unit,
                    "amt": amount_cents,
                    "status": status,
                    "key": f"key-{rid.hex}",
                    "at": created,
                },
            )
        return rid

    return factory


def refund_event(name: str = "refund.created", amount_cents: int = 10_000, **data):
    from app.platform.workflows import DomainEvent

    base = {
        "amount_cents": amount_cents,
        "currency": "USD",
        "status": "pending_approval",
        "requested_by": "alice@example.com",
        "payment_id": str(uuid.uuid4()),
        "business_unit": "US",
    }
    base.update(data)
    return DomainEvent(
        name=name,
        entity_type="refund",
        entity_id=str(uuid.uuid4()),
        actor="alice@example.com",
        data=base,
    )
