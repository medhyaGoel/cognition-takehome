"""Authentication + role-based access control.

`decode_token` is the single seam for identity. In production it would verify an OIDC
access token from the company IdP (RS256 via JWKS); in this prototype a dev-login endpoint
issues HS256 tokens for a fixed directory of demo users.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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


@dataclass(frozen=True)
class Principal:
    subject: str
    display_name: str
    roles: frozenset[str]

    def has_any(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))


DEV_DIRECTORY: dict[str, tuple[str, frozenset[str]]] = {
    "alice@example.com": ("Alice (Support Agent)", frozenset({Role.REFUND_AGENT})),
    "bob@example.com": ("Bob (Approver)", frozenset({Role.REFUND_APPROVER})),
    "carol@example.com": ("Carol (Approver)", frozenset({Role.REFUND_APPROVER})),
    "dave@example.com": (
        "Dave (Finance Admin)",
        frozenset({Role.FINANCE_ADMIN, Role.REFUND_APPROVER}),
    ),
    "erin@example.com": ("Erin (Read-only)", frozenset({Role.VIEWER})),
}

_ALGORITHM = "HS256"


def issue_token(subject: str) -> str:
    if subject not in DEV_DIRECTORY:
        raise KeyError(subject)
    display_name, roles = DEV_DIRECTORY[subject]
    now = datetime.now(UTC)
    claims = {
        "sub": subject,
        "name": display_name,
        "roles": sorted(roles),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_ttl_seconds),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> Principal:
    claims = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[_ALGORITHM],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        options={"require": ["sub", "exp", "iat", "iss", "aud"]},
    )
    roles = frozenset(r for r in claims.get("roles", []) if r in ALL_ROLES)
    return Principal(subject=claims["sub"], display_name=claims.get("name", ""), roles=roles)


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
