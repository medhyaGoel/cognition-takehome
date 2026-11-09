from sqlalchemy import text

from tests.conftest import auth_headers


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_dev_login_and_me(client):
    token = client.post("/api/auth/dev-login", json={"email": "bob@example.com"}).json()
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token['access_token']}"})
    assert me.status_code == 200
    assert me.json()["roles"] == ["refund_approver"]


def test_me_requires_auth(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_unknown_user_cannot_login(client):
    assert client.post("/api/auth/dev-login", json={"email": "x@evil.test"}).status_code == 401


def test_request_id_echoed(client):
    r = client.get("/api/me", headers={**auth_headers("erin@example.com"), "X-Request-ID": "abc"})
    assert r.headers["X-Request-ID"] == "abc"


def test_audit_log_is_append_only(db):
    import pytest
    from sqlalchemy.exc import DBAPIError

    with db.begin():
        db.execute(
            text(
                "INSERT INTO audit_log (actor, action, entity_type, entity_id) "
                "VALUES ('t', 't', 't', '1')"
            )
        )
    with pytest.raises(DBAPIError, match="append-only"), db.begin():
        db.execute(text("UPDATE audit_log SET actor = 'x'"))
    with pytest.raises(DBAPIError, match="append-only"), db.begin():
        db.execute(text("DELETE FROM audit_log"))


def test_payment_refund_bounds_enforced_by_db(db):
    import pytest
    from sqlalchemy.exc import IntegrityError

    with db.begin():
        db.execute(
            text(
                "INSERT INTO payments (provider_ref, customer_name, customer_email, amount_cents,"
                " currency) VALUES ('ch_x', 'n', 'e@x.test', 100, 'USD')"
            )
        )
    with pytest.raises(IntegrityError), db.begin():
        db.execute(text("UPDATE payments SET reserved_cents = 101"))


def test_mask_email():
    from app.platform.masking import mask_email

    assert mask_email("ada@customer.test") == "a***@customer.test"
    assert mask_email("not-an-email") == "***"
