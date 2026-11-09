"""HTTP hardening shared by every app on the platform.

* `SecurityHeadersMiddleware`  strict headers on every response; `no-store` on /api.
* `BodySizeLimitMiddleware`    rejects oversized (413) or unframed (411) request bodies before the
                               app reads them.
* `configure_cors`             CORS is off unless CORS_ALLOWED_ORIGINS lists exact origins.
* `fastapi_settings`           OpenAPI/Swagger are not served when APP_ENV=production.
* `internal_error_response`    the only body a 500 ever has (no exception text, no stack).
* `RedactingFilter`            scrubs bearer tokens, JWTs and email addresses from log records.
"""

import logging
import os
import re
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
DEFAULT_MAX_BODY_BYTES = 64 * 1024
_BODY_METHODS = {"POST", "PUT", "PATCH"}

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_BASE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
)
_HSTS = (b"strict-transport-security", b"max-age=63072000; includeSubDomains")


def security_headers(path: str, *, production: bool) -> list[tuple[bytes, bytes]]:
    headers = list(_BASE_HEADERS)
    # Swagger UI (dev only) needs scripts/styles; everything else is data and gets 'none'.
    if not path.startswith(DOCS_PATHS[:2]):
        headers.append((b"content-security-policy", API_CSP.encode()))
    if path.startswith("/api"):
        headers.append((b"cache-control", b"no-store"))
        headers.append((b"pragma", b"no-cache"))
    if production:
        headers.append(_HSTS)
    return headers


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, production: bool = False) -> None:
        self.app = app
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = security_headers(scope["path"], production=self.production)
        names = {name for name, _ in extra}

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() not in names]
                message["headers"] = kept + extra
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Content-Length above the limit -> 413; a body without Content-Length (chunked) -> 411.
    The receive stream is also counted, so a body longer than declared can't slip through."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length")
        if declared is not None:
            if not declared.isdigit():
                await _error(400, "bad_request", "Invalid Content-Length")(scope, receive, send)
                return
            if int(declared) > self.max_bytes:
                await _too_large()(scope, receive, send)
                return
        elif scope["method"] in _BODY_METHODS and b"transfer-encoding" in headers:
            await _error(411, "length_required", "Content-Length required")(scope, receive, send)
            return

        received = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await _too_large()(scope, receive, send)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _too_large() -> JSONResponse:
    return _error(413, "payload_too_large", "Request body too large")


def internal_error_response() -> JSONResponse:
    return _error(500, "internal_error", "Internal server error")


def max_body_bytes() -> int:
    return int(os.environ.get("MAX_REQUEST_BODY_BYTES", DEFAULT_MAX_BODY_BYTES))


def fastapi_settings(*, production: bool) -> dict[str, Any]:
    if production:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {}


def cors_origins_from_env() -> list[str]:
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


def configure_cors(app: FastAPI, origins: list[str]) -> None:
    """Off by default (the SPA is served same-origin). Exact origins only; never `*`, and no
    cookies (auth is a bearer header), so a misconfigured origin can't ride a session."""
    if not origins:
        return
    if any(o == "*" or not o.startswith(("https://", "http://localhost")) for o in origins):
        raise ValueError("CORS_ALLOWED_ORIGINS must list exact https:// (or localhost) origins")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )


_REDACTIONS = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer [REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), "[REDACTED_JWT]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
)


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Rewrites the rendered message (args included) and any traceback text in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed %-args: log the template rather than lose the record
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy")


def install_log_redaction() -> RedactingFilter:
    """Attach to handlers (filters on loggers don't see records propagated from children)."""
    redacting = RedactingFilter()
    for name in _LOGGERS:
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(f, RedactingFilter) for f in handler.filters):
                handler.addFilter(redacting)
    return redacting
