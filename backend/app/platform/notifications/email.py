"""Email adapter: SMTP via the standard library. Sent by the outbox dispatcher with no
database transaction open. Message-ID is derived from the outbox id so a redelivery (at-least-
once) is identifiable/deduplicable by mail systems. Credentials are never logged or stored."""

import html
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.platform.outbox import Adapter, OutboxRecord

DEFAULT_WEB_BASE_URL = "http://localhost:5173"
SMTP_TIMEOUT_SECONDS = 10.0

# (payload data key, label) shown in the details table, in this order.
_DETAILS = (
    ("amount_display", "Amount"),
    ("business_unit", "Business unit"),
    ("status", "Status"),
    ("reason", "Reason"),
    ("failure_reason", "Failure reason"),
    ("requested_by", "Requested by"),
    ("customer_email", "Customer"),
)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    sender: str
    use_tls: bool = False
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    web_base_url: str = DEFAULT_WEB_BASE_URL
    timeout: float = SMTP_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "SmtpSettings | None":
        host = os.environ.get("SMTP_HOST")
        if not host:
            return None
        return cls(
            host=host,
            port=int(os.environ.get("SMTP_PORT", "25")),
            sender=os.environ.get("SMTP_FROM", "internal-tools@localhost"),
            use_tls=_truthy(os.environ.get("SMTP_USE_TLS")),
            username=os.environ.get("SMTP_USERNAME") or None,
            password=os.environ.get("SMTP_PASSWORD") or None,
            web_base_url=os.environ.get("WEB_BASE_URL", DEFAULT_WEB_BASE_URL),
        )


def _header(value: str) -> str:
    return " ".join(value.split())[:300]


def _text(payload: dict, key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def message_id(outbox_id: int, sender: str) -> str:
    domain = sender.rpartition("@")[2] or "internal-tools.local"
    return f"<outbox-{outbox_id}@{_header(domain)}>"


def render_email(message: OutboxRecord, *, sender: str, web_base_url: str) -> EmailMessage:
    p = message.payload
    data = p.get("data") if isinstance(p.get("data"), dict) else {}
    recipients = [r for r in p.get("email_to") or [] if isinstance(r, str)]
    if not recipients:
        raise ValueError("email notification has no recipients")
    title = _text(p, "title") or message.topic
    prefix = _text(p, "email_subject_prefix")
    body = _text(p, "body")
    link_path = _text(p, "link_path")
    link = f"{web_base_url.rstrip('/')}{quote(link_path, safe='/')}" if link_path else ""
    details = [(label, str(data[k])) for k, label in _DETAILS if data.get(k) not in (None, "")]
    if p.get("rule"):
        details.append(("Matched rule", str(p["rule"])))

    msg = EmailMessage()
    msg["Subject"] = _header(f"[{prefix}] {title}" if prefix else title)
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = message_id(message.id, sender)
    msg["X-Outbox-Message-Id"] = str(message.id)

    plain = [title, "", body, ""] + [f"{label}: {value}" for label, value in details]
    if link:
        plain += ["", f"Open: {link}"]
    msg.set_content("\n".join(plain) + "\n")

    e = html.escape
    rows = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#555">{e(label)}</td>'
        f'<td style="padding:4px 0">{e(value)}</td></tr>'
        for label, value in details
    )
    button = (
        f'<p><a href="{e(link, quote=True)}" style="background:#1f6feb;color:#fff;'
        f'padding:8px 14px;border-radius:4px;text-decoration:none">Open in Internal Tools</a></p>'
        if link
        else ""
    )
    msg.add_alternative(
        '<!doctype html><html><body style="font-family:sans-serif">'
        f'<h2 style="margin:0 0 8px">{e(title)}</h2><p>{e(body)}</p>'
        f"<table>{rows}</table>{button}</body></html>",
        subtype="html",
    )
    return msg


class EmailDeliveryError(Exception):
    """SMTP failure, described without server credentials."""


class EmailAdapter(Adapter):
    name = "email"
    transactional = False

    def __init__(self, settings: SmtpSettings):
        self._settings = settings

    def __repr__(self) -> str:
        return f"EmailAdapter(host={self._settings.host!r}, port={self._settings.port})"

    def send(self, db: Session | None, message: OutboxRecord) -> None:
        s = self._settings
        msg = render_email(message, sender=s.sender, web_base_url=s.web_base_url)
        try:
            with smtplib.SMTP(s.host, s.port, timeout=s.timeout) as smtp:
                if s.use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                if s.username:
                    smtp.login(s.username, s.password or "")
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            code = getattr(exc, "smtp_code", None)
            detail = f", code {code}" if isinstance(code, int) else ""
            raise EmailDeliveryError(
                f"SMTP delivery failed ({type(exc).__name__}{detail})"
            ) from None
