import time
from datetime import timedelta

from sqlalchemy import text

from app.apps.refunds import execution
from app.db import WorkerSessionLocal
from app.platform.connectors import payment_provider
from app.platform.connectors.payment_provider import FakePaymentProvider
from tests.refunds.conftest import approve, create_refund, run_concurrently


def _approved(client, payment, amount):
    refund = create_refund(client, "alice", payment, amount).json()
    assert approve(client, "bob", refund["id"], 1).status_code == 200
    return refund["id"]


def _status(owner_engine, refund_id):
    with owner_engine.connect() as conn:
        return conn.execute(
            text("SELECT status, provider_refund_id, failure_reason FROM refunds WHERE id = :r"),
            {"r": refund_id},
        ).one()


class SlowProvider(FakePaymentProvider):
    def refund(self, **kwargs):
        time.sleep(0.05)
        return super().refund(**kwargs)


class CrashAfterProvider:
    """Money moves, then the worker 'dies' before recording the result."""

    def __init__(self, inner):
        self.inner = inner

    def refund(self, **kwargs):
        self.inner.refund(**kwargs)
        raise ConnectionError("worker crashed")


class CrashBeforeProvider:
    def refund(self, **kwargs):
        raise TimeoutError("provider unreachable")


def test_run_once_succeeds(
    client, make_payment, worker_db, owner_engine, payment_row, audit_rows, events
):
    payment = make_payment(amount_cents=10_000)
    refund_id = _approved(client, payment, 2_500)
    provider = FakePaymentProvider()
    assert execution.run_once(worker_db, provider) == 1
    row = _status(owner_engine, refund_id)
    assert row.status == "succeeded" and row.provider_refund_id.startswith("re_")
    assert payment_row(payment) == (2_500, 2_500)
    assert provider.calls == {refund_id: 1}
    rows = audit_rows(refund_id)
    assert [(r.actor, r.action) for r in rows][-2:] == [
        ("system:worker", "refund.processing"),
        ("system:worker", "refund.succeeded"),
    ]
    assert all(r.business_unit == "US" for r in rows)
    assert events.names()[-1] == "refund.succeeded"
    assert events.events[-1].actor == "system:worker"
    assert events.events[-1].data["status"] == "succeeded"
    assert execution.run_once(worker_db, provider) == 0


def test_run_once_failure_releases_reservation(
    client, make_payment, worker_db, owner_engine, payment_row, audit_rows, events
):
    payment = make_payment(amount_cents=10_000)
    refund_id = _approved(client, payment, 1_013)
    assert execution.run_once(worker_db, FakePaymentProvider()) == 1
    row = _status(owner_engine, refund_id)
    assert row.status == "failed" and row.failure_reason == "declined_by_provider"
    assert payment_row(payment) == (0, 0)
    assert audit_rows(refund_id)[-1].action == "refund.failed"
    assert events.names()[-1] == "refund.failed"
    assert events.events[-1].data["failure_reason"] == "declined_by_provider"


def test_concurrent_workers_never_execute_twice(client, make_payment, owner_engine):
    payment = make_payment(amount_cents=100_000)
    ids = [_approved(client, payment, 1_000 + i) for i in range(8)]
    provider = SlowProvider()
    sessions = [WorkerSessionLocal() for _ in range(3)]
    try:
        results = run_concurrently(
            [lambda s=s: execution.run_once(s, provider, batch_size=3) for s in sessions]
        )
        while (n := execution.run_once(sessions[0], provider)) > 0:
            results.append(n)
    finally:
        for s in sessions:
            s.close()
    assert sum(results) == 8
    assert provider.calls == dict.fromkeys(ids, 1)
    assert provider.charge_count == 8
    assert {_status(owner_engine, i).status for i in ids} == {"succeeded"}
    with owner_engine.connect() as conn:
        assert conn.scalar(text("SELECT refunded_cents FROM payments")) == sum(
            1_000 + i for i in range(8)
        )
        assert (
            conn.scalar(text("SELECT count(*) FROM audit_log WHERE action = 'refund.processing'"))
            == 8
        )


def test_crash_after_provider_call_reconciled_without_double_refund(
    client, make_payment, worker_db, owner_engine, payment_row
):
    payment = make_payment(amount_cents=10_000)
    refund_id = _approved(client, payment, 4_000)
    real = FakePaymentProvider()
    assert execution.run_once(worker_db, CrashAfterProvider(real)) == 1
    assert _status(owner_engine, refund_id).status == "processing"
    assert real.charge_count == 1
    assert payment_row(payment) == (4_000, 0)

    assert execution.reconcile_stuck_processing(worker_db, real) == 0  # not stuck long enough
    assert execution.reconcile_stuck_processing(worker_db, real, older_than=timedelta(0)) == 1
    row = _status(owner_engine, refund_id)
    assert row.status == "succeeded"
    assert real.charge_count == 1 and real.calls == {refund_id: 2}
    assert payment_row(payment) == (4_000, 4_000)
    assert execution.reconcile_stuck_processing(worker_db, real, older_than=timedelta(0)) == 0
    assert execution.run_once(worker_db, real) == 0
    assert real.charge_count == 1


def test_crash_before_provider_call_reconciled(
    client, make_payment, worker_db, owner_engine, payment_row, audit_rows
):
    payment = make_payment(amount_cents=10_000)
    refund_id = _approved(client, payment, 4_000)
    assert execution.run_once(worker_db, CrashBeforeProvider()) == 1
    assert _status(owner_engine, refund_id).status == "processing"
    real = FakePaymentProvider()
    assert execution.reconcile_stuck_processing(worker_db, real, older_than=timedelta(0)) == 1
    assert _status(owner_engine, refund_id).status == "succeeded"
    assert real.charge_count == 1
    assert audit_rows(refund_id)[-1].after["reconciled"] is True


def test_concurrent_reconcile_and_finalize_record_once(
    client, make_payment, owner_engine, payment_row
):
    payment = make_payment(amount_cents=10_000)
    refund_id = _approved(client, payment, 4_000)
    with WorkerSessionLocal() as s:
        execution.run_once(s, CrashBeforeProvider())
    slow = SlowProvider()
    sessions = [WorkerSessionLocal() for _ in range(3)]
    try:
        results = run_concurrently(
            [
                lambda s=s: execution.reconcile_stuck_processing(s, slow, older_than=timedelta(0))
                for s in sessions
            ]
        )
    finally:
        for s in sessions:
            s.close()
    assert sum(results) == 1
    assert slow.charge_count == 1
    assert _status(owner_engine, refund_id).status == "succeeded"
    assert payment_row(payment) == (4_000, 4_000)
    with owner_engine.connect() as conn:
        assert (
            conn.scalar(text("SELECT count(*) FROM audit_log WHERE action = 'refund.succeeded'"))
            == 1
        )


def test_execute_pending_uses_worker_role_and_provider(
    client, make_payment, owner_engine, monkeypatch
):
    provider = FakePaymentProvider()
    monkeypatch.setattr(payment_provider, "_provider", provider)
    payment = make_payment(business_unit="EU", currency="EUR")
    refund_id = create_refund(client, "frank", payment, 100).json()["id"]
    approve(client, "carol", refund_id, 1)
    assert execution.execute_pending() == 1
    assert _status(owner_engine, refund_id).status == "succeeded"
    assert provider.calls == {refund_id: 1}
    with owner_engine.connect() as conn:
        actors = conn.scalars(
            text(
                "SELECT DISTINCT actor FROM audit_log WHERE action LIKE 'refund.s%'"
                " OR action = 'refund.processing'"
            )
        ).all()
    assert actors == ["system:worker"]
