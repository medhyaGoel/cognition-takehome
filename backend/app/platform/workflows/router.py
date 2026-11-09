from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.platform.auth import Principal, Role, require_roles
from app.platform.notifications.store import list_recent
from app.platform.workflows.config import (
    Action,
    AutoApprove,
    Notify,
    SetRequiredApprovals,
    WorkflowConfig,
)
from app.platform.workflows.engine import get_config

router = APIRouter(prefix="/api", tags=["workflows"])

ALL_READ = (Role.VIEWER, Role.REFUND_AGENT, Role.REFUND_APPROVER, Role.FINANCE_ADMIN)

# Fail fast: an invalid workflows file stops the API from starting.
get_config()


class RuleOut(BaseModel):
    id: str
    kind: str  # "event" | "schedule"
    description: str
    on: str | None
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    schedule: dict[str, Any] | None = None


class NotificationOut(BaseModel):
    id: int
    created_at: datetime
    topic: str
    title: str
    body: str
    recipient_role: str | None
    entity_type: str | None
    entity_id: str | None
    business_unit: str | None


def _action(config: WorkflowConfig, action: Action) -> dict[str, Any]:
    if isinstance(action, SetRequiredApprovals):
        return {"type": "set_required_approvals", "count": action.count}
    if isinstance(action, AutoApprove):
        return {"type": "auto_approve"}
    assert isinstance(action, Notify)  # noqa: S101
    return {
        "type": "notify",
        "channel": action.channel,
        "adapters": list(config.channels[action.channel].adapters),
        "title": action.title.source if action.title else None,
        "template": action.template.source,
    }


def describe_rules(config: WorkflowConfig) -> list[RuleOut]:
    out = [
        RuleOut(
            id=r.id,
            kind="event",
            description=r.description,
            on=r.on,
            conditions=[{"field": c.field, "op": c.op, "value": c.value} for c in r.conditions],
            actions=[_action(config, a) for a in r.actions],
        )
        for r in config.rules
    ]
    out += [
        RuleOut(
            id=s.id,
            kind="schedule",
            description=s.description,
            on=None,
            conditions=[],
            actions=[_action(config, a) for a in s.actions],
            schedule={"job": s.job, "every_seconds": s.every_seconds, "params": dict(s.params)},
        )
        for s in config.schedules
    ]
    return out


@router.get("/workflows/rules", response_model=list[RuleOut])
def list_rules(_: Principal = Depends(require_roles(*ALL_READ))) -> list[RuleOut]:
    return describe_rules(get_config())


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(
    limit: int = Query(50, ge=1, le=100),
    _: Principal = Depends(require_roles(Role.FINANCE_ADMIN, Role.REFUND_APPROVER)),
    db: Session = Depends(get_db),
) -> list[NotificationOut]:
    return [
        NotificationOut.model_validate(n, from_attributes=True)
        for n in list_recent(db, limit=limit)
    ]
