import csv
import io
import json

from sqlalchemy import text

from app.apps.refunds.service import format_minor_units
from tests.helpers import auth_headers, make_token
from tests.refunds.conftest import approve, create_refund, reject


def test_create_reserves_and_returns_refund(client, make_payment, payment_row):
    payment = make_payment(amount_cents=10_000)
    r = create_refund(client, "alice", payment, 2_500)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending_approval"
    assert body["requested_by"] == "alice@example.com"
    assert body["business_unit"] == "US" and body["currency"] == "USD"
    assert body["required_approvals"] == 1 and body["approvals_count"] == 0
    assert body["version"] == 1
    assert "idempotency_key" not in body and "request_hash" not in body
    assert payment_row(payment).reserved_cents == 2_500
    p = client.get(f"/api/payments/{payment}", headers=auth_headers("alice")).json()
    assert p["reserved_cents"] == 2_500 and p["refundable_cents"] == 7_500


def test_create_over_refundable_is_409(client, make_payment, payment_row):
    payment = make_payment(amount_cents=1_000)
    assert create_refund(client, "alice", payment, 600).status_code == 201
    r = create_refund(client, "alice", payment, 401)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "insufficient_refundable_amount"
    assert payment_row(payment).reserved_cents == 600


def test_create_validation(client, make_payment):
    payment = make_payment()
    body = {"payment_id": str(payment), "amount_cents": 100, "reason": "x"}
    h = auth_headers("alice")
    assert client.post("/api/refunds", headers=h, json=body).status_code == 422
    short = {**h, "Idempotency-Key": "short"}
    assert client.post("/api/refunds", headers=short, json=body).status_code == 422
    ok_key = {**h, "Idempotency-Key": "k" * 16}
    for bad in (
        {**body, "amount_cents": 0},
        {**body, "amount_cents": 10.5},
        {**body, "amount_cents": "100"},
        {**body, "reason": ""},
        {**body, "extra": 1},
    ):
        assert client.post("/api/refunds", headers=ok_key, json=bad).status_code == 422
    unknown = {**body, "payment_id": "00000000-0000-0000-0000-000000000000"}
    r = client.post("/api/refunds", headers=ok_key, json=unknown)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_idempotent_replay_and_mismatch(client, make_payment, payment_row):
    payment = make_payment(amount_cents=10_000)
    first = create_refund(client, "alice", payment, 1_000, key="key-123456")
    replay = create_refund(client, "alice", payment, 1_000, key="key-123456")
    assert first.status_code == 201 and replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert payment_row(payment).reserved_cents == 1_000
    mismatch = create_refund(client, "alice", payment, 2_000, key="key-123456")
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "idempotency_mismatch"
    other_reason = create_refund(client, "alice", payment, 1_000, key="key-123456", reason="b")
    assert other_reason.json()["error"]["code"] == "idempotency_mismatch"
    # Keys are scoped to the requester: another agent's key does not collide.
    dave = create_refund(client, "dave", payment, 1_000, key="key-123456")
    assert dave.status_code == 201 and dave.json()["id"] != first.json()["id"]
    assert payment_row(payment).reserved_cents == 2_000


def test_approve_flow_and_detail(client, make_payment):
    payment = make_payment()
    refund = create_refund(client, "alice", payment, 500).json()
    r = approve(client, "bob", refund["id"], 1, comment="ok")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved" and r.json()["version"] == 2
    assert r.json()["approvals_count"] == 1
    detail = client.get(f"/api/refunds/{refund['id']}", headers=auth_headers("erin")).json()
    assert detail["status"] == "approved"
    assert [(a["approver"], a["decision"], a["comment"]) for a in detail["approvals"]] == [
        ("bob@example.com", "approved", "ok")
    ]
    assert detail["payment"]["id"] == str(payment)


def test_stale_version_and_invalid_transition(client, make_payment):
    payment = make_payment()
    refund = create_refund(client, "alice", payment, 500).json()
    stale = approve(client, "bob", refund["id"], 7)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
    assert approve(client, "bob", refund["id"], 1).status_code == 200
    again = reject(client, "carol", refund["id"], 2)
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_transition"


def test_maker_checker_self_decision_forbidden(client, make_payment):
    payment = make_payment()
    refund = create_refund(client, "dave", payment, 500).json()
    for call in (approve, reject):
        r = call(client, "dave", refund["id"], 1)
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert approve(client, "carol", refund["id"], 1).json()["status"] == "approved"


def test_reject_releases_reservation(client, make_payment, payment_row, audit_rows):
    payment = make_payment(amount_cents=1_000)
    refund = create_refund(client, "alice", payment, 1_000).json()
    assert payment_row(payment).reserved_cents == 1_000
    r = reject(client, "bob", refund["id"], 1, reason="outside policy")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert payment_row(payment).reserved_cents == 0
    assert [a.action for a in audit_rows(refund["id"])] == ["refund.created", "refund.rejected"]
    assert create_refund(client, "alice", payment, 1_000).status_code == 201


def test_audit_trail(client, make_payment, audit_rows):
    payment = make_payment()
    refund = create_refund(client, "alice", payment, 500).json()
    approve(client, "bob", refund["id"], 1)
    entries = client.get(f"/api/refunds/{refund['id']}/audit", headers=auth_headers("erin"))
    assert entries.status_code == 200
    body = entries.json()
    assert [(e["actor"], e["action"]) for e in body] == [
        ("alice@example.com", "refund.created"),
        ("bob@example.com", "refund.approved"),
    ]
    assert body[1]["before"]["status"] == "pending_approval"
    assert body[1]["after"]["status"] == "approved" and body[1]["after"]["version"] == 2
    assert all(e["entity_id"] == refund["id"] for e in body)
    assert {row.business_unit for row in audit_rows(refund["id"])} == {"US"}
    assert "customer.test" not in json.dumps(body)


def test_business_unit_isolation(client, make_payment):
    us = make_payment(business_unit="US")
    eu = make_payment(business_unit="EU", currency="EUR")
    us_refund = create_refund(client, "alice", us, 100).json()
    eu_refund = create_refund(client, "frank", eu, 100).json()
    assert eu_refund["business_unit"] == "EU" and eu_refund["currency"] == "EUR"

    frank = auth_headers("frank")
    assert client.get(f"/api/payments/{us}", headers=frank).status_code == 404
    assert client.get(f"/api/refunds/{us_refund['id']}", headers=frank).status_code == 404
    assert client.get(f"/api/refunds/{us_refund['id']}/audit", headers=frank).status_code == 404
    assert create_refund(client, "frank", us, 100).status_code == 404
    assert [r["id"] for r in client.get("/api/refunds", headers=frank).json()] == [eu_refund["id"]]
    assert [p["id"] for p in client.get("/api/payments", headers=frank).json()] == [str(eu)]
    # frank lacks the approver role; an EU-only approver gets 404 (row invisible), not 403.
    assert approve(client, "frank", us_refund["id"], 1).status_code == 403
    eu_approver = {
        "Authorization": "Bearer " + make_token("eve@example.com", ["refund_approver"], ["bu-eu"])
    }
    r = client.post(
        f"/api/refunds/{us_refund['id']}/approve", headers=eu_approver, json={"expected_version": 1}
    )
    assert r.status_code == 404
    assert (
        client.get(f"/api/refunds/{us_refund['id']}", headers=auth_headers("alice")).json()[
            "status"
        ]
        == "pending_approval"
    )

    carol = auth_headers("carol")
    seen = {r["id"] for r in client.get("/api/refunds", headers=carol).json()}
    assert seen == {us_refund["id"], eu_refund["id"]}
    assert client.get(f"/api/payments/{us}", headers=carol).status_code == 200
    assert client.get(f"/api/payments/{eu}", headers=carol).status_code == 200
    assert approve(client, "carol", eu_refund["id"], 1).status_code == 200


def test_list_refunds_filters_and_paginates(client, make_payment):
    payment = make_payment(amount_cents=100_000)
    ids = [create_refund(client, "alice", payment, 100).json()["id"] for _ in range(3)]
    approve(client, "bob", ids[0], 1)
    h = auth_headers("erin")
    listed = client.get("/api/refunds", headers=h).json()
    assert {r["id"] for r in listed} == set(ids)
    pending = client.get("/api/refunds?status=pending_approval", headers=h).json()
    assert {r["id"] for r in pending} == set(ids[1:])
    page = client.get("/api/refunds?limit=1&offset=1", headers=h).json()
    assert len(page) == 1 and page[0]["id"] == listed[1]["id"]
    assert client.get("/api/refunds?limit=101", headers=h).status_code == 422
    assert client.get("/api/refunds?status=bogus", headers=h).status_code == 422


def test_payment_search_and_pii_masking(client, make_payment):
    payment = make_payment()
    erin = client.get(f"/api/payments/{payment}", headers=auth_headers("erin")).json()
    assert erin["customer_email"] == "c***@customer.test"
    alice = client.get(f"/api/payments/{payment}", headers=auth_headers("alice")).json()
    assert alice["customer_email"] == "cust@customer.test"
    found = client.get("/api/payments?q=test%20cust", headers=auth_headers("erin")).json()
    assert [p["id"] for p in found] == [str(payment)]
    assert found[0]["customer_email"] == "c***@customer.test"
    assert client.get("/api/payments?q=%25", headers=auth_headers("erin")).json() == []
    refund = create_refund(client, "alice", payment, 100).json()
    detail = client.get(f"/api/refunds/{refund['id']}", headers=auth_headers("erin")).json()
    assert detail["payment"]["customer_email"] == "c***@customer.test"


def test_role_enforcement(client, make_payment):
    payment = make_payment()
    assert create_refund(client, "erin", payment, 100).status_code == 403
    assert create_refund(client, "bob", payment, 100).status_code == 403
    refund = create_refund(client, "alice", payment, 100).json()
    assert approve(client, "erin", refund["id"], 1).status_code == 403
    assert approve(client, "alice", refund["id"], 1).status_code == 403
    assert client.get("/api/refunds/export.csv", headers=auth_headers("bob")).status_code == 403
    assert client.get("/api/refunds").status_code == 401


def test_routes_declare_contract_roles():
    from app.apps.refunds.router import router
    from app.db import get_db

    all_read = {"viewer", "refund_agent", "refund_approver", "finance_admin"}
    expected = {
        ("GET", "/api/payments"): all_read,
        ("GET", "/api/payments/{payment_id}"): all_read,
        ("GET", "/api/refunds"): all_read,
        ("GET", "/api/refunds/{refund_id}"): all_read,
        ("POST", "/api/refunds"): {"refund_agent", "finance_admin"},
        ("POST", "/api/refunds/{refund_id}/approve"): {"refund_approver", "finance_admin"},
        ("POST", "/api/refunds/{refund_id}/reject"): {"refund_approver", "finance_admin"},
        ("GET", "/api/refunds/{refund_id}/audit"): all_read,
        ("GET", "/api/refunds/export.csv"): {"finance_admin"},
    }
    actual = {}
    for route in router.routes:
        calls = [d.call for d in route.dependant.dependencies]
        assert get_db in calls, route.path
        (roles,) = [c.required_roles for c in calls if hasattr(c, "required_roles")]
        for method in route.methods:
            actual[(method, route.path)] = set(roles)
    assert actual == expected


def test_csv_export(client, make_payment):
    payment = make_payment(amount_cents=100_000)
    a = create_refund(client, "alice", payment, 1_234, reason="=HYPERLINK(evil)").json()
    b = create_refund(client, "alice", payment, 5).json()
    approve(client, "bob", b["id"], 1)
    r = client.get("/api/refunds/export.csv", headers=auth_headers("dave"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    by_id = {row["refund_id"]: row for row in rows}
    assert by_id[a["id"]]["amount"] == "12.34" and by_id[a["id"]]["amount_cents"] == "1234"
    assert by_id[a["id"]]["reason"] == "'=HYPERLINK(evil)"
    assert by_id[b["id"]]["amount"] == "0.05"
    assert "customer_email" not in rows[0] and "customer.test" not in r.text
    approved = client.get("/api/refunds/export.csv?status=approved", headers=auth_headers("dave"))
    assert [row["refund_id"] for row in csv.DictReader(io.StringIO(approved.text))] == [b["id"]]


def test_csv_export_respects_business_units(client, make_payment, owner_engine):
    us = make_payment(business_unit="US")
    create_refund(client, "alice", us, 100)
    eu_only_admin = {
        "Authorization": "Bearer " + make_token("fin@example.com", ["finance_admin"], ["bu-eu"])
    }
    r = client.get("/api/refunds/export.csv", headers=eu_only_admin)
    assert r.status_code == 200 and len(r.text.strip().splitlines()) == 1
    with owner_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM refunds")) == 1


def test_format_minor_units():
    assert format_minor_units(0, "USD") == "0.00"
    assert format_minor_units(5, "USD") == "0.05"
    assert format_minor_units(123_456_789_012_345_678, "EUR") == "1234567890123456.78"
    assert format_minor_units(-1205, "USD") == "-12.05"
    assert format_minor_units(500, "JPY") == "500"
