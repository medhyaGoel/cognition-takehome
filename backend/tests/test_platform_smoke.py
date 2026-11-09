from cryptography.hazmat.primitives.asymmetric import rsa

from tests.helpers import auth_headers, make_token


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_auth_config_is_public_and_secret_free(client):
    body = client.get("/api/auth/config").json()
    assert set(body) == {"issuer", "client_id", "audience"}


def test_me_from_oidc_claims(client):
    me = client.get("/api/me", headers=auth_headers("carol"))
    assert me.status_code == 200
    assert me.json() == {
        "subject": "carol@example.com",
        "display_name": "Carol",
        "roles": ["flag_approver", "refund_approver"],
        "business_units": ["EU", "US"],
    }


def test_me_requires_auth(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_rejects_token_signed_by_other_key(client):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = make_token("bob@example.com", ["refund_approver"], ["bu-us"], key=other)
    assert client.get("/api/me", headers=_bearer(token)).status_code == 401


def test_rejects_hs256_and_wrong_audience_issuer_expiry(client):
    hs = make_token("bob@example.com", [], [], key="k" * 32, algorithm="HS256")
    bad = [
        hs,
        make_token("bob@example.com", [], [], aud="someone-else"),
        make_token("bob@example.com", [], [], iss="https://evil.test"),
        make_token("bob@example.com", [], [], exp=1, iat=0),
        make_token("bob@example.com", [], [], email_verified=False),
    ]
    for token in bad:
        assert client.get("/api/me", headers=_bearer(token)).status_code == 401


def test_unknown_roles_and_business_units_ignored(client):
    token = make_token("x@example.com", ["superuser"], ["bu-mars", "admins"])
    me = client.get("/api/me", headers=_bearer(token)).json()
    assert me["roles"] == [] and me["business_units"] == []


def test_request_id_echoed(client):
    r = client.get("/api/me", headers={**auth_headers("erin"), "X-Request-ID": "abc"})
    assert r.headers["X-Request-ID"] == "abc"


def test_mask_email():
    from app.platform.masking import mask_email

    assert mask_email("ada@customer.test") == "a***@customer.test"
    assert mask_email("not-an-email") == "***"
