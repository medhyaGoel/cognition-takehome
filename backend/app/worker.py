"""Background worker: outbox dispatch, scheduled workflow rules (SLA escalation) and refund
execution. Runs as `app_worker` (WorkerSessionLocal).

Each job is isolated: an exception is logged and the loop continues. SIGTERM/SIGINT stop the
loop after the current job; no job is interrupted halfway (each commits atomically anyway).
"""

import importlib
import logging
import signal
import threading
from collections.abc import Callable

from app.db import WorkerSessionLocal
from app.platform import outbox
from app.platform.workflows.engine import get_config
from app.platform.workflows.scheduler import Scheduler

log = logging.getLogger("worker")

POLL_INTERVAL_SECONDS = 2.0
REFUNDS_EXECUTION_MODULE = "app.apps.refunds.execution"


class _RefundsExecution:
    """Calls app.apps.refunds.execution.execute_pending(), imported lazily so the worker runs
    before the refunds app ships it."""

    def __init__(self) -> None:
        self._warned = False

    def __call__(self) -> None:
        try:
            module = importlib.import_module(REFUNDS_EXECUTION_MODULE)
        except ModuleNotFoundError as exc:
            if exc.name not in (REFUNDS_EXECUTION_MODULE, "app.apps.refunds"):
                raise
            module = None
        execute_pending = getattr(module, "execute_pending", None)
        if execute_pending is None:
            if not self._warned:
                log.warning(
                    "refunds execution not available yet (%s.execute_pending missing); skipping",
                    REFUNDS_EXECUTION_MODULE,
                )
                self._warned = True
            return
        execute_pending()


class Worker:
    def __init__(
        self,
        jobs: list[tuple[str, Callable[[], object]]] | None = None,
        *,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ):
        self.stop_event = threading.Event()
        self.poll_interval = poll_interval
        if jobs is None:
            scheduler = Scheduler(get_config(), WorkerSessionLocal)
            jobs = [
                ("outbox_dispatch", outbox.dispatch_pending),
                ("workflow_schedules", scheduler.run_due),
                ("refunds_execution", _RefundsExecution()),
            ]
        self.jobs = jobs

    def run_once(self) -> None:
        for name, job in self.jobs:
            if self.stop_event.is_set():
                return
            try:
                job()
            except Exception:
                log.exception("worker job %s failed; continuing", name)

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.run_once()
            self.stop_event.wait(self.poll_interval)

    def stop(self, signum: int | None = None, _frame: object = None) -> None:
        if not self.stop_event.is_set():
            log.info("worker stopping (signal %s)", signum)
        self.stop_event.set()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = get_config()  # fail fast on an invalid workflows file
    log.info("worker started (%d rules, %d schedules)", len(config.rules), len(config.schedules))
    worker = Worker()
    worker.install_signal_handlers()
    worker.run()
    log.info("worker stopped")


if __name__ == "__main__":
    main()
