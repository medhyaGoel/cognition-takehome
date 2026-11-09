import json
import logging

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Notification
from app.platform.outbox import Adapter, OutboxRecord

log = logging.getLogger("notifications")
# httpx logs full request URLs at INFO; webhook URLs are secrets.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

WEBHOOK_TIMEOUT_SECONDS = 5.0


def _text(payload: dict, key: str, default: str = "") -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else default


class LogAdapter(Adapter):
    """Structured log line. Logs ids and the title only (bodies may carry business data)."""

    name = "log"

    def send(self, db: Session | None, message: OutboxRecord) -> None:
        p = message.payload
        record = {
            "outbox_id": message.id,
            "topic": message.topic,
            "channel": p.get("channel"),
            "rule": p.get("rule"),
            "event": p.get("event"),
            "entity_type": p.get("entity_type"),
            "entity_id": p.get("entity_id"),
            "business_unit": p.get("business_unit"),
            "title": p.get("title"),
        }
        log.info("notification %s", json.dumps(record, sort_keys=True, default=str))


class InAppAdapter(Adapter):
    name = "in_app"
    transactional = True

    def send(self, db: Session | None, message: OutboxRecord) -> None:
        if db is None:
            raise RuntimeError("in_app adapter needs the dispatcher's transaction")
        p = message.payload
        business_unit = p.get("business_unit")
        if not isinstance(business_unit, str) or not business_unit:
            # A NULL business_unit is visible to every business unit; refuse (fail closed).
            raise ValueError("in_app notification requires a business_unit")
        stmt = (
            insert(Notification)
            .values(
                outbox_id=message.id,
                business_unit=business_unit,
                recipient_role=p.get("recipient_role"),
                topic=_text(p, "channel", message.topic),
                title=_text(p, "title", message.topic)[:200],
                body=_text(p, "body")[:2000],
                entity_type=p.get("entity_type"),
                entity_id=p.get("entity_id"),
            )
            .on_conflict_do_nothing(index_elements=["outbox_id"])
        )
        db.execute(stmt)


class WebhookDeliveryError(Exception):
    """Error without the webhook URL (it embeds a secret token)."""


class WebhookAdapter(Adapter):
    """Slack/Teams incoming webhook: POST {"text": ...}. Never logs or stores the URL."""

    name = "webhook"

    def __init__(
        self,
        url: str,
        *,
        timeout: float = WEBHOOK_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ):
        self._url = url
        self._timeout = timeout
        self._transport = transport

    def __repr__(self) -> str:
        return "WebhookAdapter(url=<redacted>)"

    def send(self, db: Session | None, message: OutboxRecord) -> None:
        p = message.payload
        title, body = _text(p, "title"), _text(p, "body")
        payload = {"text": f"*{title}*\n{body}" if body else title}
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                response = client.post(
                    self._url, json=payload, headers={"X-Outbox-Message-Id": str(message.id)}
                )
        except httpx.HTTPError as exc:
            raise WebhookDeliveryError(f"webhook transport error ({type(exc).__name__})") from None
        if not 200 <= response.status_code < 300:
            raise WebhookDeliveryError(f"webhook returned HTTP {response.status_code}")
