"""Presentation helpers shared by templates and adapters. Money stays integer minor units."""

from typing import Any

from app.platform.masking import mask_email

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}
_ZERO_DECIMAL = frozenset({"JPY", "KRW"})


def format_money(amount_cents: int, currency: str) -> str:
    """format_money(250000, "USD") -> "$2,500.00 USD" (no floats)."""
    currency = currency.upper()
    sign = "-" if amount_cents < 0 else ""
    units = abs(amount_cents)
    symbol = _SYMBOLS.get(currency, "")
    if currency in _ZERO_DECIMAL:
        return f"{sign}{symbol}{units:,} {currency}"
    major, minor = divmod(units, 100)
    return f"{sign}{symbol}{major:,}.{minor:02d} {currency}"


def display_data(data: dict[str, Any]) -> dict[str, Any]:
    """Scalar fields of an event, with customer emails masked and `amount_display` derived.
    Only this (never raw event data) is copied into outbox payloads and templates."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, bool | int | str | None):
            continue
        if key.endswith("email") and isinstance(value, str):
            value = mask_email(value)
        out[key] = value
    cents, currency = out.get("amount_cents"), out.get("currency")
    if isinstance(cents, int) and not isinstance(cents, bool) and isinstance(currency, str):
        out["amount_display"] = format_money(cents, currency)
    return out
