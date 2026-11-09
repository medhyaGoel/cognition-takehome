import dataclasses
import threading

from sqlalchemy import text

from app.db import WorkerSessionLocal
from app.platform.workflows.engine import get_config
from app.platform.workflows.scheduler import Scheduler, run_schedule


def sla_schedule(**params):
    (schedule,) = get_config().schedules
    if params:
        schedule = dataclasses.replace(schedule, params={**schedule.params, **params})
    return schedule


def run(**params) -> int:
    return run_schedule(get_config(), sla_schedule(**params), WorkerSessionLocal)


def test_overdue_refund_escalates_once_per_window(make_refund, outbox_rows, owner_engine):
    rid = make_refund(age_minutes=61, amount_cents=2_500)
    make_refund(age_minutes=30)  # within SLA
    make_refund(age_minutes=500, status="approved")  # not pending
    assert run() == 3  # in_app + webhook + email
    rows = outbox_rows()
    assert {r["payload"]["adapter"] for r in rows} == {"in_app", "webhook", "email"}
    assert all(r["payload"]["entity_id"] == str(rid) for r in rows)
    assert all(r["dedupe_key"].startswith(f"schedule:refund-approval-sla:{rid}:w1:") for r in rows)
    assert rows[0]["payload"]["title"] == "SLA breached: refund $25.00 USD · US"
    assert rows[0]["payload"]["data"]["reason"] == "customer request"
    assert run() == 0 and len(outbox_rows()) == 3  # same window: deduped
    with owner_engine.connect() as conn:
        escalated = conn.execute(
            text("SELECT escalated_at, version FROM refunds WHERE id = :id"), {"id": rid}
        ).one()
        audit = conn.execute(
            text("SELECT actor, after FROM audit_log WHERE action = 'refund.escalated'")
        ).all()
    assert escalated.escalated_at is not None and escalated.version == 1
    assert audit == [("system:worker", {"sla_minutes": 60, "sla_window": 1})]


def test_dedupe_is_the_guarantee_even_if_escalated_at_is_cleared(make_refund, owner_engine):
    make_refund(age_minutes=61)
    assert run() == 3
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE refunds SET escalated_at = NULL"))
    assert run() == 0


def test_next_window_escalates_again(make_refund, outbox_rows, owner_engine):
    rid = make_refund(age_minutes=61)
    assert run() == 3
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE refunds SET created_at = created_at - interval '60 minutes',"
                " escalated_at = escalated_at - interval '60 minutes' WHERE id = :id"
            ),
            {"id": rid},
        )
    assert run() == 3
    keys = sorted(r["dedupe_key"] for r in outbox_rows())
    assert sum(":w1:" in k for k in keys) == 3 and sum(":w2:" in k for k in keys) == 3


def test_sla_minutes_is_configurable(make_refund):
    make_refund(age_minutes=11)
    assert run() == 0
    assert run(sla_minutes=10) == 3


def test_concurrent_schedulers_notify_once(make_refund, outbox_rows):
    for _ in range(5):
        make_refund(age_minutes=90)
    barrier = threading.Barrier(3)
    results: list[int] = []

    def go():
        barrier.wait()
        results.append(run())

    threads = [threading.Thread(target=go) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 15 and len(outbox_rows()) == 15


def test_scheduler_cadence_and_error_isolation(make_refund):
    now = [0.0]
    scheduler = Scheduler(get_config(), WorkerSessionLocal, clock=lambda: now[0])
    make_refund(age_minutes=61)
    assert scheduler.run_due() == 3
    make_refund(age_minutes=61)
    assert scheduler.run_due() == 0  # not due yet
    now[0] += sla_schedule().every_seconds
    assert scheduler.run_due() == 3
