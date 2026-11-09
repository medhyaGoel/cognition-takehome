"""Built-in scheduled jobs. The only app-specific Python in the workflow engine lives here;
what to do with each hit (channels, templates) is declared in config/workflows.yaml."""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform import audit
from app.platform.workflows.events import DomainEvent
from app.platform.workflows.jobs import ParamSpec, ScheduledHit, scheduled_job

WORKER_ACTOR = "system:worker"  # == app.db.WORKER_ACTOR (not imported to avoid engine setup)

# Overdue = pending_approval for at least one SLA period. Window n covers ages
# [n*sla, (n+1)*sla); each refund escalates at most once per window. `escalated_at` only
# pre-filters (so a backlog can't starve the batch); the outbox dedupe_key is the guarantee.
_OVERDUE_REFUNDS = text(
    """
    SELECT * FROM (
        SELECT id, payment_id, business_unit, amount_cents, currency, status, requested_by,
               reason,
               created_at, escalated_at,
               floor(extract(epoch FROM now() - created_at) / (:sla * 60))::int AS sla_window,
               floor(extract(epoch FROM now() - created_at) / 60)::int AS age_minutes
        FROM refunds
        WHERE status = 'pending_approval'
          AND created_at <= now() - make_interval(mins => :sla)
    ) r
    WHERE r.escalated_at IS NULL
       OR r.escalated_at < r.created_at + make_interval(mins => :sla * r.sla_window)
    ORDER BY r.created_at, r.id
    LIMIT :limit
    """
)


@scheduled_job(
    "refund_sla_escalation",
    params={
        "sla_minutes": ParamSpec(int, 60, minimum=1, maximum=7 * 24 * 60),
        "batch_size": ParamSpec(int, 200, minimum=1, maximum=1000),
    },
)
def refund_sla_escalation(db: Session, params: Mapping[str, Any]) -> list[ScheduledHit]:
    sla = params["sla_minutes"]
    rows = db.execute(_OVERDUE_REFUNDS, {"sla": sla, "limit": params["batch_size"]}).all()
    hits = []
    for r in rows:
        refund_id = str(r.id)
        data = {
            "refund_id": refund_id,
            "amount_cents": r.amount_cents,
            "currency": r.currency,
            "status": r.status,
            "requested_by": r.requested_by,
            "reason": r.reason,
            "payment_id": str(r.payment_id),
            "business_unit": r.business_unit,
            "age_minutes": r.age_minutes,
            "sla_minutes": sla,
            "sla_window": r.sla_window,
        }
        hits.append(
            ScheduledHit(
                event=DomainEvent(
                    name="refund.sla_breached",
                    entity_type="refund",
                    entity_id=refund_id,
                    actor=WORKER_ACTOR,
                    data=data,
                ),
                window_key=f"{refund_id}:w{r.sla_window}",
                on_first_notify=_mark_escalated(refund_id, r.business_unit, r.sla_window, sla),
            )
        )
    return hits


def _mark_escalated(refund_id: str, business_unit: str, window: int, sla: int):
    def apply(db: Session) -> None:
        # Not a state transition: `version` is untouched so approvers' expected_version holds.
        db.execute(
            text(
                "UPDATE refunds SET escalated_at = now()"
                " WHERE id = :id AND status = 'pending_approval'"
            ),
            {"id": refund_id},
        )
        audit.record(
            db,
            actor=WORKER_ACTOR,
            action="refund.escalated",
            entity_type="refund",
            entity_id=refund_id,
            business_unit=business_unit,
            after={"sla_minutes": sla, "sla_window": window},
        )

    return apply
