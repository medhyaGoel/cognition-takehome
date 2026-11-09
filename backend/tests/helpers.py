"""Test identities. Tokens are RS256 JWTs signed by a key generated per test run and shaped
exactly like the Keycloak realm's access tokens (see keycloak/internal-tools-realm.json)."""

import time
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings

TEST_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEST_PUBLIC_KEY = TEST_PRIVATE_KEY.public_key()

# Mirrors the realm users: name -> (roles, business-unit groups)
PERSONAS: dict[str, tuple[list[str], list[str]]] = {
    "alice": (["refund_agent"], ["bu-us"]),
    "frank": (["refund_agent"], ["bu-eu"]),
    "bob": (["refund_approver"], ["bu-us"]),
    "carol": (["refund_approver"], ["bu-us", "bu-eu"]),
    "dave": (["finance_admin", "refund_approver"], ["bu-us", "bu-eu"]),
    "erin": (["viewer"], ["bu-us"]),
}


def make_token(
    email: str,
    roles: list[str],
    groups: list[str],
    *,
    key=None,
    algorithm: str = "RS256",
    **overrides,
) -> str:
    now = int(time.time())
    claims = {
        "sub": str(uuid.uuid5(uuid.NAMESPACE_DNS, email)),
        "email": email,
        "email_verified": True,
        "name": email.split("@")[0].title(),
        "roles": roles,
        "groups": groups,
        "iss": settings.oidc_issuer,
        "aud": settings.oidc_audience,
        "iat": now,
        "exp": now + 900,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key or TEST_PRIVATE_KEY, algorithm=algorithm)


def token_for(persona: str) -> str:
    roles, groups = PERSONAS[persona]
    return make_token(f"{persona}@example.com", roles, groups)


def auth_headers(persona: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(persona)}"}


def principal_for(persona: str):
    from app.platform.auth import decode_token

    return decode_token(token_for(persona))
