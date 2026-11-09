"""The real engine + shipped rules wired into the refunds app (tests/refunds fakes on_event)."""

from sqlalchemy import text

from app.apps.refunds.execution import execute_pending
from app.platform.notifications import default_adapters
from app.platform.outbox import dispatch_pending
from tests.helpers import auth_headers
from tests.refunds.conftest import approve, create_refund


def topics(owner_engine) -> list[str]:
    with owner_engine.connect() as conn:
        return list(conn.execute(text("SELECT topic FROM outbox ORDER BY id")).scalars())


def test_small_refund_auto_approved_by_shipped_rule(client, make_payment, owner_engine):
    refund = create_refund(client, "alice", make_payment(), 5_000).json()
    assert refund["status"] == "approved" and refund["required_approvals"] == 0
    with owner_engine.connect() as conn:
        after = conn.execute(
            text("SELECT after FROM audit_log WHERE action = 'refund.auto_approved'")
        ).scalar_one()
    assert after["rules"] == ["refund-small-auto-approve"]
    assert topics(owner_engine) == []


def test_large_refund_flow_end_to_end(client, make_payment, owner_engine, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("NOTIFY_WEBHOOK_URL", raising=False)
    payment = make_payment(amount_cents=1_000_000)
    refund = create_refund(client, "alice", payment, 250_000).json()
    assert refund["status"] == "pending_approval" and refund["required_approvals"] == 2
    assert topics(owner_engine) == ["notify.finance-approvals"] * 3

    medium = create_refund(client, "alice", payment, 10_000).json()
    assert medium["required_approvals"] == 1 and medium["status"] == "pending_approval"

    approve(client, "bob", refund["id"], 1)
    assert approve(client, "carol", refund["id"], 2).json()["status"] == "approved"
    execute_pending()
    assert topics(owner_engine)[-2:] == ["notify.refunds-activity"] * 2  # in_app + log
    dispatch_pending(adapters=default_adapters())
    titles = [
        n["title"] for n in client.get("/api/notifications", headers=auth_headers("dave")).json()
    ]
    assert titles == ["Refund $2,500.00 USD · US paid out", "Refund $2,500.00 USD · US"]


def test_failed_payout_pages_oncall(client, make_payment, owner_engine):
    refund = create_refund(client, "alice", make_payment(), 1_013).json()  # provider declines *13
    approve(client, "bob", refund["id"], 1)
    execute_pending()
    with owner_engine.connect() as conn:
        rows = conn.execute(text("SELECT topic, payload FROM outbox ORDER BY id")).all()
    assert [r.topic for r in rows] == ["notify.payments-oncall"] * 3
    assert [r.payload["adapter"] for r in rows] == ["in_app", "webhook", "email"]
    assert rows[2].payload["email_to"] == ["oncall@example.com"]
    assert rows[0].payload["data"]["failure_reason"] == "declined_by_provider"
