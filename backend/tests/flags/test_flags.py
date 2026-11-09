import threading
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.helpers import auth_headers


def _headers(persona: str) -> dict[str, str]:
    return {**auth_headers(persona), "Idempotency-Key": uuid.uuid4().hex}


def _state(client, bu: str, env: str, key: str = "instant_payouts") -> dict:
    states = client.get("/api/flags", headers=auth_headers("dave")).json()
    return next(
        s
        for s in states
        if s["flag_key"] == key and s["business_unit"] == bu and s["environment"] == env
    )


def _toggle(client, persona: str, state: dict, headers=None):
    return client.post(
        f"/api/flags/states/{state['id']}/toggle",
        headers=headers or _headers(persona),
        json={"enabled": not state["enabled"], "expected_version": state["version"]},
    )


def test_list_is_scoped_to_business_units(client):
    alice = client.get("/api/flags", headers=auth_headers("alice")).json()
    assert {s["business_unit"] for s in alice} == {"US"}
    assert len(alice) == 6 * 3
    frank = client.get("/api/flags", headers=auth_headers("frank")).json()
    assert {s["business_unit"] for s in frank} == {"EU"}


def test_dev_toggle_applies_with_audit_and_outbox(client, owner_engine):
    state = _state(client, "US", "dev")
    r = _toggle(client, "alice", state)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "applied"
    after = _state(client, "US", "dev")
    assert after["enabled"] is (not state["enabled"])
    assert after["version"] == state["version"] + 1
    assert after["updated_by"] == "alice@example.com"
    audit = client.get(f"/api/flags/states/{state['id']}/audit", headers=auth_headers("erin"))
    assert [e["action"] for e in audit.json()] == ["flag.toggled"]
    with owner_engine.connect() as conn:
        topics = conn.scalars(text("SELECT topic FROM outbox")).all()
    assert "flag.toggled" in topics


def test_stale_version_is_409_and_changes_nothing(client):
    state = _state(client, "US", "staging")
    assert _toggle(client, "alice", state).status_code == 201
    r = _toggle(client, "alice", state)  # same expected_version, now stale
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "version_conflict"
    assert _state(client, "US", "staging")["version"] == state["version"] + 1


def test_idempotent_replay_does_not_reapply(client):
    state = _state(client, "US", "dev", "new_kyc_flow")
    headers = _headers("alice")
    first = _toggle(client, "alice", state, headers)
    replay = _toggle(client, "alice", state, headers)
    assert (first.status_code, replay.status_code) == (201, 200)
    assert replay.json()["id"] == first.json()["id"]
    assert _state(client, "US", "dev", "new_kyc_flow")["version"] == state["version"] + 1


def test_prod_needs_a_different_approver(client):
    state = _state(client, "US", "prod")
    cr = _toggle(client, "alice", state)
    assert cr.status_code == 201 and cr.json()["status"] == "pending"
    assert _state(client, "US", "prod")["version"] == state["version"]  # not applied yet
    cr_id = cr.json()["id"]
    assert (
        client.post(
            f"/api/flags/change-requests/{cr_id}/approve",
            headers=auth_headers("alice"),
            json={"expected_version": 1},
        ).status_code
        == 403
    )  # alice is not a flag_approver
    pending = client.get(
        "/api/flags/change-requests?status=pending", headers=auth_headers("carol")
    ).json()
    assert [p["id"] for p in pending] == [cr_id]
    ok = client.post(
        f"/api/flags/change-requests/{cr_id}/approve",
        headers=auth_headers("carol"),
        json={"expected_version": 1},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved" and ok.json()["decided_by"] == "carol@example.com"
    after = _state(client, "US", "prod")
    assert after["enabled"] is (not state["enabled"]) and after["version"] == state["version"] + 1
    actions = [
        e["action"]
        for e in client.get(
            f"/api/flags/states/{state['id']}/audit", headers=auth_headers("dave")
        ).json()
    ]
    assert actions == ["flag.change_requested", "flag.change_approved"]


def test_reject_leaves_flag_unchanged(client):
    state = _state(client, "EU", "prod")
    cr_id = _toggle(client, "frank", state).json()["id"]
    r = client.post(
        f"/api/flags/change-requests/{cr_id}/reject",
        headers=auth_headers("dave"),
        json={"expected_version": 1, "reason": "freeze week"},
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert _state(client, "EU", "prod")["version"] == state["version"]


def test_self_approval_blocked_in_code(client, monkeypatch):
    from tests import helpers

    monkeypatch.setitem(helpers.PERSONAS, "dave", (["flag_editor", "flag_approver"], ["bu-us"]))
    state = _state(client, "US", "prod", "dispute_ai_triage")
    cr_id = _toggle(client, "dave", state).json()["id"]
    r = client.post(
        f"/api/flags/change-requests/{cr_id}/approve",
        headers=auth_headers("dave"),
        json={"expected_version": 1},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "self_approval"


def test_self_approval_blocked_by_database(client, db_as):
    state = _state(client, "US", "prod", "ledger_shadow_writes")
    cr_id = _toggle(client, "alice", state).json()["id"]
    db = db_as("alice")
    with pytest.raises(DBAPIError, match="maker-checker"), db.begin():
        db.execute(
            text(
                "UPDATE flag_change_requests SET status='approved', decided_by=:me,"
                " decided_at=now() WHERE id=:id"
            ),
            {"me": "alice@example.com", "id": cr_id},
        )


def test_cross_business_unit_access_is_404(client):
    eu = _state(client, "EU", "dev")
    assert _toggle(client, "alice", eu).status_code == 404
    eu_prod = _state(client, "EU", "prod")
    cr_id = _toggle(client, "frank", eu_prod).json()["id"]
    r = client.post(
        f"/api/flags/change-requests/{cr_id}/approve",
        headers=auth_headers("carol"),
        json={"expected_version": 1},
    )
    assert r.status_code == 200  # carol is in both BUs
    us_only = client.get("/api/flags/change-requests", headers=auth_headers("erin")).json()
    assert all(c["business_unit"] == "US" for c in us_only)


def test_concurrent_approvals_apply_once(client):
    state = _state(client, "US", "prod", "card_3ds2_challenge")
    cr_id = _toggle(client, "alice", state).json()["id"]
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def approve(persona: str) -> None:
        barrier.wait()
        r = client.post(
            f"/api/flags/change-requests/{cr_id}/approve",
            headers=auth_headers(persona),
            json={"expected_version": 1},
        )
        results[persona] = r.status_code

    threads = [threading.Thread(target=approve, args=(p,)) for p in ("carol", "dave")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values()) == [200, 409], results
    after = _state(client, "US", "prod", "card_3ds2_challenge")
    assert after["version"] == state["version"] + 1
    assert after["enabled"] is (not state["enabled"])
    audit = client.get(f"/api/flags/states/{state['id']}/audit", headers=auth_headers("dave"))
    assert [e["action"] for e in audit.json()].count("flag.change_approved") == 1


def test_concurrent_dev_toggles_one_wins(client):
    state = _state(client, "EU", "staging", "refund_auto_approve_v2")
    barrier = threading.Barrier(2)
    codes: list[int] = []

    def go() -> None:
        barrier.wait()
        codes.append(_toggle(client, "frank", state).status_code)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [201, 409], codes
    assert _state(client, "EU", "staging", "refund_auto_approve_v2")["version"] == (
        state["version"] + 1
    )
