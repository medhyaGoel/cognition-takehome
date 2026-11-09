import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.platform.workflows import WorkflowOutcome, on_event
from tests.workflows.conftest import refund_event


def test_large_refund_enqueues_in_callers_transaction(db_as, outbox_rows):
    db = db_as("alice")
    event = refund_event(amount_cents=250_000, customer_email="jane@customer.test")
    with db.begin():
        outcome = on_event(db, event)
    assert outcome == WorkflowOutcome(
        required_approvals=2,
        auto_approve=False,
        matched_rules=["refund-large-two-approvals"],
        notified_channels=["finance-approvals"],
    )
    rows = outbox_rows()
    assert [r["payload"]["adapter"] for r in rows] == ["in_app", "webhook", "email"]
    payload = rows[0]["payload"]
    assert rows[0]["topic"] == "notify.finance-approvals"
    assert payload["title"] == "Refund $2,500.00 USD · US"
    assert payload["business_unit"] == "US"
    assert payload["entity_id"] == event.entity_id
    assert payload["link_path"] == f"/refunds/{event.entity_id}"
    assert payload["data"]["customer_email"] == "j***@customer.test"
    assert "jane@customer.test" not in str([dict(r) for r in rows])
    assert rows[2]["payload"]["email_to"] == ["finance@example.com"]


def test_rolled_back_transaction_produces_no_outbox_row(db_as, outbox_rows):
    db = db_as("alice")
    with pytest.raises(RuntimeError, match="boom"), db.begin():
        outcome = on_event(db, refund_event(amount_cents=250_000))
        assert outcome.notified_channels == ["finance-approvals"]
        raise RuntimeError("boom")
    assert outbox_rows() == []


def test_small_refund_auto_approves_without_notifications(db_as, outbox_rows):
    db = db_as("alice")
    with db.begin():
        outcome = on_event(db, refund_event(amount_cents=500))
    assert (outcome.required_approvals, outcome.auto_approve) == (0, True)
    assert outcome.matched_rules == ["refund-small-auto-approve"]
    assert outbox_rows() == []


def test_on_event_requires_a_transaction(db_as):
    with pytest.raises(RuntimeError, match="inside the caller's transaction"):
        on_event(db_as("alice"), refund_event(amount_cents=250_000))


def test_api_role_cannot_read_outbox(db_as):
    db = db_as("dave")
    with pytest.raises(ProgrammingError, match="permission denied"), db.begin():
        db.execute(text("SELECT * FROM outbox"))


def test_event_without_business_unit_never_creates_global_in_app(db_as, outbox_rows):
    db = db_as("dave")
    event = refund_event("refund.failed")
    del event.data["business_unit"]
    with db.begin():
        on_event(db, event)
    assert [r["payload"]["adapter"] for r in outbox_rows()] == ["webhook", "email"]
