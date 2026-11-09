"""PII masking. Serializers call these; roles that need raw PII are listed in one place."""

from app.platform.auth import Principal, Role

PII_ROLES = (Role.REFUND_AGENT, Role.REFUND_APPROVER, Role.FINANCE_ADMIN)


def can_view_pii(principal: Principal) -> bool:
    return principal.has_any(*PII_ROLES)


def mask_email(email: str) -> str:
    local, sep, domain = email.rpartition("@")
    if not sep or not local or not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def mask_name(name: str) -> str:
    """'Ada Lovelace' -> 'A*** L***'."""
    parts = name.split()
    return " ".join(f"{p[:1]}***" for p in parts) if parts else "***"
