"""Notification channel adapters used by the outbox dispatcher.

  log      structured log line (default for messages without an adapter)
  in_app   row in `notifications` (RLS-scoped by business_unit; idempotent on outbox_id)
  webhook  Slack/Teams-compatible incoming-webhook POST to NOTIFY_WEBHOOK_URL
  email    SMTP (SMTP_HOST/PORT/FROM/USE_TLS, optional SMTP_USERNAME/PASSWORD)
webhook and email fall back to `log` when they are not configured.
"""

import os

from app.platform.notifications.adapters import InAppAdapter, LogAdapter, WebhookAdapter
from app.platform.notifications.email import EmailAdapter, SmtpSettings
from app.platform.outbox import Adapter

__all__ = [
    "EmailAdapter",
    "InAppAdapter",
    "LogAdapter",
    "SmtpSettings",
    "WebhookAdapter",
    "default_adapters",
]


def default_adapters() -> dict[str, Adapter]:
    log_adapter = LogAdapter()
    url = os.environ.get("NOTIFY_WEBHOOK_URL") or None
    smtp = SmtpSettings.from_env()
    return {
        "log": log_adapter,
        "in_app": InAppAdapter(),
        "webhook": WebhookAdapter(url) if url else log_adapter,
        "email": EmailAdapter(smtp) if smtp else log_adapter,
    }
