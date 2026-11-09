"""Authentication (OIDC) + role-based access control.

The API never handles passwords. Users sign in at the identity provider (Keycloak in local dev;
Microsoft Entra ID / Google / Okta in production; same code, different OIDC_* settings) and the
SPA sends the IdP-issued access token. We verify it locally: RS256 signature against the IdP's
JWKS, issuer, audience, expiry, and a verified email.

Claims used:
  email           -> Principal.subject (human-readable actor in audit/RLS; must be verified)
  roles           -> app roles (unknown values ignored)
  groups "bu-xx"  -> business units the user may see (drives Postgres row-level security)
"""

from collections.abc import Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings


class Role:
    VIEWER = "viewer"
    REFUND_AGENT = "refund_agent"
    REFUND_APPROVER = "refund_approver"
    FINANCE_ADMIN = "finance_admin"


ALL_ROLES = frozenset({Role.VIEWER, Role.REFUND_AGENT, Role.REFUND_APPROVER, Role.FINANCE_ADMIN})
BUSINESS_UNITS = frozenset({"US", "EU"})
_BU_GROUP_PREFIX = "bu-"
_ALGORITHMS = ["RS256"]


@dataclass(frozen=True)
class Principal:
    subject: str
    display_name: str
    roles: frozenset[str]
    business_units: frozenset[str]

    def has_any(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))


_jwks_client = jwt.PyJWKClient(settings.oidc_jwks_url, cache_keys=True, lifespan=300, timeout=5)


def _jwks_key_resolver(token: str):
    return _jwks_client.get_signing_key_from_jwt(token).key


_key_resolver: Callable[[str], object] = _jwks_key_resolver


def set_key_resolver(resolver: Callable[[str], object]) -> None:
    """Test seam: verify tokens against a locally generated key instead of the IdP's JWKS."""
    global _key_resolver
    _key_resolver = resolver


def decode_token(token: str) -> Principal:
    claims = jwt.decode(
        token,
        _key_resolver(token),
        algorithms=_ALGORITHMS,
        audience=settings.oidc_audience,
        issuer=settings.oidc_issuer,
        leeway=30,
        options={"require": ["sub", "exp", "iat", "iss", "aud"]},
    )
    email = claims.get("email")
    if not isinstance(email, str) or not email or claims.get("email_verified") is not True:
        raise jwt.InvalidTokenError("verified email claim required")
    roles = frozenset(r for r in claims.get("roles", []) if r in ALL_ROLES)
    business_units = frozenset(
        g.removeprefix(_BU_GROUP_PREFIX).upper()
        for g in claims.get("groups", [])
        if isinstance(g, str) and g.startswith(_BU_GROUP_PREFIX)
    ).intersection(BUSINESS_UNITS)
    return Principal(
        subject=email.lower(),
        display_name=claims.get("name", email),
        roles=roles,
        business_units=business_units,
    )


_bearer = HTTPBearer(auto_error=False)


def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        return decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc


def require_roles(*roles: str) -> Callable[..., Principal]:
    """Route dependency: authenticated AND holding at least one of `roles`."""
    unknown = set(roles) - ALL_ROLES
    if unknown or not roles:
        raise ValueError(f"require_roles called with invalid roles: {roles}")

    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_any(*roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return principal

    dependency.required_roles = frozenset(roles)  # type: ignore[attr-defined]
    return dependency
