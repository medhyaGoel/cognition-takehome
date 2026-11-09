from sqlalchemy import text

from app.platform.notifications import default_adapters
from app.platform.outbox import dispatch_pending
from app.platform.workflows import on_event
from tests.helpers import auth_headers, make_token
from tests.workflows.conftest import refund_event


def test_rules_endpoint(client):
    for persona in ("erin", "alice", "bob", "dave"):
        assert client.get("/api/workflows/rules", headers=auth_headers(persona)).status_code == 200
    rules = client.get("/api/workflows/rules", headers=auth_headers("erin")).json()
    by_id = {r["id"]: r for r in rules}
    large = by_id["refund-large-two-approvals"]
    assert large["on"] == "refund.created"
    assert large["conditions"] == [{"field": "amount_cents", "op": "gt", "value": 50000}]
    assert large["actions"][0] == {"type": "set_required_approvals", "count": 2}
    assert large["actions"][1]["adapters"] == ["in_app", "webhook", "email"]
    sla = by_id["refund-approval-sla"]
    assert sla["kind"] == "schedule" and sla["schedule"]["params"]["sla_minutes"] == 60
    assert "finance@example.com" not in str(rules)


def test_rules_requires_auth(client):
    assert client.get("/api/workflows/rules").status_code == 401


def _seed(db_as, business_unit: str) -> None:
    db = db_as("dave")
    with db.begin():
        on_event(db, refund_event("refund.failed", status="failed", business_unit=business_unit))


def test_notifications_rls_scoped(client, db_as, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("NOTIFY_WEBHOOK_URL", raising=False)
    _seed(db_as, "US")
    _seed(db_as, "EU")
    dispatch_pending(adapters=default_adapters())

    def bus(headers):
        resp = client.get("/api/notifications", headers=headers)
        assert resp.status_code == 200
        return [n["business_unit"] for n in resp.json()]

    assert sorted(bus(auth_headers("dave"))) == ["EU", "US"]
    assert bus(auth_headers("bob")) == ["US"]
    eu_only = {
        "Authorization": "Bearer " + make_token("eve@example.com", ["refund_approver"], ["bu-eu"])
    }
    assert bus(eu_only) == ["EU"]
    (n,) = client.get("/api/notifications", headers=auth_headers("bob")).json()
    assert set(n) == {
        "id",
        "created_at",
        "topic",
        "title",
        "body",
        "recipient_role",
        "entity_type",
        "entity_id",
        "business_unit",
    }
    assert n["topic"] == "payments-oncall" and n["title"].startswith("Refund $100.00 USD")


def test_notifications_newest_first_and_limit(client, db_as, worker_db):
    for _ in range(3):
        _seed(db_as, "US")
    dispatch_pending(adapters=default_adapters())
    ids = [n["id"] for n in client.get("/api/notifications", headers=auth_headers("dave")).json()]
    assert ids == sorted(ids, reverse=True) and len(ids) == 3
    limited = client.get("/api/notifications?limit=2", headers=auth_headers("dave")).json()
    assert [n["id"] for n in limited] == ids[:2]
    assert client.get("/api/notifications?limit=0", headers=auth_headers("dave")).status_code == 422
    assert worker_db.execute(text("SELECT count(*) FROM notifications")).scalar_one() == 3


def test_notifications_forbidden_for_other_roles(client):
    for persona in ("alice", "erin"):
        assert client.get("/api/notifications", headers=auth_headers(persona)).status_code == 403
    assert client.get("/api/notifications").status_code == 401
