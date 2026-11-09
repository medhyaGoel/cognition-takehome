import threading
import uuid

import pytest
from sqlalchemy import text

from app.platform import workflows
from tests.helpers import auth_headers


def create_refund(client, persona, payment_id, amount_cents, *, key=None, reason="duplicate"):
    return client.post(
        "/api/refunds",
        headers={**auth_headers(persona), "Idempotency-Key": key or uuid.uuid4().hex},
        json={"payment_id": str(payment_id), "amount_cents": amount_cents, "reason": reason},
    )


def approve(client, persona, refund_id, version, comment=None):
    return client.post(
        f"/api/refunds/{refund_id}/approve",
        headers=auth_headers(persona),
        json={"expected_version": version, "comment": comment},
    )


def reject(client, persona, refund_id, version, reason="not eligible"):
    return client.post(
        f"/api/refunds/{refund_id}/reject",
        headers=auth_headers(persona),
        json={"expected_version": version, "reason": reason},
    )


def run_concurrently(fns):
    """Start all callables at the same instant (barrier); return results or exceptions."""
    barrier = threading.Barrier(len(fns))
    results: list = [None] * len(fns)

    def runner(i, fn):
        barrier.wait()
        try:
            results[i] = fn()
        except Exception as exc:
            results[i] = exc

    threads = [threading.Thread(target=runner, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


@pytest.fixture
def payment_row(owner_engine):
    def get(payment_id):
        with owner_engine.connect() as conn:
            return conn.execute(
                text("SELECT reserved_cents, refunded_cents FROM payments WHERE id = :p"),
                {"p": payment_id},
            ).one()

    return get


@pytest.fixture
def audit_rows(owner_engine):
    def get(refund_id):
        with owner_engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT actor, action, business_unit, before, after FROM audit_log"
                    " WHERE entity_type = 'refund' AND entity_id = :id ORDER BY id"
                ),
                {"id": str(refund_id)},
            ).all()

    return get


@pytest.fixture(autouse=True)
def events(monkeypatch):
    """Record domain events and let tests choose the WorkflowOutcome for refund.created.
    Autouse: refunds tests are isolated from whatever rules config/workflows.yaml holds."""
    recorded: list[workflows.DomainEvent] = []
    outcome = {"value": workflows.WorkflowOutcome()}

    def fake_on_event(db, event):
        assert db.in_transaction()
        recorded.append(event)
        if event.name == "refund.created":
            return outcome["value"]
        return workflows.WorkflowOutcome()

    monkeypatch.setattr(workflows, "on_event", fake_on_event)

    class Recorder:
        def __init__(self):
            self.events = recorded

        def set_outcome(self, **kwargs):
            outcome["value"] = workflows.WorkflowOutcome(**kwargs)

        def names(self):
            return [e.name for e in recorded]

    return Recorder()
