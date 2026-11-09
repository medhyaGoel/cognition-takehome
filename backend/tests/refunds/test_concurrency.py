"""Concurrency guarantees: threads with separate sessions (separate Postgres connections)."""

import uuid

from sqlalchemy import text

from app.apps.refunds import service
from app.apps.refunds.schemas import ApproveRequest, RefundCreate
from app.platform.errors import Conflict
from tests.helpers import principal_for
from tests.refunds.conftest import run_concurrently


def _create_fn(db_as, persona, payment, amount, key):
    session, principal = db_as(persona), principal_for(persona)
    body = RefundCreate(payment_id=payment, amount_cents=amount, reason="concurrency")
    return lambda: service.create_refund(session, principal, key, body)


def test_concurrent_creates_never_over_reserve(db_as, make_payment, payment_row, owner_engine):
    payment = make_payment(amount_cents=10_000)
    fns = [_create_fn(db_as, "alice", payment, 3_000, f"key-{i:08d}") for i in range(10)]
    results = run_concurrently(fns)

    ok = [r for r in results if isinstance(r, tuple)]
    errors = [r for r in results if not isinstance(r, tuple)]
    assert len(ok) == 3 and all(created for _, created in ok)
    assert len(errors) == 7
    assert all(
        isinstance(e, Conflict) and e.code == "insufficient_refundable_amount" for e in errors
    )
    assert payment_row(payment).reserved_cents == 9_000
    with owner_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM refunds")) == 3


def test_concurrent_replays_create_exactly_one(db_as, make_payment, payment_row, owner_engine):
    payment = make_payment(amount_cents=10_000)
    key = f"replay-{uuid.uuid4().hex}"
    results = run_concurrently([_create_fn(db_as, "alice", payment, 1_000, key) for _ in range(8)])
    assert all(isinstance(r, tuple) for r in results), results
    assert len({r[0]["id"] for r in results}) == 1
    assert sum(created for _, created in results) == 1
    assert payment_row(payment).reserved_cents == 1_000
    with owner_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM refunds")) == 1
        assert (
            conn.scalar(text("SELECT count(*) FROM audit_log WHERE action = 'refund.created'")) == 1
        )


def test_concurrent_approvals_single_transition(db_as, make_payment, owner_engine, audit_rows):
    payment = make_payment()
    refund, _ = _create_fn(db_as, "alice", payment, 500, "approve-race-1")()

    def approve_as(persona):
        session, principal = db_as(persona), principal_for(persona)
        body = ApproveRequest(expected_version=1)
        return lambda: service.approve_refund(session, principal, refund["id"], body)

    results = run_concurrently([approve_as("bob"), approve_as("carol")])
    ok = [r for r in results if isinstance(r, dict)]
    errors = [r for r in results if not isinstance(r, dict)]
    assert len(ok) == 1 and ok[0]["status"] == "approved" and ok[0]["version"] == 2
    assert len(errors) == 1
    assert isinstance(errors[0], Conflict) and errors[0].code == "version_conflict"
    with owner_engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM refund_approvals WHERE refund_id = :r"),
                {"r": refund["id"]},
            )
            == 1
        )
        assert (
            conn.scalar(text("SELECT version FROM refunds WHERE id = :r"), {"r": refund["id"]}) == 2
        )
    actions = [a.action for a in audit_rows(refund["id"])]
    assert actions == ["refund.created", "refund.approved"]
