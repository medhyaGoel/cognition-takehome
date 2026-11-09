"""Refunds <-> workflow engine, tested against the interface with monkeypatched outcomes."""

import pytest
from sqlalchemy import text

from app.platform import workflows
from tests.refunds.conftest import approve, create_refund, reject

REQUIRED_DATA = {
    "amount_cents",
    "currency",
    "status",
    "requested_by",
    "payment_id",
    "business_unit",
}


def test_created_event_and_default_required_approvals(client, make_payment, events):
    payment = make_payment(business_unit="EU", currency="EUR")
    refund = create_refund(client, "frank", payment, 700).json()
    assert refund["required_approvals"] == 1 and refund["status"] == "pending_approval"
    (event,) = events.events
    assert event.name == "refund.created" and event.entity_type == "refund"
    assert event.entity_id == refund["id"] and event.actor == "frank@example.com"
    assert set(event.data) >= REQUIRED_DATA
    assert event.data == {
        "amount_cents": 700,
        "currency": "EUR",
        "status": "pending_approval",
        "requested_by": "frank@example.com",
        "payment_id": str(payment),
        "business_unit": "EU",
        "reason": "duplicate",
    }


def test_required_approvals_from_outcome(client, make_payment, events):
    events.set_outcome(required_approvals=2, matched_rules=["large-refund"])
    payment = make_payment()
    refund = create_refund(client, "alice", payment, 9_000).json()
    assert refund["required_approvals"] == 2

    first = approve(client, "bob", refund["id"], 1).json()
    assert first["status"] == "pending_approval"
    assert first["approvals_count"] == 1 and first["version"] == 2
    dup = approve(client, "bob", refund["id"], 2)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "conflict"
    dup_reject = reject(client, "bob", refund["id"], 2)
    assert dup_reject.status_code == 409
    second = approve(client, "carol", refund["id"], 2).json()
    assert second["status"] == "approved" and second["approvals_count"] == 2
    assert events.names() == ["refund.created", "refund.approved"]


@pytest.mark.parametrize(
    "outcome",
    [
        {"required_approvals": 0, "matched_rules": ["small-refund"]},
        {"auto_approve": True, "matched_rules": ["small-refund"]},
    ],
)
def test_auto_approval(client, make_payment, events, audit_rows, payment_row, outcome):
    events.set_outcome(**outcome)
    payment = make_payment()
    refund = create_refund(client, "alice", payment, 300).json()
    assert refund["status"] == "approved" and refund["version"] == 2
    assert payment_row(payment).reserved_cents == 300
    rows = audit_rows(refund["id"])
    assert [(r.actor, r.action) for r in rows] == [
        ("alice@example.com", "refund.created"),
        ("alice@example.com", "refund.auto_approved"),
    ]
    assert rows[1].before["status"] == "pending_approval"
    assert rows[1].after["status"] == "approved" and rows[1].after["rules"] == ["small-refund"]
    assert rows[1].business_unit == "US"
    assert events.names() == ["refund.created", "refund.approved"]
    assert events.events[1].data["status"] == "approved"


def test_rejected_event(client, make_payment, events):
    payment = make_payment()
    refund = create_refund(client, "alice", payment, 300).json()
    reject(client, "bob", refund["id"], 1)
    assert events.names() == ["refund.created", "refund.rejected"]
    assert events.events[1].actor == "bob@example.com"
    assert events.events[1].data["status"] == "rejected"
    assert set(events.events[1].data) >= REQUIRED_DATA


def test_workflow_failure_rolls_back_creation(
    client, make_payment, monkeypatch, payment_row, owner_engine
):
    def boom(db, event):
        raise RuntimeError("engine down")

    monkeypatch.setattr(workflows, "on_event", boom)
    payment = make_payment()
    with pytest.raises(RuntimeError):
        create_refund(client, "alice", payment, 300)
    assert payment_row(payment).reserved_cents == 0
    with owner_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM refunds")) == 0
        assert conn.scalar(text("SELECT count(*) FROM audit_log")) == 0
