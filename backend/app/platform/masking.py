"""PII masking. Serializers call these; roles that need raw PII are listed in one place."""

from app.platform.auth import Principal, Role

PII_ROLES = (Role.REFUND_AGENT, Role.REFUND_APPROVER, Role.FINANCE_ADMIN)


def can_view_pii(principal: Principal) -> bool:
    return principal.has_any(*PII_ROLES)


def mask_email(email: str) -> str:
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    return f"{local[:1]}***@{domain}"
