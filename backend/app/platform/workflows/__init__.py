"""Workflow automation (Power Automate equivalent).

Contract used by app services: call `on_event(db, event)` inside the transaction that
produced the event. It returns a `WorkflowOutcome` the caller applies (e.g. required
approvals), and enqueues any notifications to the outbox in the same transaction.

STUB: returns defaults. Replaced by the rules engine (config/workflows.yaml).
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class DomainEvent:
    name: str  # e.g. "refund.created"
    entity_type: str  # e.g. "refund"
    entity_id: str
    actor: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowOutcome:
    required_approvals: int | None = None
    auto_approve: bool = False
    matched_rules: list[str] = field(default_factory=list)


def on_event(db: Session, event: DomainEvent) -> WorkflowOutcome:
    return WorkflowOutcome()
