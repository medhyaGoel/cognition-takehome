"""Workflow automation (Power Automate equivalent).

Contract used by app services: call `on_event(db, event)` inside the transaction that
produced the event. It returns a `WorkflowOutcome` the caller applies (e.g. required
approvals), and enqueues any notifications to the outbox in the same transaction, so a
rolled-back transaction notifies nobody.

Rules are declarative (config/workflows.yaml, see `config.py`); evaluation is deterministic
(see `engine.py`). Scheduled rules (e.g. SLA escalation) run in the worker (`scheduler.py`).
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.platform.workflows.engine import enqueue_notification, evaluate, get_config
from app.platform.workflows.events import DomainEvent

__all__ = ["DomainEvent", "WorkflowOutcome", "on_event"]


@dataclass
class WorkflowOutcome:
    required_approvals: int | None = None
    auto_approve: bool = False
    matched_rules: list[str] = field(default_factory=list)
    # Channels that received a notification, in rule order (may repeat).
    notified_channels: list[str] = field(default_factory=list)


def on_event(db: Session, event: DomainEvent) -> WorkflowOutcome:
    if not db.in_transaction():
        raise RuntimeError("workflows.on_event must be called inside the caller's transaction")
    config = get_config()
    result = evaluate(config, event)
    for rule_id, action in result.notifications:
        enqueue_notification(db, config, rule_id, action, event)
    return WorkflowOutcome(
        required_approvals=result.required_approvals,
        auto_approve=result.auto_approve,
        matched_rules=list(result.matched_rules),
        notified_channels=[action.channel for _, action in result.notifications],
    )
