import logging
import smtplib
import socket
import uuid
from email import message_from_bytes, policy

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult

from app.db import WorkerSessionLocal
from app.platform.notifications.email import EmailAdapter, SmtpSettings, render_email
from app.platform.outbox import OutboxRecord, dispatch_batch
from app.platform.workflows import on_event
from app.platform.workflows.engine import get_config, plan_messages
from tests.workflows.conftest import refund_event

USERNAME, PASSWORD = "mailer", "sup3r-s3cret-pw"


class Sink:
    def __init__(self, fail_code: str | None = None):
        self.messages: list[bytes] = []
        self.envelopes: list[tuple[str, list[str]]] = []
        self.fail_code = fail_code

    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        if self.fail_code:
            return self.fail_code
        self.messages.append(envelope.original_content)
        self.envelopes.append((envelope.mail_from, list(envelope.rcpt_tos)))
        return "250 OK"


def _authenticator(server, session, envelope, mechanism, auth_data):
    ok = auth_data.login == USERNAME.encode() and auth_data.password == PASSWORD.encode()
    return AuthResult(success=ok)


@pytest.fixture
def smtp_sink():
    controllers = []

    def start(fail_code: str | None = None, auth: bool = False):
        sink = Sink(fail_code)
        kwargs = {"authenticator": _authenticator, "auth_require_tls": False} if auth else {}
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        controller = Controller(sink, hostname="127.0.0.1", port=port, **kwargs)
        controller.start()
        controllers.append(controller)
        return sink, controller.port

    yield start
    for c in controllers:
        c.stop()


def large_refund_email_payload(**data):
    event = refund_event(
        amount_cents=250_000,
        reason="Duplicate charge",
        customer_email="jane.doe@customer.test",
        **data,
    )
    cfg = get_config()
    (_, action), = [n for n in __import__("app.platform.workflows.engine", fromlist=["x"])
                     .evaluate(cfg, event).notifications]
    (payload,) = [p for p in plan_messages(cfg, "refund-large-two-approvals", action, event)
                  if p["adapter"] == "email"]
    return event, payload


def test_render_approval_email():
    event, payload = large_refund_email_payload()
    msg = render_email(
        OutboxRecord(id=42, topic="notify.finance-approvals", payload=payload, attempts=1),
        sender="internal-tools@example.com",
        web_base_url="http://localhost:5173/",
    )
    assert msg["Subject"] == "[Approval needed] Refund $2,500.00 USD · US"
    assert msg["To"] == "finance@example.com"
    assert msg["Message-ID"] == "<outbox-42@example.com>"
    plain = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    link = f"http://localhost:5173/refunds/{event.entity_id}"
    for part in (plain, html):
        assert "$2,500.00 USD" in part
        assert "Duplicate charge" in part
        assert "alice@example.com" in part  # requester: finance is entitled to it
        assert "refund-large-two-approvals" in part
        assert "j***@customer.test" in part
        assert "jane.doe@customer.test" not in part
        assert link in part
    assert "Business unit: US" in plain


def test_html_body_is_escaped():
    _, payload = large_refund_email_payload()
    payload["data"]["reason"] = "<script>alert(1)</script>"
    msg = render_email(
        OutboxRecord(id=1, topic="t", payload=payload, attempts=1),
        sender="a@example.com",
        web_base_url="http://x",
    )
    html = msg.get_body(("html",)).get_content()
    assert "<script>" not in html and "&lt;script&gt;" in html


def _settings(port: int, **kw) -> SmtpSettings:
    return SmtpSettings(host="127.0.0.1", port=port, sender="internal-tools@example.com", **kw)


def test_large_refund_email_delivered_via_dispatcher(db_as, smtp_sink, outbox_rows):
    sink, port = smtp_sink()
    db = db_as("alice")
    event = refund_event(amount_cents=250_000)
    with db.begin():
        on_event(db, event)
    report = dispatch_batch(adapters={"email": EmailAdapter(_settings(port))}, batch_size=1)
    # batch_size=1 claims the in_app message first; dispatch until the email row is done
    while report.claimed:
        report = dispatch_batch(adapters={"email": EmailAdapter(_settings(port))})
    email_row = next(r for r in outbox_rows() if r["payload"]["adapter"] == "email")
    assert email_row["dispatched_at"] is not None
    (raw,) = sink.messages
    parsed = message_from_bytes(raw, policy=policy.default)
    assert parsed["Message-ID"] == f"<outbox-{email_row['id']}@example.com>"
    assert parsed["Subject"] == "[Approval needed] Refund $2,500.00 USD · US"
    assert sink.envelopes == [("internal-tools@example.com", ["finance@example.com"])]


def _enqueue_email() -> None:
    from app.platform import outbox

    _, payload = large_refund_email_payload()
    with WorkerSessionLocal() as db, db.begin():
        outbox.enqueue(db, topic="notify.finance-approvals", payload=payload)


def test_smtp_failure_is_retried(smtp_sink, outbox_rows, owner_engine):
    from sqlalchemy import text

    bad, bad_port = smtp_sink(fail_code="451 4.3.0 try again later")
    _enqueue_email()
    report = dispatch_batch(adapters={"email": EmailAdapter(_settings(bad_port))})
    assert report.failed == 1
    (row,) = outbox_rows()
    assert row["attempts"] == 1 and row["dispatched_at"] is None
    assert row["last_error"] == "EmailDeliveryError: SMTP delivery failed (SMTPDataError, code 451)"
    good, good_port = smtp_sink()
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE outbox SET available_at = now()"))
    assert dispatch_batch(adapters={"email": EmailAdapter(_settings(good_port))}).sent == 1
    assert len(good.messages) == 1 and bad.messages == []


def test_connection_refused_is_retried(outbox_rows):
    _enqueue_email()
    report = dispatch_batch(adapters={"email": EmailAdapter(_settings(1))})
    assert report.failed == 1
    assert "SMTP delivery failed (ConnectionRefusedError)" in outbox_rows()[0]["last_error"]


def test_credentials_are_used_but_never_logged(smtp_sink, outbox_rows, caplog):
    sink, port = smtp_sink(auth=True)
    _enqueue_email()
    with caplog.at_level(logging.DEBUG):
        ok = dispatch_batch(
            adapters={"email": EmailAdapter(_settings(port, username=USERNAME, password=PASSWORD))}
        )
    assert ok.sent == 1 and len(sink.messages) == 1
    # Wrong password: failure recorded without the secret.
    _enqueue_email()
    with caplog.at_level(logging.DEBUG):
        bad = dispatch_batch(
            adapters={"email": EmailAdapter(_settings(port, username=USERNAME, password="wrong-pw"))}
        )
    assert bad.failed == 1
    errors = " ".join(r["last_error"] or "" for r in outbox_rows())
    assert "SMTP delivery failed" in errors
    for secret in (PASSWORD, "wrong-pw"):
        assert secret not in caplog.text and secret not in errors
    assert PASSWORD not in repr(_settings(port, username=USERNAME, password=PASSWORD))


def test_settings_from_env(monkeypatch):
    for k, v in {
        "SMTP_HOST": "mailpit",
        "SMTP_PORT": "1025",
        "SMTP_FROM": "it@example.com",
        "SMTP_USE_TLS": "true",
        "SMTP_USERNAME": "u",
        "SMTP_PASSWORD": "p",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("WEB_BASE_URL", raising=False)
    s = SmtpSettings.from_env()
    assert (s.host, s.port, s.sender, s.use_tls, s.username) == (
        "mailpit", 1025, "it@example.com", True, "u",
    )
    assert s.web_base_url == "http://localhost:5173"


def test_smtplib_is_not_given_debug_output():
    assert smtplib.SMTP.debuglevel == 0
    assert uuid  # keep import for refund ids in helpers
