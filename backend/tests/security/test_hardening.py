"""HTTP hardening: headers, CORS, docs in production, generic 500s, body limits, log redaction."""

import asyncio
import io
import json
import logging
import os
import pathlib
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app, unhandled_error_handler
from app.platform.security import (
    API_CSP,
    BodySizeLimitMiddleware,
    RedactingFilter,
    configure_cors,
    fastapi_settings,
    install_log_redaction,
    redact,
)
from tests.helpers import auth_headers, token_for

BACKEND = pathlib.Path(__file__).resolve().parents[2]


def _assert_security_headers(response, *, api: bool):
    h = response.headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "no-referrer"
    assert h["Content-Security-Policy"] == API_CSP
    assert API_CSP.startswith("default-src 'none'")
    if api:
        assert h["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "path,headers",
    [
        ("/api/me", "erin"),
        ("/api/me", None),
        ("/api/auth/config", None),
        ("/api/does-not-exist", None),
    ],
)
def test_security_headers_on_api_responses(client, path, headers):
    response = client.get(path, headers=auth_headers(headers) if headers else {})
    _assert_security_headers(response, api=True)


def test_security_headers_on_non_api_responses(client):
    response = client.get("/healthz")
    _assert_security_headers(response, api=False)
    assert "no-store" not in response.headers.get("Cache-Control", "")


def test_cors_disabled_by_default(client):
    preflight = client.options(
        "/api/me",
        headers={
            "Origin": "https://evil.test",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert "access-control-allow-origin" not in preflight.headers
    simple = client.get("/api/me", headers={**auth_headers("erin"), "Origin": "https://evil.test"})
    assert "access-control-allow-origin" not in simple.headers


def test_cors_allowlist_when_configured():
    probe = FastAPI()
    probe.get("/api/x")(lambda: {})
    configure_cors(probe, ["https://tools.example.com"])
    c = TestClient(probe)

    def preflight(origin):
        return c.options(
            "/api/x", headers={"Origin": origin, "Access-Control-Request-Method": "GET"}
        )

    ok = preflight("https://tools.example.com")
    assert ok.headers["access-control-allow-origin"] == "https://tools.example.com"
    assert "access-control-allow-credentials" not in ok.headers
    assert "access-control-allow-origin" not in preflight("https://evil.test").headers


@pytest.mark.parametrize("origins", [["*"], ["http://tools.example.com"], ["null"]])
def test_cors_rejects_wildcard_and_insecure_origins(origins):
    with pytest.raises(ValueError):
        configure_cors(FastAPI(), origins)


def test_openapi_docs_enabled_in_development_only(client):
    assert client.get("/openapi.json").status_code == 200
    prod = FastAPI(**fastapi_settings(production=True))
    c = TestClient(prod)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404


def test_production_app_does_not_serve_docs():
    """Import the real app under APP_ENV=production (no DB/IdP connection needed)."""
    env = {
        **os.environ,
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+psycopg://u:p@db.invalid/app",
        "WORKER_DATABASE_URL": "postgresql+psycopg://u:p@db.invalid/app",
        "MIGRATION_DATABASE_URL": "postgresql+psycopg://u:p@db.invalid/app",
        "SOURCE_DATABASE_URL": "postgresql+psycopg://u:p@db.invalid/src",
        "OIDC_ISSUER": "https://login.example.com/tenant/v2.0",
        "OIDC_AUDIENCE": "api://internal-tools",
        "OIDC_WEB_CLIENT_ID": "web",
        "OIDC_JWKS_URL": "https://login.example.com/tenant/discovery/v2.0/keys",
    }
    script = (
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "c = TestClient(app)\n"
        "print([c.get(p).status_code for p in ('/docs', '/redoc', '/openapi.json')])\n"
        "print(c.get('/healthz').headers.get('strict-transport-security'))\n"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script], cwd=BACKEND, env=env, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    assert lines[0] == "[404, 404, 404]"
    assert lines[1].startswith("max-age=")


@pytest.fixture
def boom_route():
    async def boom():
        raise RuntimeError("password=hunter2 user=alice@example.com at /srv/app/secret.py")

    app.add_api_route("/__test__/boom", boom)
    yield "/__test__/boom"
    app.router.routes[:] = [
        r for r in app.router.routes if getattr(r, "path", "") != "/__test__/boom"
    ]


def test_unhandled_errors_are_generic_500(boom_route, caplog, monkeypatch):
    # alembic's fileConfig (run by the migration fixture) disables pre-existing loggers.
    monkeypatch.setattr(logging.getLogger("app"), "disabled", False)
    c = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="app"):
        response = c.get(boom_route, headers={"X-Request-ID": "req-123"})
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }
    assert "hunter2" not in response.text and "Traceback" not in response.text
    assert response.headers["X-Request-ID"] == "req-123"
    _assert_security_headers(response, api=False)
    assert any("req-123" in r.getMessage() and r.exc_info for r in caplog.records), caplog.records


def test_unhandled_errors_still_propagate_to_the_server(boom_route):
    """The generic body is sent, and the exception still reaches the server (and test clients)."""
    with pytest.raises(RuntimeError):
        TestClient(app).get(boom_route)


def test_backstop_exception_handler_is_generic():
    response = asyncio.run(unhandled_error_handler(None, RuntimeError("leak me")))
    assert response.status_code == 500
    assert b"leak me" not in response.body


def test_request_id_rejects_injection(client):
    response = client.get("/healthz", headers={"X-Request-ID": "abc\x1b[31m fake log line"})
    assert response.headers["X-Request-ID"] != "abc\x1b[31m fake log line"
    assert len(response.headers["X-Request-ID"]) == 36


def test_body_over_limit_is_413_before_auth(client):
    response = client.post("/api/me", content=b"x" * (64 * 1024 + 1))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    _assert_security_headers(response, api=True)


def test_chunked_body_without_length_is_411(client):
    def chunks():
        yield b"{}"

    response = client.post("/api/me", content=chunks())
    assert response.status_code == 411


def test_body_longer_than_declared_is_cut_off():
    """Defense in depth: count streamed bytes even if Content-Length lied."""
    seen: list[bytes] = []

    async def echo(scope, receive, send):
        while True:
            message = await receive()
            seen.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BodySizeLimitMiddleware(echo, max_bytes=10)
    chunks = [
        {"type": "http.request", "body": b"x" * 8, "more_body": True},
        {"type": "http.request", "body": b"x" * 8, "more_body": False},
    ]
    sent: list[dict] = []

    async def receive():
        return chunks.pop(0)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/x",
        "headers": [(b"content-length", b"5")],
    }
    asyncio.run(middleware(scope, receive, send))
    assert sent[0]["status"] == 413
    assert seen == [b"x" * 8]


@pytest.mark.parametrize(
    "raw,leaked",
    [
        ("Authorization: Bearer abc.def-ghi_jkl", "abc.def-ghi_jkl"),
        ("authorization=bearer xyz123==", "xyz123"),
        (f"token {token_for('alice')}", "eyJ"),
        ("refund requested by alice@example.com", "alice@example.com"),
        ("GET /api/payments?q=Ada.Lovelace%40customer.test", None),
    ],
)
def test_redact(raw, leaked):
    out = redact(raw)
    if leaked:
        assert leaked not in out
    assert "[REDACTED" in out or leaked is None


def test_redacting_filter_scrubs_args_and_tracebacks():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    log = logging.getLogger("test.redaction")
    log.addHandler(handler)
    log.propagate = False
    try:
        log.warning("login %s with %s", "bob@example.com", f"Bearer {token_for('bob')}")
        try:
            raise ValueError("bad email carol@example.com")
        except ValueError:
            log.exception("failed for %s", "dave@example.com")
    finally:
        log.removeHandler(handler)
    output = stream.getvalue()
    for secret in ("bob@example.com", "eyJ", "carol@example.com", "dave@example.com"):
        assert secret not in output
    assert "Bearer [REDACTED]" in output and "[REDACTED_EMAIL]" in output


def test_redaction_installed_on_root_handlers():
    install_log_redaction()
    root_handlers = logging.getLogger().handlers
    assert root_handlers
    assert all(any(isinstance(f, RedactingFilter) for f in h.filters) for h in root_handlers)


def test_auth_failures_do_not_echo_token(client):
    token = token_for("bob")[:-4] + "AAAA"
    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert json.loads(response.text) == {"detail": "Invalid token"}
