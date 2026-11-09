"""Fail-closed route policy.

1. Every /api route (except PUBLIC) depends on get_principal, directly or via require_roles.
2. Every /api route has an entry in POLICY (transcribed from docs/api-contract.md), and the roles
   it declares in code match that entry exactly.
3. Every registered POLICY route x every persona: disallowed -> 403, allowed -> not 401/403.
4. Missing/forged/expired/mis-scoped tokens -> 401; IdP key outage -> 503, never 500.

POLICY entries whose route isn't registered yet are skipped; STRICT_POLICY=1 makes them fail.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from collections.abc import Iterator

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute, Route

from app.main import app
from app.platform import auth
from app.platform.auth import Role, get_principal, require_roles
from tests.helpers import PERSONAS, TEST_PRIVATE_KEY, TEST_PUBLIC_KEY, auth_headers, make_token

PUBLIC = {("GET", "/api/auth/config")}
ANY_AUTHENTICATED = frozenset({"*"})
ALL_READ = frozenset({Role.VIEWER, Role.REFUND_AGENT, Role.REFUND_APPROVER, Role.FINANCE_ADMIN})
MAKERS = frozenset({Role.REFUND_AGENT, Role.FINANCE_ADMIN})
CHECKERS = frozenset({Role.REFUND_APPROVER, Role.FINANCE_ADMIN})

# (method, path with params normalised to {}) -> roles. Source: docs/api-contract.md.
POLICY: dict[tuple[str, str], frozenset[str]] = {
    ("GET", "/api/me"): ANY_AUTHENTICATED,
    ("GET", "/api/payments"): ALL_READ,
    ("GET", "/api/payments/{}"): ALL_READ,
    ("GET", "/api/refunds"): ALL_READ,
    ("GET", "/api/refunds/{}"): ALL_READ,
    ("POST", "/api/refunds"): MAKERS,
    ("POST", "/api/refunds/{}/approve"): CHECKERS,
    ("POST", "/api/refunds/{}/reject"): CHECKERS,
    ("GET", "/api/refunds/{}/audit"): ALL_READ,
    ("GET", "/api/refunds/export.csv"): frozenset({Role.FINANCE_ADMIN}),
    ("GET", "/api/workflows/rules"): ALL_READ,
    ("GET", "/api/notifications"): frozenset({Role.FINANCE_ADMIN, Role.REFUND_APPROVER}),
}

PROBE_BODIES: dict[tuple[str, str], dict] = {
    ("POST", "/api/refunds"): {
        "payment_id": str(uuid.uuid4()),
        "amount_cents": 100,
        "reason": "access-matrix probe",
    },
    ("POST", "/api/refunds/{}/approve"): {"expected_version": 1, "comment": "probe"},
    ("POST", "/api/refunds/{}/reject"): {"expected_version": 1, "reason": "probe"},
}

STRICT = os.environ.get("STRICT_POLICY") == "1"


def normalise(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def api_endpoints(routes: list[BaseRoute]) -> Iterator[tuple[str, str, BaseRoute]]:
    for route in routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api"):
            continue
        methods = getattr(route, "methods", None) or {"*"}
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            yield method, normalise(path), route


def _dependency_calls(dependant) -> Iterator[object]:
    for dep in dependant.dependencies:
        yield dep.call
        yield from _dependency_calls(dep)


def declared_roles(route: APIRoute) -> frozenset[str] | None:
    """None = no authentication. ANY_AUTHENTICATED = get_principal without a role check."""
    calls = list(_dependency_calls(route.dependant))
    if get_principal not in calls:
        return None
    role_sets = [c.required_roles for c in calls if hasattr(c, "required_roles")]
    if not role_sets:
        return ANY_AUTHENTICATED
    return frozenset.intersection(*role_sets)


def unprotected_routes(routes: list[BaseRoute]) -> list[str]:
    bad = []
    for method, path, route in api_endpoints(routes):
        if (method, path) in PUBLIC:
            continue
        if not isinstance(route, APIRoute) or declared_roles(route) is None:
            bad.append(f"{method} {path}")
    return bad


def routes_missing_policy(routes: list[BaseRoute]) -> list[str]:
    return [
        f"{m} {p}"
        for m, p, _ in api_endpoints(routes)
        if (m, p) not in POLICY and (m, p) not in PUBLIC
    ]


REGISTERED = {(m, p): r for m, p, r in api_endpoints(app.routes)}


# --- 1 & 2: static policy -------------------------------------------------------------------


def test_every_api_route_requires_authentication():
    assert unprotected_routes(app.routes) == []


def test_every_api_route_declares_a_policy():
    assert routes_missing_policy(app.routes) == [], "add the route to POLICY (and the contract)"


def test_public_routes_are_really_public(client):
    for method, path in PUBLIC:
        assert client.request(method, path).status_code == 200


@pytest.mark.parametrize("key", sorted(POLICY), ids=lambda k: f"{k[0]} {k[1]}")
def test_declared_roles_match_policy(key):
    route = REGISTERED.get(key)
    if route is None:
        _not_registered(key)
    assert declared_roles(route) == POLICY[key]


def test_checks_detect_unprotected_and_undeclared_routes():
    probe = FastAPI()

    @probe.get("/api/leaky")
    def leaky():
        return {}

    @probe.get("/api/new-thing")
    def new_thing(_=Depends(require_roles(Role.VIEWER))):
        return {}

    probe.router.routes.append(Route("/api/raw", lambda r: None))
    assert unprotected_routes(probe.routes) == ["GET /api/leaky", "GET /api/raw"]
    assert routes_missing_policy(probe.routes) == [
        "GET /api/leaky",
        "GET /api/new-thing",
        "GET /api/raw",
    ]


def _not_registered(key):
    reason = f"{key[0]} {key[1]} not registered yet (another agent owns it)"
    if STRICT:
        pytest.fail(f"STRICT_POLICY: {reason}")
    pytest.skip(reason)


# --- 3: dynamic matrix ----------------------------------------------------------------------


def _probe(client, key, headers):
    method, path = key
    url = REGISTERED[key].path_format
    url = re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), url)
    headers = {**headers, "Idempotency-Key": f"probe-{uuid.uuid4()}"}
    body = PROBE_BODIES.get(key, {}) if method in {"POST", "PUT", "PATCH"} else None
    return client.request(method, url, headers=headers, json=body)


@pytest.mark.parametrize("persona", sorted(PERSONAS))
@pytest.mark.parametrize("key", sorted(POLICY), ids=lambda k: f"{k[0]} {k[1]}")
def test_role_matrix(client, key, persona):
    if key not in REGISTERED:
        _not_registered(key)
    allowed = POLICY[key] == ANY_AUTHENTICATED or bool(POLICY[key] & set(PERSONAS[persona][0]))
    response = _probe(client, key, auth_headers(persona))
    if allowed:
        assert response.status_code not in (401, 403), response.text
        assert response.status_code < 500, response.text
    else:
        assert response.status_code == 403, response.text


def _protected_keys():
    return sorted(k for k in REGISTERED if k not in PUBLIC)


def test_roleless_identity_is_forbidden_on_role_gated_routes(client):
    headers = {"Authorization": f"Bearer {make_token('nobody@example.com', [], ['bu-us'])}"}
    for key in _protected_keys():
        expected = 200 if POLICY.get(key) == ANY_AUTHENTICATED else 403
        status = _probe(client, key, headers).status_code
        assert status == expected or (expected == 200 and status not in (401, 403)), key


# --- 4: token validation --------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _claims(**overrides) -> dict:
    token = make_token("bob@example.com", ["refund_approver"], ["bu-us"])
    claims = jwt.decode(token, options={"verify_signature": False})
    claims.update(overrides)
    return claims


def _forge(header: dict, claims: dict, sign) -> str:
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
    return f"{signing_input}.{_b64(sign(signing_input.encode()))}"


def _tampered_payload() -> str:
    header, _, signature = make_token("bob@example.com", ["refund_approver"], ["bu-us"]).split(".")
    elevated = _b64(
        json.dumps(_claims(roles=["finance_admin"], groups=["bu-us", "bu-eu"])).encode()
    )
    return f"{header}.{elevated}.{signature}"


def _flipped_signature() -> str:
    header, payload, signature = make_token("bob@example.com", [], []).split(".")
    raw = bytearray(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
    raw[0] ^= 0xFF
    return f"{header}.{payload}.{_b64(bytes(raw))}"


def _bad_tokens() -> dict[str, str]:
    now = int(time.time())
    public_pem = TEST_PUBLIC_KEY.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {
        "garbage": "not-a-jwt",
        "expired": make_token("bob@example.com", [], [], exp=now - 3600, iat=now - 7200),
        "not_yet_valid": make_token("bob@example.com", [], [], nbf=now + 3600),
        "wrong_audience": make_token("bob@example.com", [], [], aud="another-api"),
        "wrong_issuer": make_token("bob@example.com", [], [], iss="https://evil.test/realms/x"),
        "alg_none": _forge({"alg": "none", "typ": "JWT"}, _claims(), lambda _m: b""),
        "alg_none_mixed_case": _forge({"alg": "nOnE"}, _claims(), lambda _m: b""),
        "hs256_with_public_key": _forge(
            {"alg": "HS256", "typ": "JWT"},
            _claims(),
            lambda m: hmac.new(public_pem, m, hashlib.sha256).digest(),
        ),
        "other_signing_key": make_token("bob@example.com", [], [], key=other_key),
        "tampered_payload": _tampered_payload(),
        "tampered_signature": _flipped_signature(),
        "unverified_email": make_token("bob@example.com", [], [], email_verified=False),
        "missing_email_verified": make_token("bob@example.com", [], [], email_verified=None),
        "missing_exp": make_token("bob@example.com", [], [], exp=None),
        "id_token_typ": make_token("bob@example.com", [], [], typ="ID"),
        "crit_header": jwt.encode(
            _claims(), TEST_PRIVATE_KEY, algorithm="RS256", headers={"crit": ["exp"]}
        ),
        "oversized": make_token("bob@example.com", [], [], pad="x" * 10_000),
    }


@pytest.mark.parametrize("name", sorted(_bad_tokens()))
def test_bad_tokens_get_401_on_every_protected_route(client, name):
    token = _bad_tokens()[name]
    for key in _protected_keys():
        response = _probe(client, key, {"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, (key, response.text)
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_missing_or_malformed_authorization_gets_401(client):
    for headers in ({}, {"Authorization": "Basic Ym9iOnB3"}, {"Authorization": "Bearer"}):
        for key in _protected_keys():
            assert _probe(client, key, headers).status_code == 401, (key, headers)


# --- JWKS (real resolver path) --------------------------------------------------------------


@pytest.fixture
def jwks(monkeypatch):
    """Route verification through the real PyJWKClient resolver, serving our test key."""
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(TEST_PUBLIC_KEY))
    jwk.update(kid="test-kid", use="sig", alg="RS256")
    client = jwt.PyJWKClient("https://idp.invalid/certs", cache_keys=True)
    monkeypatch.setattr(client, "fetch_data", lambda: {"keys": [jwk]})
    monkeypatch.setattr(auth, "_jwks_client", client)
    monkeypatch.setattr(auth, "_key_resolver", auth._jwks_key_resolver)


def _kid_token(kid: str | None) -> dict[str, str]:
    headers = {"kid": kid} if kid else None
    token = jwt.encode(_claims(), TEST_PRIVATE_KEY, algorithm="RS256", headers=headers)
    return {"Authorization": f"Bearer {token}"}


def test_jwks_known_kid_accepted_unknown_kid_rejected(client, jwks):
    assert client.get("/api/me", headers=_kid_token("test-kid")).status_code == 200
    assert client.get("/api/me", headers=_kid_token("attacker-kid")).status_code == 401


def test_jwks_fetch_failure_is_503_without_details(client, monkeypatch):
    unreachable = jwt.PyJWKClient("http://127.0.0.1:9/realms/x/certs", timeout=1)
    monkeypatch.setattr(auth, "_jwks_client", unreachable)
    monkeypatch.setattr(auth, "_key_resolver", auth._jwks_key_resolver)
    response = client.get("/api/me", headers=_kid_token("test-kid"))
    assert response.status_code == 503
    assert response.json() == {"detail": "Identity provider unavailable"}


def test_resolver_crash_is_503_not_500(client, monkeypatch):
    def broken(_token):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(auth, "_key_resolver", broken)
    response = client.get("/api/me", headers=_kid_token("test-kid"))
    assert response.status_code == 503
    assert "secret" not in response.text


def test_bad_algorithm_never_reaches_key_resolver(client, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "_key_resolver", lambda t: calls.append(t) or TEST_PUBLIC_KEY)
    for name in ("alg_none", "hs256_with_public_key", "garbage"):
        token = _bad_tokens()[name]
        assert (
            client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
        )
    assert calls == []


def test_require_roles_enforced_end_to_end_on_probe_app():
    """The matrix above only exercises registered routes; prove the mechanism on a probe app."""
    from fastapi.testclient import TestClient

    probe = FastAPI()

    @probe.get("/api/probe-export")
    def export(_=Depends(require_roles(Role.FINANCE_ADMIN))):
        return {"ok": True}

    probe_client = TestClient(probe)
    for persona, (roles, _) in PERSONAS.items():
        expected = 200 if Role.FINANCE_ADMIN in roles else 403
        assert probe_client.get("/api/probe-export", headers=auth_headers(persona)).status_code == (
            expected
        ), persona
    assert probe_client.get("/api/probe-export").status_code == 401
