"""Runs YAML `schedules` (time-driven rules) in the worker as `app_worker`.

Each run is one transaction: the job's query, the outbox inserts (deduplicated per schedule,
entity window, action, channel and adapter) and the job's `on_first_notify` side effects
commit together. Concurrent workers are safe: the second insert of a dedupe_key waits for the
first transaction and then does nothing. Cadence (`every_seconds`) is tracked in memory; it
only decides how often to look, never whether to notify.
"""

import logging
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.platform.workflows.config import Schedule, WorkflowConfig
from app.platform.workflows.engine import enqueue_notification
from app.platform.workflows.jobs import get_job

log = logging.getLogger("workflows.scheduler")


def run_schedule(
    config: WorkflowConfig, schedule: Schedule, session_factory: Callable[[], Session]
) -> int:
    job = get_job(schedule.job)
    if job is None:  # validated at load time
        raise RuntimeError(f"unknown job {schedule.job!r}")
    inserted = 0
    with session_factory() as db, db.begin():
        for hit in job.fn(db, schedule.params):
            new = sum(
                enqueue_notification(
                    db,
                    config,
                    schedule.id,
                    action,
                    hit.event,
                    dedupe_prefix=f"schedule:{schedule.id}:{hit.window_key}:a{i}",
                )
                for i, action in enumerate(schedule.actions)
            )
            if new and hit.on_first_notify is not None:
                hit.on_first_notify(db)
            inserted += new
    if inserted:
        log.info("schedule %s enqueued %d notification(s)", schedule.id, inserted)
    return inserted


class Scheduler:
    def __init__(
        self,
        config: WorkflowConfig,
        session_factory: Callable[[], Session],
        clock: Callable[[], float] = time.monotonic,
    ):
        self._config = config
        self._session_factory = session_factory
        self._clock = clock
        self._last_run: dict[str, float] = {}

    def run_due(self) -> int:
        total = 0
        errors: list[str] = []
        for schedule in self._config.schedules:
            now = self._clock()
            last = self._last_run.get(schedule.id)
            if last is not None and now - last < schedule.every_seconds:
                continue
            self._last_run[schedule.id] = now
            try:
                total += run_schedule(self._config, schedule, self._session_factory)
            except Exception:
                log.exception("schedule %s failed", schedule.id)
                errors.append(schedule.id)
        if errors:
            raise RuntimeError(f"schedules failed: {', '.join(errors)}")
        return total
