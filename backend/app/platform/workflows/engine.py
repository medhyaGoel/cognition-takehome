"""Deterministic rule evaluation and notification planning.

`evaluate` is pure: same config + same event -> same outcome. Rules are evaluated in file
order; conditions are ANDed. Combination:
  * required approvals = max(count) over matched `set_required_approvals` (strictest wins);
  * a matched `auto_approve` only applies if no matched rule requires approvals (> 0);
  * final required approvals == 0  <=>  auto-approve.
Conditions never coerce types: a missing field, or a value of a different type, does not match.
"""

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.config import settings
from app.platform import outbox
from app.platform.notifications.formatting import display_data
from app.platform.workflows.config import (
    AutoApprove,
    Condition,
    Notify,
    SetRequiredApprovals,
    WorkflowConfig,
    load_config,
)
from app.platform.workflows.events import DomainEvent

log = logging.getLogger("workflows")

NOTIFICATION_KIND = "notification"


@dataclass(frozen=True)
class Evaluation:
    required_approvals: int | None
    auto_approve: bool
    matched_rules: tuple[str, ...]
    notifications: tuple[tuple[str, Notify], ...]  # (rule id, action), in rule order


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


def condition_matches(cond: Condition, data: Mapping[str, Any]) -> bool:
    if cond.field not in data:
        return False
    actual = data[cond.field]
    match cond.op:
        case "eq":
            return _same(actual, cond.value)
        case "ne":
            return type(actual) is type(cond.value) and actual != cond.value
        case "in":
            return any(_same(actual, v) for v in cond.value)
        case "gt" | "gte" | "lt" | "lte":
            if not _is_int(actual):
                return False
            return {
                "gt": actual > cond.value,
                "gte": actual >= cond.value,
                "lt": actual < cond.value,
                "lte": actual <= cond.value,
            }[cond.op]
    return False


def evaluate(config: WorkflowConfig, event: DomainEvent) -> Evaluation:
    matched: list[str] = []
    counts: list[int] = []
    auto = False
    notifications: list[tuple[str, Notify]] = []
    for rule in config.rules:
        if rule.on != event.name:
            continue
        if not all(condition_matches(c, event.data) for c in rule.conditions):
            continue
        matched.append(rule.id)
        for action in rule.actions:
            if isinstance(action, SetRequiredApprovals):
                counts.append(action.count)
            elif isinstance(action, AutoApprove):
                auto = True
            elif isinstance(action, Notify):
                notifications.append((rule.id, action))
    if counts and max(counts) > 0:
        required: int | None = max(counts)
    elif counts or auto:
        required = 0
    else:
        required = None
    return Evaluation(
        required_approvals=required,
        auto_approve=required == 0,
        matched_rules=tuple(matched),
        notifications=tuple(notifications),
    )


def plan_messages(
    config: WorkflowConfig, source_id: str, action: Notify, event: DomainEvent
) -> list[dict[str, Any]]:
    """One outbox payload per adapter of the action's channel."""
    channel = config.channels[action.channel]
    data = display_data(event.data)  # scalars only, customer emails masked
    business_unit = data.get("business_unit")
    link = config.links.get(event.entity_type)
    base = {
        "kind": NOTIFICATION_KIND,
        "channel": channel.name,
        "rule": source_id,
        "event": event.name,
        "title": action.title.render(data) if action.title else event.name,
        "body": action.template.render(data),
        "recipient_role": channel.recipient_role,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "business_unit": business_unit if isinstance(business_unit, str) else None,
        "link_path": link.replace("{id}", quote(event.entity_id, safe="")) if link else None,
        "data": data,
    }
    payloads = []
    for adapter in channel.adapters:
        if adapter == "email":
            payloads.append(
                {
                    **base,
                    "adapter": adapter,
                    "email_to": list(channel.email_to),
                    "email_subject_prefix": channel.email_subject_prefix,
                }
            )
            continue
        if adapter == "in_app" and base["business_unit"] is None:
            # NULL business_unit would make the notification visible in every business unit.
            log.warning(
                "skipping in_app notification without business_unit",
                extra={"rule": source_id, "channel": channel.name, "event": event.name},
            )
            continue
        payloads.append({**base, "adapter": adapter})
    return payloads


def enqueue_notification(
    db: Session,
    config: WorkflowConfig,
    source_id: str,
    action: Notify,
    event: DomainEvent,
    *,
    dedupe_prefix: str | None = None,
) -> int:
    """Enqueue in the caller's transaction. Returns how many messages were newly inserted."""
    inserted = 0
    for payload in plan_messages(config, source_id, action, event):
        key = (
            f"{dedupe_prefix}:{payload['channel']}:{payload['adapter']}" if dedupe_prefix else None
        )
        if outbox.enqueue(
            db, topic=f"notify.{payload['channel']}", payload=payload, dedupe_key=key
        ):
            inserted += 1
    return inserted


_lock = threading.Lock()
_config: WorkflowConfig | None = None


def get_config() -> WorkflowConfig:
    """The rules loaded once from settings.workflows_path (raises WorkflowConfigError)."""
    global _config
    if _config is None:
        with _lock:
            if _config is None:
                _config = load_config(settings.workflows_path)
    return _config


def set_config(config: WorkflowConfig | None) -> None:
    """Replace (or with None, reset) the loaded rules. Tests and reloads only."""
    global _config
    with _lock:
        _config = config
